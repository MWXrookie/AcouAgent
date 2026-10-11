import copy
import hashlib
import json
import math
import pytest
from app import controlled_consistency as bundle
from scripts.validation.consistency_experiment import recipe

@pytest.fixture
def records():
    result={}
    for name,(size,cfl,equivalent) in bundle.PROFILES.items():
        dx=.032/size;dt=cfl*dx/1500
        raw=dict(pressure=[[.001]*size for _ in range(size)],sample_time_s=2e-6,
            output_index=round(2e-6/dt)-1,pressure_sample_origin="after_first_update",
            energy_data={},dt_s=dt,dx_m=[dx,dx],parameters=dict(domain_N=[size,size],
            dx_m=dx,sound_speed_m_s=1500.0,density_kg_m3=1000.0,pml_size=0,cfl=cfl,t_end_s=2.2e-6))
        if name=="base":
            raw["energy_data"]=dict(initial_pressure=[[math.exp(-.5*((i*dx-.012)/.002)**2)]*size for i in range(size)],
                final_velocity=[[[0.0]*size for _ in range(size)] for _ in range(2)])
        result[name]=dict(code_sha256=hashlib.sha256(recipe(size,cfl,equivalent).encode()).hexdigest(),stdout=json.dumps(raw))
    return result

def test_four_raw_experiments_pass_without_granting_q3(records):
    got=bundle.assess(records)
    assert got["numeric_pass"] and got["quality_level"]=="Q2" and len(got["checks"])==4
    assert not any(got[k] for k in bundle.gate.FALSE_TRUST)

@pytest.mark.parametrize("fault",["missing","code","time","pml","shape","source","boolean","summary","energy"])
def test_configuration_and_raw_failure_cannot_be_hidden(records,fault):
    if fault=="missing":records.pop("grid")
    elif fault=="code":records["base"]["code_sha256"]="0"*64
    else:
        raw=json.loads(records["base"]["stdout"])
        if fault=="time":raw["sample_time_s"]=1.9e-6
        elif fault=="pml":raw["parameters"]["pml_size"]=20
        elif fault=="shape":raw["pressure"]=raw["pressure"][:-1]
        elif fault=="source":raw["energy_data"]["initial_pressure"]=[[1.0]*64 for _ in range(64)]
        elif fault=="boolean":raw["parameters"]["pml_size"]=False
        elif fault=="summary":raw["numeric_pass"]=True
        elif fault=="energy":raw["energy_data"]["final_velocity"]=[[[1.0]*64 for _ in range(64)] for _ in range(2)]
        records["base"]["stdout"]=json.dumps(raw)
    assert not bundle.assess(records)["numeric_pass"]

def test_raw_budget_and_duplicate_keys_fail_closed(records):
    records["base"]["stdout"]=" "*500001
    assert not bundle.assess(records)["numeric_pass"]
    records["base"]["stdout"]='{"pressure":0,"pressure":1}'
    assert not bundle.assess(records)["numeric_pass"]
