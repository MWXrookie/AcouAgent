import json
import pytest
from app.physics_gate import assess_quality

def evaluate(params=None, **overrides):
    arguments = dict(exit_code=0, timed_out=False,
        analysis=dict(verdict="normal", max_pressure=1.0, rms_pressure=0.5,
                      field_shape=[64,64], has_signal=True),
        params_json=json.dumps(dict(sound_speed=1500, source_frequency=100000,
            domain_N=[64,64], domain_dx=0.00025, pml_size=10)))
    if params is not None:
        arguments["params_json"] = params
    arguments.update(overrides)
    return assess_quality(**arguments)["quality_level"]

def test_valid_is_only_candidate():
    assert evaluate() == "Q2"

@pytest.mark.parametrize("overrides", [
    dict(exit_code=False), dict(exit_code="0"), dict(timed_out=0), dict(analysis=[]),
    dict(analysis=dict(verdict="normal", max_pressure=True, rms_pressure=0.5,
         field_shape=[64,64], has_signal=True)),
    dict(analysis=dict(verdict="normal", max_pressure=10**1000, rms_pressure=0.5,
         field_shape=[64,64], has_signal=True)),
    dict(analysis=dict(verdict="normal", max_pressure=1., rms_pressure=0.5,
         field_shape=[64,64], has_signal="false")),
])
def test_invalid_execution_or_metrics(overrides):
    assert evaluate(**overrides) == "Q0"

@pytest.mark.parametrize("params", [
    '{"sound_speed":1500,"sound_speed":1500}', '{"x":NaN}', "["*1000,
    " "*4097, None, 42,
])
def test_invalid_parameter_documents(params):
    # None is passed explicitly to avoid the helper default.
    if params is None:
        assert assess_quality(exit_code=0, timed_out=False,
            analysis=dict(verdict="normal",max_pressure=1,rms_pressure=.5,
                field_shape=[64,64],has_signal=True),params_json=None)["quality_level"]=="Q1"
    else:
        assert evaluate(params) == "Q1"

@pytest.mark.parametrize("key,value", [
    ("sound_speed",True), ("source_frequency","100000"), ("cfl",False),
    ("domain_dx",True), ("sound_speed",10**1000), ("domain_dx",10**1000),
])
def test_invalid_physical_numbers(key,value):
    params=dict(sound_speed=1500,source_frequency=100000,
                domain_N=[64,64],domain_dx=.00025,pml_size=10)
    params[key]=value
    assert evaluate(json.dumps(params))=="Q1"
