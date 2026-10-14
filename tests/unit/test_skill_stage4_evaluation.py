import copy
import pytest

from scripts.validation.skill_reuse_evaluation import digest,evaluate,make_plan
from scripts.validation.skill_reuse_live_batch import schedule


def frozen_tasks():
    tasks=[]
    for kind,count,positive in (("exact",15,True),("rephrase",5,True),
                                ("outside",5,False),("missing",5,False)):
        for number in range(count):
            task_id=f"{kind}-{number+1:02d}"
            tasks.append(dict(task_id=task_id,kind=kind,scope="scope-"+task_id,
                scenario_type="initial_pressure_2d",parameters={"case":task_id},
                expected_reference=positive,recipe_sha256=("a" if positive else "b")*64,
                prompt="prompt "+task_id,physical_contract=dict(
                    required_metrics=["metric"] if positive else [])))
    body=dict(version=1,tasks=tasks)
    return dict(body,sha256=digest(body))


def controls():
    snapshot={"git_head":"a"*40,"workflow":"b"*64,"model":"deepseek-v4-flash"}
    return {"snapshot":snapshot,"controls_sha256":digest(snapshot)}


def records(plan):
    rows=[]
    for task in plan["tasks"]:
        for repetition in range(3):
            for arm in ("off","on"):
                positive=task["expected_reference"]
                rows.append(dict(task_id=task["task_id"],repetition=repetition,arm=arm,
                    run_id=f"{task['task_id']}-{repetition}-{arm}",
                    duration_ms=70 if arm=="on" else 100,
                    model_calls=1 if arm=="on" else 2,
                    retry_count=0 if arm=="on" else 1,
                    outcome="completed",verdict="normal" if positive else "abnormal",
                    physical_errors=[0.001] if positive else [0.0],
                    expected_physical_checks=1,
                    reference_delivered=arm=="on" and positive,
                    generated_code_sha256=task["recipe_sha256"] if arm=="on" and positive else "c"*64,
                    executed_code_sha256=task["recipe_sha256"] if arm=="on" and positive else "c"*64,
                    controls_sha256=plan["controls_sha256"],
                    prompt_sha256=digest(task["prompt"])))
    return rows


def test_frozen_authorized_plan_and_deterministic_interleaved_schedule():
    plan=make_plan(frozen_tasks(),controls())
    assert plan["run_count"]==180 and plan["paid_runs_authorized"]
    order=schedule(plan)
    assert len(order)==180 and order==schedule(plan)
    for index in range(0,len(order),2):
        left,right=order[index:index+2]
        assert left[:2]==right[:2] and {left[2],right[2]}=={"on","off"}


def test_complete_measurement_meets_targets_but_still_requires_review():
    plan=make_plan(frozen_tasks(),controls())
    got=evaluate(plan,records(plan))
    assert got["complete"] and got["targets_met"]
    assert got["negative_reference_count"]==0
    assert got["model_call_reduction"]==.5 and got["duration_reduction"]==.3
    assert not got["evidence_authenticated"] and not got["formal_acceptance"]


def test_missing_contaminated_or_control_drift_cannot_pass():
    plan=make_plan(frozen_tasks(),controls());rows=records(plan)
    assert not evaluate(plan,rows[:-1])["targets_met"]
    contaminated=copy.deepcopy(rows)
    next(row for row in contaminated if row["arm"]=="off")["reference_delivered"]=True
    with pytest.raises(ValueError,match="baseline_contaminated"):
        evaluate(plan,contaminated)
    drift=copy.deepcopy(rows)
    drift[0]["controls_sha256"]="f"*64
    assert not evaluate(plan,drift)["targets_met"]


@pytest.mark.parametrize("fault",["digest","count","duplicate","controls"])
def test_changed_freeze_or_controls_are_rejected(fault):
    frozen=frozen_tasks();control=controls()
    if fault=="digest":frozen["tasks"][0]["prompt"]="changed"
    if fault=="count":
        frozen["tasks"].pop();body={k:v for k,v in frozen.items() if k!="sha256"};frozen["sha256"]=digest(body)
    if fault=="duplicate":
        frozen["tasks"][1]["task_id"]=frozen["tasks"][0]["task_id"]
        body={k:v for k,v in frozen.items() if k!="sha256"};frozen["sha256"]=digest(body)
    if fault=="controls":control["snapshot"]["model"]="changed"
    with pytest.raises(ValueError):make_plan(frozen,control)
