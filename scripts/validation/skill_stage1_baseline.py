"""Stage-one baseline freezing. Offline only: never runs a model, solver or database mutation."""
import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest()

def seal(value):
    result=copy.deepcopy(value)
    result["sha256"]=digest(result)
    return result

def verify_seal(value):
    if type(value) is not dict or value.get("sha256")!=digest({k:v for k,v in value.items() if k!="sha256"}):
        raise ValueError("snapshot_changed")

def make_tasks(specs):
    periodic=sorted((s for s in specs if s["scope"].startswith("managed-local-periodic-")),key=lambda s:s["scope"])
    if len(periodic)!=15: raise ValueError("periodic_count")
    tasks=[]
    def task(spec,kind,i):
        params=copy.deepcopy(spec["parameters"])
        if kind=="outside": params["boundary_condition"]="open"
        if kind=="missing": params.pop("boundary_condition")
        texts={"exact":"请按以下完整配置执行二维声学仿真，不改变参数：",
            "rephrase":"请模拟这组二维声场的演化并检查结果，保持所列配置：",
            "outside":"请按以下开放边界配置仿真，不要改成周期边界：",
            "missing":"请处理以下仿真请求；未给出的边界条件请先向我确认，不要猜测："}
        positive=kind in ("exact","rephrase")
        metrics=[f"{category}-{i}-{metric}" for category in ("probe","field")
            for i in range(3) for metric in ("maximum","l2")]
        return dict(task_id=f"{kind}-{i:02d}",kind=kind,scope=spec["scope"],
            scenario_type=spec["scene"],parameters=params,parameters_sha256=digest(params),
            expected_reference=positive,
            expected_behavior="eligible_fixed_reference" if positive else
                "no_fixed_reference" if kind=="outside" else "clarify_missing_boundary",
            recipe_sha256=spec["code_sha256"],validator_sha256=spec["validator_sha256"],
            physical_contract=dict(applicable=positive,required_metrics=metrics if positive else [],
                validator="periodic-eigenmode.v1",tolerance_exclusive=0.01,
                complete_probe_samples=True,complete_three_64x64_fields=True,
                no_deleted_failed_samples=True),
            prompt=texts[kind]+json.dumps(params,ensure_ascii=False,sort_keys=True))
    for i,s in enumerate(periodic,1): tasks.append(task(s,"exact",i))
    # Choose five structures across all three media instead of five air-only variants.
    chosen=[next(s for s in periodic if f"-{medium}-{mode}." in s["scope"])
        for medium,mode in (("water","axial"),("air","beating"),
            ("soft_tissue","crossed"),("water","harmonic"),("soft_tissue","oblique"))]
    for kind in ("rephrase","outside","missing"):
        for i,s in enumerate(chosen,1): tasks.append(task(s,kind,i))
    return seal(dict(version=1,stage=1,kind="frozen_acceptance_tasks",tasks=tasks,
        task_count=30,independent_positive_configurations=15,positive_tasks=20,
        negative_tasks=10,future_ab_repetitions=3,future_ab_arms=["off","on"],
        future_ab_runs=180,executed_ab_runs=0,
        cost_authorization="not exercised in stage one",
        formal_product_acceptance=False))

def build(specs,observed,runtime,workflows,sources,head,git_status,observed_at):
    if len(specs)!=17 or len({s["scope"] for s in specs})!=17:
        raise ValueError("catalog_count")
    rows=observed["items"]
    if len(rows)!=17 or len({r["scope"] for r in rows})!=17:
        raise ValueError("inventory_count")
    if observed["learning_enabled"] is not False or observed["schema"]!=list(range(1,18)):
        raise ValueError("schema_or_learning_changed")
    inventory=[]
    for spec in sorted(specs,key=lambda s:s["scope"]):
        row=next((r for r in rows if r["scope"]==spec["scope"]),None)
        if row is None: raise ValueError("scope_missing")
        if (row["status"]!="active" or row["quality_level"]!="Q4" or row["archived"]!=0
            or row["latest_source_rechecked"] is not True or row["numeric"].get("numeric_pass") is not True):
            raise ValueError("skill_not_current")
        if (row["parameters"]!=spec["parameters"] or row["code_sha256"]!=spec["code_sha256"]
            or row["validator_sha256"]!=spec["validator_sha256"]):
            raise ValueError("catalog_drift")
        for field in ("skill_id","source_run_id","capsule_sha256"):
            if not row[field]: raise ValueError("evidence_missing")
        inventory.append(dict(row,purpose=spec["state_text"],recipe_path="scripts/validation/recipes/"+spec["recipe"],
            applicable_boundary="exact parameters only",validator_pins=spec["validator_pins"]))
    if {w["role"] for w in workflows}!={"draft","published"}: raise ValueError("workflow_roles")
    if any(w["node_count"]<1 or w["edge_count"]<1 for w in workflows): raise ValueError("empty_graph")
    if set(runtime)!={"gateway","executor","flags","deployed_source_sha256"}: raise ValueError("runtime_shape")
    if runtime["flags"].get("SKILL_LEARNING_ENABLED")!="false": raise ValueError("learning_enabled")
    for path,sha in runtime["deployed_source_sha256"].items():
        if sources.get(path)!=sha: raise ValueError("deployed_source_drift:"+path)
    plan=make_tasks(specs)
    baseline=seal(dict(version=1,kind="stage1_frozen_baseline",observed_at=observed_at,
        git_head=head,git_status=git_status,source_sha256=sources,inventory=inventory,
        runtime=runtime,workflows=workflows,task_plan_sha256=plan["sha256"],
        inventory_count=17,stage1_only=True,model_adoption_authenticated=False,
        physical_evidence="existing solver outputs rechecked, not new solver runs",
        production_mutations=0,learning_enabled=False,formal_product_acceptance=False))
    return baseline,plan

def verify_tasks(plan):
    verify_seal(plan)
    tasks=plan["tasks"]
    if len(tasks)!=30 or Counter(t["kind"] for t in tasks)!=dict(exact=15,rephrase=5,outside=5,missing=5):
        raise ValueError("task_counts")
    if len({t["task_id"] for t in tasks})!=30: raise ValueError("duplicate_task")
    for t in tasks:
        if digest(t["parameters"])!=t["parameters_sha256"]: raise ValueError("parameters_changed")
        positive=t["kind"] in ("exact","rephrase")
        if t["expected_reference"] is not positive: raise ValueError("expectation_changed")
        pc=t["physical_contract"]
        if pc["applicable"] is not positive or pc["tolerance_exclusive"]!=0.01:
            raise ValueError("physical_contract_changed")
        if positive and (len(pc["required_metrics"])!=12 or len(set(pc["required_metrics"]))!=12):
            raise ValueError("checks_missing")
    return True

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory",type=Path)
    args=p.parse_args()
    baseline=json.loads((args.directory/"baseline.json").read_text())
    plan=json.loads((args.directory/"tasks.json").read_text())
    verify_seal(baseline);verify_tasks(plan)
    if baseline["task_plan_sha256"]!=plan["sha256"]: raise ValueError("cross_binding_changed")
    for path,sha in baseline["source_sha256"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha: raise ValueError("source_changed:"+path)
    print(json.dumps(dict(stage1_artifact_integrity=True,skills=17,tasks=30,
        formal_product_acceptance=False,executed_ab_runs=0)))
if __name__=="__main__": main()
