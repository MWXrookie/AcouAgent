import copy
import json
import math
import pytest
from app import physics_planar_initial as analytic,controlled_consistency as bundle
from tests.unit.test_controlled_consistency import records


@pytest.mark.parametrize("size",[64,128])
def test_exact_planar_gaussian_matches_dalembert_without_trust(size):
    pressure=[[value]*size for value in analytic.reference_profile(size,2e-6)]
    result=analytic.assess(pressure=pressure,sample_time_s=2e-6)
    assert result["passed"] and result["relative_error"]==0
    assert all(result[k] is False for k in bundle.gate.FALSE_TRUST)
    # Independent central value: two Gaussian halves at +/-3mm.
    assert math.isclose(pressure[3*size//8][0],math.exp(-.5*(.003/.002)**2),rel_tol=1e-12)


def test_consistent_constant_fields_fail_independent_physical_reference(records):
    assert bundle.assess(records)["numeric_pass"]
    result=bundle.assess_with_analytic_reference(records)
    assert not result["numeric_pass"] and result["quality_level"]=="Q1"
    assert len(result["checks"])==8
    assert all(not x["passed"] for x in result["checks"][-4:])


@pytest.mark.parametrize("fault",["wrong_speed","transverse","zero","nonfinite","shape","time","boolean"])
def test_planar_physical_mismatches_cannot_hide_in_peak_or_consistency(fault):
    pressure=[[value]*64 for value in analytic.reference_profile(64,2e-6)]
    time=2e-6
    if fault=="wrong_speed":pressure=[[value]*64 for value in analytic.reference_profile(64,1e-6)]
    elif fault=="transverse":pressure[20][30]+=.02
    elif fault=="zero":pressure=[[0.0]*64 for _ in range(64)]
    elif fault=="nonfinite":pressure[0][0]=float("nan")
    elif fault=="shape":pressure.pop()
    elif fault=="time":time=3e-6
    else:pressure[0][0]=True
    assert not analytic.assess(pressure=pressure,sample_time_s=time)["passed"]


def test_strict_one_percent_full_field_boundary():
    pressure=[[value]*64 for value in analytic.reference_profile(64,2e-6)]
    peak=max(x[0] for x in pressure)
    pressure[0][0]+=.01*peak
    assert not analytic.assess(pressure=pressure,sample_time_s=2e-6)["passed"]


def test_periodic_extension_is_continuous_across_domain_and_not_static():
    at=analytic.reference_profile(128,2e-6)
    assert at[0]>math.exp(-.5*((0-.012)/.002)**2)
    assert at[48]<1
