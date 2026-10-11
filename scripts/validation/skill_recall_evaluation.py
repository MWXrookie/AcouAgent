"""Bounded readonly annotated-query evaluation; never certifies dataset truth."""
import argparse
import hashlib
import json
import math
import re
import time
import sqlite3
import os
import stat
from contextlib import ExitStack
from app import skill_retrieval
from scripts.validation.host_forward_signature import unique,reject,canonical

def evaluate(document,search=None):
    if type(document) is not str or len(document.encode())>200000:raise ValueError("dataset_budget")
    dataset=json.loads(document,object_pairs_hook=unique,parse_constant=reject)
    if type(dataset) is not dict or set(dataset)!={"version","annotation_status","queries"}:
        raise ValueError("explicit_annotated_dataset")
    if dataset["version"]!="skill-recall.v1" or dataset["annotation_status"]!="operator_declared":
        raise ValueError("dataset_contract")
    queries=dataset["queries"]
    if type(queries) is not list or not 30<=len(queries)<=100:raise ValueError("query_count_30_to_100")
    checked=[];ids=set();identities=set()
    for query in queries:
        if type(query) is not dict or set(query)!={"id","scenario_type","parameters","relevant_skill_ids"}:
            raise ValueError("query_fields")
        identifier=query["id"]
        if type(identifier) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,64}",identifier) is None or identifier in ids:
            raise ValueError("unique_query_id")
        if type(query["parameters"]) is not dict:raise ValueError("parameters_object")
        params=skill_retrieval._request(query["scenario_type"],canonical(query["parameters"]).decode(),3)
        identity=canonical({"scenario_type":query["scenario_type"],"parameters":params})
        if identity in identities:raise ValueError("duplicate_physical_query")
        relevant=query["relevant_skill_ids"]
        if type(relevant) is not list or len(relevant)>3 or any(
            type(v) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}",v) is None for v in relevant) or len(relevant)!=len(set(relevant)):
            raise ValueError("bounded_relevance_labels")
        ids.add(identifier);identities.add(identity);checked.append((identifier,query["scenario_type"],params,set(relevant)))
    if not any(v[3] for v in checked) or not any(not v[3] for v in checked):
        raise ValueError("positive_and_negative_queries_required")
    # A callback is a test/evaluation adapter, not proof of the configured DB.
    if search is not None:
        result=_score(document,checked,search)
        return dict(result,source_kind="injected_search",store_consistency="not_checked")
    from app import config,skill_library_service
    with ExitStack() as stack:
        watcher=None;before=None;identity=None
        state="disabled"
        if config.SKILL_LIBRARY_ENABLED is True and config.SKILL_RETRIEVAL_ENABLED is True:
            state="unavailable"
            try:
                monitored_store=skill_library_service.STORE
                path=monitored_store.path.resolve()
                # No BEGIN: a retained read transaction would hide later commits
                # from this connection's data_version. Queries keep their usual
                # independent readonly snapshots and current-policy checks.
                watcher=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=0.25)
                stack.callback(watcher.close)
                watcher.execute("PRAGMA query_only=ON")
                before=watcher.execute("PRAGMA data_version").fetchone()[0]
                stat=path.stat();identity=(stat.st_dev,stat.st_ino)
                state="unchanged"
            except (sqlite3.Error,OSError,ValueError):
                watcher=None
        result=_score(document,checked,skill_retrieval.search)
        if watcher is not None:
            try:
                after=watcher.execute("PRAGMA data_version").fetchone()[0]
                stat=path.stat()
                if (before!=after or identity!=(stat.st_dev,stat.st_ino)
                        or skill_library_service.STORE is not monitored_store
                        or skill_library_service.STORE.path.resolve()!=path):
                    state="changed"
            except (sqlite3.Error,OSError):
                state="unavailable"
        # A mixed-state benchmark keeps every row for diagnosis, but cannot
        # pass targets, even if easy queries make its aggregate score look good.
        result["operational"] &= state=="unchanged"
        result["metric_targets_met"] &= state=="unchanged"
        return dict(result,source_kind="configured_readonly_search",store_consistency=state)

def _score(document,checked,search):
    rows=[];latencies=[];positive=hits=wrong=0;operational=True
    for identifier,scene,params,relevant in checked:
        started=time.monotonic()
        result=search(scene,canonical(params).decode(),3)
        elapsed=(time.monotonic()-started)*1000
        latencies.append(elapsed)
        skills=result.get("skills") if type(result) is dict else None
        if type(skills) is not list or len(skills)>3 or any(type(v) is not dict or type(v.get("skill_id")) is not str for v in skills):
            raise ValueError("retrieval_contract")
        returned=[v["skill_id"] for v in skills]
        if len(returned)!=len(set(returned)):raise ValueError("duplicate_retrieval")
        matched=bool(set(returned)&relevant)
        unexpected=bool(set(returned)-relevant)
        positive+=bool(relevant);hits+=bool(relevant) and matched;wrong+=unexpected
        operational &= result.get("reason") in {"fixed_profile_metadata_only","no_applicable_skill","provenance_authentication_unavailable"}
        rows.append(dict(id=identifier,returned_skill_ids=returned,hit=matched,
            unexpected_result=unexpected,latency_ms=round(elapsed,3),reason=result.get("reason")))
    recall=hits/positive;false_rate=wrong/len(checked)
    p95=sorted(latencies)[math.ceil(0.95*len(latencies))-1]
    return dict(dataset_sha256=hashlib.sha256(document.encode()).hexdigest(),query_count=len(checked),
        positive_query_count=positive,top3_hit_rate=recall,false_result_query_rate=false_rate,
        latency_p95_ms=round(p95,3),operational=operational,
        metric_targets_met=operational and recall>=0.85 and false_rate<=0.05 and p95<2000,
        annotation_truth_authenticated=False,actual_use_authenticated=False,overall_contract_passed=False,
        rows=rows)

def read_dataset(path):
    """Read one bounded regular-file descriptor; never wait for FIFO input."""
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):raise ValueError("dataset_regular_file_required")
        if before.st_size>200000:raise ValueError("dataset_budget")
        raw=os.read(fd,200001)
        os.lseek(fd,0,os.SEEK_SET)
        repeated=os.read(fd,200001)
        after=os.fstat(fd)
        if len(raw)>200000 or len(repeated)>200000:raise ValueError("dataset_budget")
        if (raw!=repeated or len(raw)!=before.st_size
                or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                !=(after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ValueError("dataset_changed_during_read")
        return raw.decode("utf-8")
    finally:
        os.close(fd)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",required=True)
    args=parser.parse_args()
    print(json.dumps(evaluate(read_dataset(args.dataset)),ensure_ascii=False,allow_nan=False))
if __name__=="__main__":main()
