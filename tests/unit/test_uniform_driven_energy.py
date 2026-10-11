import ast
import hashlib
import json
import pytest
from scripts.validation.driven_energy_experiment import recipe,assess_record,assess_pair,SOURCE_SEMANTICS_SHA256


def evidence(cfl):
    dt=cfl*.001/1500.;steps=5 if cfl==.1 else 10
    times=[n*dt for n in range(steps+1)]
    return dict(pressure_pa=[[[1.+1e6*t]*32 for _ in range(32)] for t in times],
        velocity_m_s=[[[[0.]*32 for _ in range(32)] for _ in range(2)] for t in times],
        times_s=times,dt_s=dt,source_semantics_sha256=SOURCE_SEMANTICS_SHA256)


def checked(raw,cfl=.1,code_sha256=None):
    return assess_record(code_sha256 or hashlib.sha256(recipe(cfl).encode()).hexdigest(),
                         json.dumps(raw),cfl)


@pytest.mark.parametrize("cfl",[.1,.05])
def test_manufactured_uniform_forced_solution_and_recipe_syntax(cfl):
    ast.parse(recipe(cfl))
    result=checked(evidence(cfl),cfl)
    assert result["numeric_pass"] and result["quality_level"]=="Q2"
    assert result["trajectory_relative_error"]==0.
    assert result["energy_balance"]["relative_error"]<1e-12
    assert not result["trusted_run"] and not result["runtime_approved"]


@pytest.mark.parametrize("change",[
    lambda r:r.update(source_semantics_sha256="0"*64),
    lambda r:r.update(dt_s=True),
    lambda r:r["times_s"].__setitem__(0,1e-9),
    lambda r:r["times_s"].__setitem__(1,True),
    lambda r:r["pressure_pa"].pop(),
    lambda r:r["pressure_pa"][2][0].__setitem__(0,2.),
    lambda r:r["pressure_pa"][0][0].__setitem__(0,True),
    lambda r:r["velocity_m_s"][2][0][0].__setitem__(0,.001),
    lambda r:r.update(source_work_j=1.),
])
def test_changed_source_mapping_sampling_fields_and_self_report_refuse(change):
    raw=evidence(.1);change(raw)
    assert not checked(raw)["numeric_pass"]


def test_recipe_change_and_unsupported_resolution_refuse():
    assert not checked(evidence(.1),code_sha256="0"*64)["numeric_pass"]
    for cfl in (True,1,.2,".1"):
        with pytest.raises(ValueError):recipe(cfl)


def test_nan_duplicate_keys_and_output_budget_refuse():
    digest=hashlib.sha256(recipe(.1).encode()).hexdigest()
    for stdout in ('{"pressure_pa":NaN}', '{"a":1,"a":2}', " "*1000001):
        assert not assess_record(digest,stdout,.1)["numeric_pass"]


def pair_record(cfl,scale=1.):
    raw=evidence(cfl)
    if scale!=1.:
        raw["pressure_pa"]=[[[v*scale for v in row] for row in frame] for frame in raw["pressure_pa"]]
    return dict(cfl=cfl,code_sha256=hashlib.sha256(recipe(cfl).encode()).hexdigest(),
        stdout=json.dumps(raw),stderr="",exit_code=0,timed_out=False)


def test_complete_pair_matches_pressure_energy_and_integrated_work_without_trust():
    records=[pair_record(.1),pair_record(.05)]
    result=assess_pair(records)
    assert result["numeric_pass"] and result["quality_level"]=="Q2"
    assert max(result["relative_errors"].values())<1e-12 and result["common_sample_count"]==6
    assert not result["refinement_authenticated"] and not result["trusted_run"]
    assert not result["actual_use_authenticated"]


def test_two_individually_subthreshold_trajectories_can_fail_pair_refinement():
    records=[pair_record(.1,1.008),pair_record(.05,.992)]
    assert all(assess_record(r["code_sha256"],r["stdout"],r["cfl"])["numeric_pass"] for r in records)
    result=assess_pair(records)
    assert not result["numeric_pass"] and result["quality_level"]=="Q1"
    assert result["relative_errors"]["pressure"]>.01
    assert result["relative_errors"]["energy"]>.01


@pytest.mark.parametrize("fault",["missing","duplicate","failure","stderr","self_report"])
def test_pair_cannot_discard_case_or_use_failed_execution_or_reported_metric(fault):
    records=[pair_record(.1),pair_record(.05)]
    if fault=="missing":records.pop()
    elif fault=="duplicate":records[1]=records[0]
    elif fault=="failure":records[1]["exit_code"]=1
    elif fault=="stderr":records[1]["stderr"]="execution error"
    else:records[1]["relative_error"]=0.
    assert not assess_pair(records)["numeric_pass"]
