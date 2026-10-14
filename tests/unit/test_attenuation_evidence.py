import copy
import json
import math
import pytest
from scripts.validation import attenuation_evidence as gate
def evidence():
    ratio=gate.theory_ratio(gate.CONFIGURATION)
    return {"raw_evidence":dict(profile=gate.PROFILE,configuration=copy.deepcopy(gate.CONFIGURATION),
        units=dict(pressure="Pa",attenuation="dB/(MHz^2 cm)"),
        pressure_complex=[[0.6,0.8],[ratio*0.8,ratio*0.6]])}
def test_complex_amplitude_recomputed_without_summary_or_trust():
    got=gate.validate(json.dumps(evidence()))
    assert got["numeric_pass"] and got["error_pct"]<1e-10
    expected=math.sqrt(0.5)*math.exp(-(100*math.log(10)/20)*0.012)
    assert abs(got["theory_ratio"]-expected)<1e-15
    assert not got["trusted_run"] and not got["solver_convergence_authenticated"]
@pytest.mark.parametrize("fault",["summary","boolean","zero","units","time_domain","coefficient","pml","error","nan","shape"])
def test_wrong_physics_and_self_reported_output_refused(fault):
    value=evidence();raw=value["raw_evidence"]
    if fault=="summary":value["err_pct"]=0
    if fault=="boolean":raw["pressure_complex"][0][0]=True
    if fault=="zero":raw["pressure_complex"][0]=[0,0]
    if fault=="units":raw["units"]["attenuation"]="Np/m"
    if fault=="time_domain":raw["configuration"]["solver_mode"]="time_domain"
    if fault=="coefficient":raw["configuration"]["attenuation_db_mhz2_cm"]=0.0
    if fault=="pml":raw["configuration"]["sensor_indices"][1]=[145,80]
    if fault=="error":raw["pressure_complex"][1]=[p*1.02 for p in raw["pressure_complex"][1]]
    if fault=="nan":raw["pressure_complex"][0][0]=float("nan")
    if fault=="shape":raw["pressure_complex"][0].append(1)
    assert not gate.validate(json.dumps(value))["numeric_pass"]
def test_duplicate_fields_and_budget_refused():
    assert not gate.validate('{"raw_evidence":{},"raw_evidence":{}}')["numeric_pass"]
    assert not gate.validate(" "*16001)["numeric_pass"]
