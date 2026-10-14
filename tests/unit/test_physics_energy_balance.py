"""Manufactured raw fields, not executor/provenance or model certification."""
import copy
import math
import pytest
from app.physics_energy_balance import assess_energy_balance


def field(value,shape=(2,2)):
    return [field(value,shape[1:]) for _ in range(shape[0])] if shape else value


def evidence(times=(0.,1.,2.),dims=2):
    shape=(2,)*dims
    faces={str(axis)+sign:[
        dict(pressure_pa=field(1+t,shape[:axis]+shape[axis+1:]),
             outward_velocity_m_s=field(0.,shape[:axis]+shape[axis+1:]))
        for t in times] for axis in range(dims) for sign in ("-","+")}
    return dict(times_s=list(times),pressure_pa=[field(1+t,shape) for t in times],
        velocity_m_s=[[field(0.,shape) for _ in range(dims)] for t in times],
        pressure_rate_pa_s=[field(1.,shape) for t in times],
        acceleration_m_s2=[[field(0.,shape) for _ in range(dims)] for t in times],
        boundary_faces=faces,spacing_m=[.5]*dims,density_kg_m3=1.,
        sound_speed_m_s=1.,model="uniform_lossless_stationary_linear",
        sampling="collocated_cell_volume_and_face_area")


def test_uniform_pressure_source_work_all_intervals_and_units():
    # p=1+t, q=1, u=0 solves the explicit uniform forced equations.
    for dims in (2,3):
        result=assess_energy_balance(**evidence(times=(0.,.25,1.,2.),dims=dims))
        assert result["passed"] and result["relative_error"]==0.
        assert result["energy_unit"]==("J" if dims==3 else "J_per_m_depth")
        assert result["interval_count"]==3
        assert result["energy_samples"]==[.5,.78125,2.,4.5]
        assert not result["trusted_run"] and not result["eligible_for_trusted_activation"]


def test_body_acceleration_work_and_outward_normals_cancel():
    raw=evidence();times=raw["times_s"]
    raw["pressure_pa"]=[field(1.) for t in times]
    raw["pressure_rate_pa_s"]=[field(0.) for t in times]
    raw["velocity_m_s"]=[[field(t),field(0.)] for t in times]
    raw["acceleration_m_s2"]=[[field(1.),field(0.)] for t in times]
    for key in raw["boundary_faces"]:
        sign=(-1 if key.endswith("-") else 1)
        for t,face in zip(times,raw["boundary_faces"][key]):
            face["pressure_pa"]=[1.,1.]
            face["outward_velocity_m_s"]=[sign*t if key.startswith("0") else 0.]*2
    result=assess_energy_balance(**raw)
    assert result["passed"]
    assert result["outward_flux_intervals"]==[0.,0.]
    assert result["source_work_intervals"]==[.5,1.5]


def test_signed_boundary_budget_accepts_inflow_and_rejects_wrong_sign():
    # Scalar quadrature fixture verifies flux sign/area, not a PDE solution.
    raw=evidence(times=(0.,1.));raw["pressure_rate_pa_s"]=[field(0.)]*2
    raw["pressure_pa"]=[field(1.),field(math.sqrt(2.))]
    for key,frames in raw["boundary_faces"].items():
        for frame in frames:
            frame["pressure_pa"]=[1.,1.]
            frame["outward_velocity_m_s"]=[-.5,-.5] if key=="0-" else [0.,0.]
    good=assess_energy_balance(**raw)
    assert good["passed"] and good["outward_flux_intervals"]==[-.5]
    for frame in raw["boundary_faces"]["0-"]:frame["outward_velocity_m_s"]=[.5,.5]
    assert not assess_energy_balance(**raw)["passed"]


def test_endpoint_cancellation_cannot_hide_bad_middle_interval():
    raw=evidence();raw["pressure_pa"]=[field(1.),field(2.),field(1.)]
    raw["pressure_rate_pa_s"]=[field(0.)]*3
    result=assess_energy_balance(**raw)
    assert not result["passed"] and result["relative_error"]==.75


@pytest.mark.parametrize("change",[
    lambda r:r["boundary_faces"].pop("0-"),
    lambda r:r.update(model="absorbing"),
    lambda r:r.update(sampling="staggered"),
    lambda r:r.update(times_s=[0.,0.,2.]),
    lambda r:r.update(times_s=[0.,True,2.]),
    lambda r:r.update(times_s=[0.,float("nan"),2.]),
    lambda r:r["pressure_pa"].pop(),
    lambda r:r["velocity_m_s"][0].pop(),
    lambda r:r["boundary_faces"]["0-"][0].update(pressure_pa=[1.]),
    lambda r:r["boundary_faces"]["0-"][0].update(verdict="normal"),
    lambda r:r.update(sound_speed_m_s=1e200),
    lambda r:r.update(density_kg_m3=0.),
    lambda r:r.update(spacing_m=[True,.5]),
    lambda r:r["acceleration_m_s2"][0][0][0].__setitem__(0,float("inf")),
])
def test_incomplete_unsupported_nonfinite_or_misaligned_raw_evidence_fails(change):
    raw=evidence();change(raw)
    result=assess_energy_balance(**raw)
    assert not result["passed"] and result["reason"]=="raw_evidence_unavailable"


def test_raw_budget_precedes_expanding_frame_data():
    raw=evidence()
    shape=(200,200);raw["pressure_pa"][0]=field(1.,shape)
    result=assess_energy_balance(**raw)
    assert not result["passed"] and result["reason"]=="raw_evidence_unavailable"


def test_unforced_energy_growth_and_strict_one_percent_limit():
    raw=evidence(times=(0.,1.))
    raw["pressure_rate_pa_s"]=[field(0.)]*2
    # Relative residual uses the larger endpoint energy, not a caller tolerance.
    raw["pressure_pa"]=[field(1.),field(math.sqrt(1./.989))]
    result=assess_energy_balance(**raw)
    assert result["relative_error"]>0.01 and not result["passed"]
    raw["pressure_pa"]=[field(1.),field(math.sqrt(1./.995))]
    assert assess_energy_balance(**raw)["passed"]
