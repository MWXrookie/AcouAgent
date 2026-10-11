import copy
import json
import pytest
from scripts.validation import interface_evidence as gate
def evidence():
    dt=1e-8;times=[(i+1)*dt for i in range(2400)]
    reflection=3.5/6.5;transmission=10/6.5
    return {"raw_evidence":dict(profile=gate.PROFILE,configuration=copy.deepcopy(gate.CONFIGURATION),
        units=dict(time="s",pressure="Pa"),dt_s=dt,time=times,
        pressure_left=[0.5 if 4e-6<=t<=7e-6 else 0.5*reflection if 15e-6<=t<=18e-6 else 0.0 for t in times],
        pressure_right=[0.5*transmission if 12e-6<=t<=16e-6 else 0.0 for t in times])}
def test_raw_reflection_and_transmission_both_recomputed():
    got=gate.validate(json.dumps(evidence()))
    assert got["numeric_pass"] and all(e<1 for e in got["error_pct"].values())
    assert not got["trusted_run"] and not got["parameters_bound"]
@pytest.mark.parametrize("fault",["density","summary","window","reflection","transmission","origin","boolean"])
def test_failed_or_self_reported_interface_never_passes(fault):
    value=evidence();raw=value["raw_evidence"]
    if fault=="density":raw["configuration"]["density_kg_m3"]=[1000.0,1000.0]
    if fault=="summary":value["err_pct"]={"reflection":0,"transmission":0}
    if fault=="window":
        for key in ["time","pressure_left","pressure_right"]:raw[key]=raw[key][:1500]
    if fault=="reflection":
        raw["pressure_left"]=[p*1.02 if 14e-6<=t<=19e-6 else p for t,p in zip(raw["time"],raw["pressure_left"])]
    if fault=="transmission":raw["pressure_right"]=[p*1.02 for p in raw["pressure_right"]]
    if fault=="origin":raw["time"]=[t-raw["dt_s"] for t in raw["time"]]
    if fault=="boolean":raw["pressure_left"][0]=True
    assert not gate.validate(json.dumps(value))["numeric_pass"]


def test_float32_time_axis_roundoff_remains_valid():
    import struct
    raw=evidence()
    raw["raw_evidence"]["time"]=[struct.unpack("f",struct.pack("f",v))[0] for v in raw["raw_evidence"]["time"]]
    assert gate.validate(json.dumps(raw))["numeric_pass"]
