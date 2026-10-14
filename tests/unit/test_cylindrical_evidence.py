import copy
import json
import math
import pytest
from scripts.validation import cylindrical_evidence as gate
def evidence():
    dt=1/30000000
    time=[(i+1)*dt for i in range(1800)]
    ratio=math.sqrt(64/96)
    first=[1.0 if 25e-6<=t<=30e-6 else 0.0 for t in time]
    second=[ratio if 36e-6<=t<=41e-6 else 0.0 for t in time]
    return {"raw_evidence":dict(profile=gate.PROFILE,configuration=copy.deepcopy(gate.CONFIGURATION),
        units=dict(time="s",pressure="Pa"),dt_s=dt,time=time,pressure_r1=first,pressure_r2=second)}

def test_raw_arrays_recompute_ratio_without_self_reported_grade():
    result=gate.validate(json.dumps(evidence()))
    assert result["numeric_pass"] and result["error_pct"]<1
    assert result["pml_geometry_passed"] and result["complete_windows"]
    assert not any(result[k] for k in ("trusted_run","runtime_approved","run_bound","parameters_bound"))

@pytest.mark.parametrize("fault",["pml","time_origin","window","nonfinite","bool","summary","threshold","units","huge"])
def test_missing_drifted_or_failed_raw_evidence_never_passes(fault):
    value=evidence();raw=value["raw_evidence"]
    if fault=="pml":raw["configuration"]["sensor_indices"][1]=[240,128]
    if fault=="time_origin":raw["time"]=[t-raw["dt_s"] for t in raw["time"]]
    if fault=="window":
        for key in ["time","pressure_r1","pressure_r2"]:raw[key]=raw[key][:800]
    if fault=="nonfinite":raw["pressure_r1"][0]=float("nan")
    if fault=="bool":raw["pressure_r1"][0]=True
    if fault=="summary":value["numeric_pass"]=True
    if fault=="threshold":raw["pressure_r2"]=[p*1.010001 for p in raw["pressure_r2"]]
    if fault=="units":raw["units"]["pressure"]="kPa"
    if fault=="huge":raw["pressure_r1"][0]=10**400
    assert not gate.validate(json.dumps(value))["numeric_pass"]

def test_duplicate_or_oversized_json_fails_closed():
    assert not gate.validate('{"raw_evidence":{},"raw_evidence":{}}')["numeric_pass"]
    assert not gate.validate(" "*500001)["numeric_pass"]


def spherical():
    dt=1/30000000
    time=[(i+1)*dt for i in range(750)]
    return {"raw_evidence":dict(profile=gate.SPHERICAL_PROFILE,
        configuration=copy.deepcopy(gate.SPHERICAL_CONFIGURATION),units=dict(time="s",pressure="Pa"),dt_s=dt,time=time,
        pressure_r1=[1.0 if 8e-6<=t<=11e-6 else 0.0 for t in time],
        pressure_r2=[2/3 if 11e-6<=t<=14e-6 else 0.0 for t in time])}
def test_spherical_raw_arrays_use_three_dimensional_geometric_law():
    raw=spherical()
    got=gate.validate_spherical(json.dumps(raw))
    assert got["numeric_pass"] and got["error_pct"]<1
    assert not gate.validate(json.dumps(raw))["numeric_pass"]
    assert not gate.validate_spherical(json.dumps(evidence()))["numeric_pass"]
    # A 2D square-root ratio cannot stand in for 3D pressure decay.
    raw["raw_evidence"]["pressure_r2"]=[p*math.sqrt(3/2) for p in raw["raw_evidence"]["pressure_r2"]]
    assert not gate.validate_spherical(json.dumps(raw))["numeric_pass"]

def test_spherical_probe_in_absorbing_layer_is_not_accepted():
    raw=spherical();raw["raw_evidence"]["configuration"]["sensor_indices"][1]=[64,36,36]
    assert not gate.validate_spherical(json.dumps(raw))["numeric_pass"]


def test_float32_time_axis_precision_is_not_an_origin_shift():
    import struct
    raw=evidence()
    raw["raw_evidence"]["time"]=[struct.unpack("f",struct.pack("f",v))[0] for v in raw["raw_evidence"]["time"]]
    assert gate.validate(json.dumps(raw))["numeric_pass"]
