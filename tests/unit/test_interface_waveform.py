import json
import pytest
from scripts.validation import interface_waveform as gate
from scripts.validation.interface_evidence import CONFIGURATION,PROFILE

def raw():
    dt=CONFIGURATION["cfl"]*min(CONFIGURATION["domain_dx_m"])/max(CONFIGURATION["sound_speed_m_s"])
    times=[(i+1)*dt for i in range(2400)]
    left,right=gate.analytic_traces(times)
    return dict(raw_evidence=dict(profile=PROFILE,configuration=CONFIGURATION,
        units=dict(time="s",pressure="Pa"),dt_s=dt,time=times,pressure_left=left,pressure_right=right))

def test_analytic_signed_trace_diagnostic_remains_untrusted():
    result=gate.validate(json.dumps(raw()))
    assert result["numeric_pass"] and result["quality_level"]=="Q2"
    assert result["waveform_relative_errors"]==[0,0]
    assert not result["trusted_run"] and not result["source_authenticated"]

@pytest.mark.parametrize("fault",["invert","shift","distort","negative_time","wrong_geometry","nonfinite"])
def test_peak_ratio_alone_cannot_hide_wrong_waveform(fault):
    value=raw();data=value["raw_evidence"]
    if fault=="invert":data["pressure_right"]=[-p for p in data["pressure_right"]]
    if fault=="shift":data["pressure_right"]=[0]*30+data["pressure_right"][:-30]
    if fault=="distort":
        data["pressure_right"]=[p if i%2 else -.5*p for i,p in enumerate(data["pressure_right"])]
    if fault=="negative_time":data["time"][0]=-1
    if fault=="wrong_geometry":
        data["configuration"]=dict(CONFIGURATION,interface_x_index=193)
    if fault=="nonfinite":data["pressure_right"][20]=float("nan")
    assert not gate.validate(json.dumps(value))["numeric_pass"]

def test_wide_domain_has_distinct_scope_and_no_silent_old_case_expansion():
    value=raw();data=value["raw_evidence"]
    data["profile"]=gate.WIDE_PROFILE;data["configuration"]=gate.WIDE_CONFIGURATION
    data["pressure_left"],data["pressure_right"]=gate.analytic_traces(data["time"],gate.WIDE_CONFIGURATION)
    assert gate.validate_wide(json.dumps(value))["numeric_pass"]
    assert not gate.validate(json.dumps(value))["numeric_pass"]

def test_single_bad_sample_cannot_hide_under_small_l2_error():
    value=raw();data=value["raw_evidence"]
    data["pressure_right"][2100]+=.02*max(data["pressure_right"])
    result=gate.validate(json.dumps(value))
    assert result["waveform_relative_errors"][1]<.01
    assert result["maximum_sample_relative_errors"][1]>.01
    assert not result["numeric_pass"]
