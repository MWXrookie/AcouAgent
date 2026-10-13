"""Offline paired reuse evaluation. Never invokes a model or grants physics trust.

Runner exports are measurements, not authenticated evidence. Reports remain
pending independent review, including when all numeric targets are satisfied.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()

def make_plan():
    from app import skill_local_catalog as catalog
    scopes=sorted(catalog.PERIODIC_RECIPES)
    tasks=[]
    for scope in scopes:
        spec=catalog.specification(scope)
        params=dict(spec["parameters"])
        tasks.append(dict(task_id="exact-"+str(len(tasks)+1),kind="exact",
            scope=scope,scenario_type=spec["scene"],parameters=params,
            expected_reference=True,recipe_sha256=spec["code_sha256"],
            prompt="请按以下完整配置仿真，不改变参数："+json.dumps(params,ensure_ascii=False,sort_keys=True)))
    for i,scope in enumerate(scopes[:5]):
        spec=catalog.specification(scope)
        tasks.append(dict(task_id="rephrase-"+str(i+1),kind="rephrase",scope=scope,
            scenario_type=spec["scene"],parameters=spec["parameters"],
            expected_reference=True,recipe_sha256=spec["code_sha256"],
            prompt="执行这个固定声学案例并检查结果。完整配置："+json.dumps(spec["parameters"],ensure_ascii=False,sort_keys=True)))
    for kind in ("outside","missing"):
        for i,scope in enumerate(scopes[:5]):
            spec=catalog.specification(scope);params=dict(spec["parameters"])
            if kind=="outside":params["boundary_condition"]="open"
            else:params.pop("boundary_condition",None)
            tasks.append(dict(task_id=kind+"-"+str(i+1),kind=kind,scope=scope,
                scenario_type=spec["scene"],parameters=params,expected_reference=False,
                recipe_sha256=spec["code_sha256"],
                prompt="请仿真并检查适用条件："+json.dumps(params,ensure_ascii=False,sort_keys=True)))
    plan=dict(version=1,kind="paired_model_reuse_plan",tasks=tasks,repetitions=3,
        arms=["off","on"],run_count=180,
        controls=["same model/version/prompt/system/context/temperature/seed",
                  "frozen skill snapshot and draft graph",
                  "same executor and physical validator; no threshold changes",
                  "randomized arm order; failures and cancellations retained"],
        paid_runs_authorized=False,published_workflow_required=False,
        acceptance_targets=dict(min_physics_rate=0.95,min_success_gain=0.10,
            min_retry_reduction=0.20,min_duration_reduction=0.15,
            min_model_call_reduction=0.15,min_exact_adoption_rate=0.90,
            max_negative_reference_count=0))
    plan["plan_sha256"]=digest(plan)
    return plan

def _number(x):
    return type(x) in (int,float) and math.isfinite(x) and x>=0

def evaluate(plan,records):
    body={k:v for k,v in plan.items() if k!="plan_sha256"}
    if digest(body)!=plan.get("plan_sha256"):raise ValueError("plan_changed")
    tasks={t["task_id"]:t for t in plan["tasks"]}
    expected={(t,r,a) for t in tasks for r in range(plan["repetitions"]) for a in ("off","on")}
    actual={};run_ids=set()
    for row in records:
        key=(row["task_id"],row["repetition"],row["arm"])
        if key not in expected or key in actual:raise ValueError("unexpected_or_duplicate_run")
        if type(row["repetition"]) is not int:raise ValueError("invalid_repetition")
        if not isinstance(row["run_id"],str) or not row["run_id"] or row["run_id"] in run_ids:
            raise ValueError("duplicate_run_id")
        run_ids.add(row["run_id"])
        for field in ("duration_ms","model_calls","retry_count"):
            if not _number(row[field]):raise ValueError("invalid_measurement")
        for field in ("model_calls","retry_count"):
            if type(row[field]) is not int:raise ValueError("invalid_count")
        if row["outcome"] not in ("completed","failed","timeout","cancelled"):raise ValueError("invalid_outcome")
        errors=row["physical_errors"]
        if type(errors) is not list or any(not _number(e) for e in errors):raise ValueError("invalid_physics")
        if type(row["expected_physical_checks"]) is not int or row["expected_physical_checks"]<1:
            raise ValueError("invalid_physics_count")
        if type(row["reference_delivered"]) is not bool:raise ValueError("invalid_reference")
        if row["arm"]=="off" and row["reference_delivered"]:raise ValueError("baseline_contaminated")
        actual[key]=row
    missing=len(expected-set(actual))
    mismatches=[]
    for tid,rep,_ in sorted(expected):
        if (tid,rep,"off") not in actual or (tid,rep,"on") not in actual:continue
        off=actual[tid,rep,"off"];on=actual[tid,rep,"on"]
        if off["controls_sha256"]!=on["controls_sha256"] or off["prompt_sha256"]!=digest(tasks[tid]["prompt"]) or on["prompt_sha256"]!=digest(tasks[tid]["prompt"]):
            mismatches.append([tid,rep])
    def physical(row):
        return (row["outcome"]=="completed" and row["verdict"]=="normal"
            and len(row["physical_errors"])==row["expected_physical_checks"]
            and all(e<0.01 for e in row["physical_errors"]))
    metrics={}
    positive={tid for tid,t in tasks.items() if t["expected_reference"]}
    for arm in ("off","on"):
        all_rows=[r for (tid,_,a),r in actual.items() if a==arm]
        rows=[r for (tid,_,a),r in actual.items() if a==arm and tid in positive]
        denominator=len(positive)*plan["repetitions"]
        metrics[arm]=dict(positive_total=denominator,observed_positive=len(rows),
            physics_pass_count=sum(physical(r) for r in rows),
            physics_pass_rate=sum(physical(r) for r in rows)/denominator,
            mean_duration_ms=mean(r["duration_ms"] for r in rows) if rows else None,
            mean_model_calls=mean(r["model_calls"] for r in rows) if rows else None,
            mean_retries=mean(r["retry_count"] for r in rows) if rows else None,
            failure_count=sum(r["outcome"]!="completed" for r in all_rows))
    def reduction(field):
        x,y=metrics["off"][field],metrics["on"][field]
        return None if x is None or y is None or x==0 else (x-y)/x
    adopted=sum(r["reference_delivered"] and r.get("generated_code_sha256")==tasks[tid]["recipe_sha256"]
        and r.get("executed_code_sha256")==r.get("generated_code_sha256")
        for (tid,_,a),r in actual.items() if a=="on" and tid in positive)
    negatives=sum(r["reference_delivered"] for (tid,_,a),r in actual.items()
        if a=="on" and tid not in positive)
    return dict(kind="paired_reuse_measurement_report",plan_sha256=plan["plan_sha256"],
        complete=missing==0,missing_runs=missing,control_mismatches=sorted(set(map(tuple,mismatches))),
        metrics=metrics,success_gain=metrics["on"]["physics_pass_rate"]-metrics["off"]["physics_pass_rate"],
        duration_reduction=reduction("mean_duration_ms"),retry_reduction=reduction("mean_retries"),
        model_call_reduction=reduction("mean_model_calls"),
        exact_adoption_rate=adopted/(len(positive)*plan["repetitions"]),
        negative_reference_count=negatives,
        evidence_authenticated=False,formal_acceptance=False,
        review_required="Recheck runner provenance, raw solver output and frozen controls; hashes alone do not authenticate adoption.")

def load(path):
    raw=Path(path).read_bytes()
    if len(raw)>2_000_000:raise ValueError("input_budget")
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError("duplicate_json_key")
            result[k]=v
        return result
    return json.loads(raw.decode("utf-8"),object_pairs_hook=unique,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite_json")))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--plan",action="store_true")
    parser.add_argument("--plan-file");parser.add_argument("--records")
    args=parser.parse_args()
    result=make_plan() if args.plan else evaluate(load(args.plan_file),load(args.records))
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False))
if __name__=="__main__":main()
