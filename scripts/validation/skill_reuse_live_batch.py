"""Resumable live runner for the frozen stage-four paired evaluation.

The runner never changes a workflow, feature flag, skill or threshold.  It
sends the request-scoped ``skill_reuse_mode`` control to an already published
workflow, keeps every failed/timeout record, and writes one compact record plus
a gzip SSE trace for independent review.  It never prints the API key.
"""
import argparse
import gzip
import hashlib
import json
import os
import random
import time
import uuid
from pathlib import Path

import httpx

from app import execution
from scripts.validation.periodic_eigenmodes import validate as validate_periodic
from scripts.validation.skill_reuse_evaluation import digest, load

MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_TRACE_BYTES = 24 * 1024 * 1024
RUNNERS = ("5655556822808", "1786095555004")
ANALYZERS = ("1786091111002", "1786095555006")
CODE_OUTPUTS = ("daily_main_code", "daily_retry_code")
READERS = ("local_basic_main_reader", "local_basic_retry_reader")
PREFIX = "\n\nValidated local fixed-profile reference (parameters must remain unchanged):\n"


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schedule(plan):
    units=[(task["task_id"],rep) for task in plan["tasks"]
           for rep in range(plan["repetitions"])]
    rng=random.Random(plan["plan_sha256"])
    rng.shuffle(units)
    result=[]
    for task_id,rep in units:
        arms=["off","on"]
        rng.shuffle(arms)
        result.extend((task_id,rep,arm) for arm in arms)
    return result


def _frame(lines):
    data=[line[5:].lstrip() for line in lines if line.startswith("data:")]
    if not data:return None
    value=json.loads("\n".join(data))
    if type(value) is not dict:raise ValueError("invalid_sse_event")
    return value


def _one(value):
    if type(value) is list and len(value)==1:value=value[0]
    return value if type(value) is dict else None


def _outputs(nodes,node_ids):
    for node_id in reversed(node_ids):
        value=nodes.get(node_id)
        if value is not None:return value.get("outputs") or {}
    return {}


def _physical(task,runner):
    count=len(task.get("physical_contract",{}).get("required_metrics") or [])
    if not task["expected_reference"]:
        return [0.0],1,False
    if count<1:return [1.0],1,False
    try:
        stdout=runner["stdout"]
        checked=validate_periodic(json.loads(stdout))
        errors=checked.get("relative_errors")
        if (type(errors) is not list or len(errors)!=count
                or any(type(value) not in (int,float) for value in errors)):
            raise ValueError("physical_error_shape")
        return errors,count,checked.get("numeric_pass") is True
    except (ValueError,TypeError,KeyError,IndexError,json.JSONDecodeError):
        return [1.0]*count,count,False


def compact_record(task,repetition,arm,controls_sha256,events,wall_ms,
                   trace_sha256,transport_error=None):
    nodes={}
    workflow=None
    workflow_id=None
    task_id=None
    for event in events:
        data=event.get("data") or {}
        task_id=event.get("task_id") or task_id
        workflow_id=event.get("workflow_run_id") or workflow_id
        if event.get("event")=="workflow_started":
            workflow_id=data.get("id") or workflow_id
        elif event.get("event")=="node_finished" and data.get("node_id"):
            nodes[data["node_id"]]=data
        elif event.get("event")=="workflow_finished":workflow=data
    runner=_one(_outputs(nodes,RUNNERS).get("json")) or _outputs(nodes,RUNNERS)
    analysis=_one(_outputs(nodes,ANALYZERS).get("json")) or _outputs(nodes,ANALYZERS)
    errors,expected_checks,numeric_pass=_physical(task,runner)
    model_calls=sum(1 for node in nodes.values()
                    if node.get("node_type")=="llm" and node.get("status")=="succeeded")
    attempts=runner.get("total_attempts",0)
    retries=max(attempts-1,0) if type(attempts) is int else 0
    reference=False
    for reader_id in READERS:
        reader=nodes.get(reader_id)
        if not reader:continue
        inputs=reader.get("inputs") or {};outputs=reader.get("outputs") or {}
        original=inputs.get("original_context");delivered=outputs.get("text")
        if type(delivered) is list and len(delivered)==1:delivered=delivered[0]
        if (type(original) is str and type(delivered) is str
                and delivered.startswith(original+PREFIX) and delivered!=original):
            reference=True
    generated=_outputs(nodes,CODE_OUTPUTS).get("output")
    executed=runner.get("final_code")
    generated=execution._clean_code(generated) if type(generated) is str else ""
    executed=execution._clean_code(executed) if type(executed) is str else ""
    status=workflow.get("status") if workflow else None
    if transport_error=="timeout":outcome="timeout"
    elif status=="succeeded":outcome="completed"
    elif task_id and workflow is None:outcome="cancelled"
    else:outcome="failed"
    verdict=analysis.get("verdict") if type(analysis) is dict else None
    if verdict not in ("normal","abnormal"):verdict="abnormal"
    elapsed=(workflow or {}).get("elapsed_time")
    measured_ms=float(elapsed)*1000 if type(elapsed) in (int,float) else wall_ms
    return dict(task_id=task["task_id"],repetition=repetition,arm=arm,
        run_id=workflow_id or "transport-"+str(uuid.uuid4()),
        workflow_run_id=workflow_id,dify_task_id=task_id,outcome=outcome,
        duration_ms=round(measured_ms,3),
        wall_duration_ms=round(wall_ms,3),model_calls=model_calls,retry_count=retries,
        verdict=verdict,physical_errors=errors,
        expected_physical_checks=expected_checks,independent_numeric_pass=numeric_pass,
        reference_delivered=reference,
        generated_code_sha256=_sha(generated) if generated else None,
        executed_code_sha256=_sha(executed) if executed else None,
        controls_sha256=controls_sha256,prompt_sha256=digest(task["prompt"]),
        trace_sha256=trace_sha256,trace_authority="raw_dify_sse_gzip",
        transport_error=transport_error,total_tokens=(workflow or {}).get("total_tokens"))


def run_one(client,base_url,api_key,task,repetition,arm,controls_sha256,trace_path):
    headers={"Authorization":"Bearer "+api_key}
    payload={"inputs":{"query":task["prompt"],"skill_reuse_mode":arm},
             "user":"stage4-paired-"+task["task_id"]+"-"+str(repetition)+"-"+arm,
             "response_mode":"streaming"}
    events=[];lines=[];trace_bytes=0;hasher=hashlib.sha256();task_id=None
    started=time.monotonic();transport_error=None
    try:
        with client.stream("POST",base_url.rstrip("/")+"/workflows/run",
                           headers=headers,json=payload) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type",""):
                raise ValueError("invalid_stream_content_type")
            with gzip.open(trace_path,"wt",encoding="utf-8",compresslevel=6) as raw:
                for line in response.iter_lines():
                    encoded=(line+"\n").encode("utf-8")
                    trace_bytes+=len(encoded)
                    if trace_bytes>MAX_TRACE_BYTES:raise ValueError("trace_size_limit")
                    hasher.update(encoded);raw.write(line+"\n")
                    if line:
                        if len(encoded)>MAX_EVENT_BYTES:raise ValueError("event_line_limit")
                        lines.append(line);continue
                    value=_frame(lines);lines=[]
                    if value is None:continue
                    events.append(value);task_id=value.get("task_id") or task_id
        if lines:
            value=_frame(lines)
            if value is not None:events.append(value)
    except httpx.TimeoutException:transport_error="timeout"
    except (httpx.HTTPError,ValueError,TypeError,UnicodeError,json.JSONDecodeError) as exc:
        transport_error=type(exc).__name__
    finally:
        finished=any(event.get("event")=="workflow_finished" for event in events)
        if task_id and not finished:
            try:
                client.post(base_url.rstrip("/")+"/workflows/tasks/"+task_id+"/stop",
                    headers=headers,json={"user":payload["user"]},timeout=10).raise_for_status()
            except httpx.HTTPError:pass
    return compact_record(task,repetition,arm,controls_sha256,events,
        (time.monotonic()-started)*1000,hasher.hexdigest(),transport_error)


def existing(path):
    rows=[]
    if not path.exists():return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():rows.append(json.loads(line))
    keys=[(row["task_id"],row["repetition"],row["arm"]) for row in rows]
    if len(keys)!=len(set(keys)):raise ValueError("duplicate_existing_record")
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-file",required=True)
    parser.add_argument("--output-dir",required=True)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--task-id",action="append")
    parser.add_argument("--arm",choices=("on","off"))
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args()
    plan=load(args.plan_file)
    body={key:value for key,value in plan.items() if key!="plan_sha256"}
    if digest(body)!=plan.get("plan_sha256") or plan.get("run_count")!=180:
        raise ValueError("plan_changed")
    if not plan.get("paid_runs_authorized") or not plan.get("published_workflow_required"):
        raise ValueError("live_runs_not_authorized")
    controls=plan.get("controls_sha256")
    if type(controls) is not str or len(controls)!=64:raise ValueError("controls_required")
    tasks={task["task_id"]:task for task in plan["tasks"]}
    order=schedule(plan)
    out=Path(args.output_dir);traces=out/"traces"
    out.mkdir(parents=True,exist_ok=True);traces.mkdir(exist_ok=True)
    records_path=out/"records.jsonl";rows=existing(records_path)
    done={(row["task_id"],row["repetition"],row["arm"]) for row in rows}
    pending=[key for key in order if key not in done]
    if args.task_id:
        unknown=set(args.task_id)-set(tasks)
        if unknown:raise ValueError("unknown_task_id")
        pending=[key for key in pending if key[0] in set(args.task_id)]
    if args.arm:pending=[key for key in pending if key[2]==args.arm]
    if args.limit is not None:pending=pending[:args.limit]
    if args.dry_run:
        print(json.dumps(dict(total=len(order),complete=len(done),pending=len(pending),
            schedule=pending),ensure_ascii=False));return
    key=os.environ.get("DIFY_API_KEY")
    if not key:raise ValueError("DIFY_API_KEY_required")
    base=os.environ.get("DIFY_API_BASE_URL","http://nginx/v1")
    timeout=httpx.Timeout(connect=10,read=300,write=30,pool=10)
    with httpx.Client(timeout=timeout,follow_redirects=False) as client,records_path.open("a",encoding="utf-8") as writer:
        for sequence,(task_id,repetition,arm) in enumerate(pending,start=len(done)+1):
            trace=traces/(f"{sequence:03d}-{task_id}-r{repetition}-{arm}.sse.gz")
            row=run_one(client,base,key,tasks[task_id],repetition,arm,controls,trace)
            writer.write(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n")
            writer.flush();os.fsync(writer.fileno())
            print(json.dumps({"sequence":sequence,"task":task_id,"repetition":repetition,
                "arm":arm,"outcome":row["outcome"],"workflow_run_id":row["workflow_run_id"]},
                ensure_ascii=False),flush=True)


if __name__=="__main__":main()
