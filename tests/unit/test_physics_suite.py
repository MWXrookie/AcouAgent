import copy
import json
import pytest
from app import physics_suite
from scripts.validation import plane_wave_diagnostic as plane
from tests.unit.test_plane_wave_evidence import evidence as legacy_plane
from tests.unit.test_cylindrical_evidence import evidence as cylindrical,spherical
from tests.unit.test_interface_evidence import evidence as interface
from tests.unit.test_attenuation_evidence import evidence as attenuation
def documents():
    raw=legacy_plane()["raw_evidence"]
    dt=1e-8
    raw["time"]=[t+dt for t in raw["time"]]
    # Regenerate at the actual after-update timestamps, not relabel old pressure.
    import math
    for key,offset in (("pressure_x1",48),("pressure_x2",96)):
        arrival=offset*0.00025/1500;sigma=12*0.00025/1500
        raw[key]=[0.5*(math.exp(-0.5*((t-arrival)/sigma)**2)+math.exp(-0.5*((t+arrival)/sigma)**2)) for t in raw["time"]]
    raw.update(profile=plane.PROFILE,configuration=copy.deepcopy(plane.CONFIGURATION),dt_s=dt)
    return {k:json.dumps(v) for k,v in dict(plane_wave={"raw_evidence":raw},
        cylindrical=cylindrical(),interface=interface(),spherical=spherical(),attenuation=attenuation()).items()}
def test_all_five_recomputed_without_trust():
    got=physics_suite.assess(documents())
    assert got["numeric_pass"] and len(got["cases"])==5 and got["quality_level"]=="Q2"
    assert not any(got[k] for k in ("trusted_run","runtime_approved","run_bound","parameters_bound","actual_use_authenticated"))
@pytest.mark.parametrize("case",list(physics_suite.CASES))
def test_no_missing_or_failed_case_can_be_removed(case):
    docs=documents();docs.pop(case)
    assert not physics_suite.assess(docs)["numeric_pass"]
    docs=documents();docs[case]='{"status":"PASS","err_pct":0}'
    got=physics_suite.assess(docs)
    assert not got["numeric_pass"] and len(got["cases"])==5
def test_old_pressure_time_origin_cannot_stand_in_for_v3():
    raw=json.loads(documents()["plane_wave"]);raw["raw_evidence"]["time"]=[t-1e-8 for t in raw["raw_evidence"]["time"]]
    assert not plane.validate(json.dumps(raw))["numeric_pass"]
def test_suite_budget_and_unknown_extra_refused():
    docs=documents();docs["plane_wave"]=" "*2000001
    assert not physics_suite.assess(docs)["numeric_pass"]
    assert not physics_suite.assess(dict(documents(),extra="{}"))["numeric_pass"]
