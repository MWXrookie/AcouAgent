import pytest
from app import physics_consistency as gate

FIELD=[[1.0,1.0],[1.0,1.0]]
ZERO=[[0.0,0.0],[0.0,0.0]]
GRID=dict(coarse_pressure=FIELD,refined_pressure=[[1.0]*4 for _ in range(4)],
          coarse_dx_m=[.001,.001],refined_dx_m=[.0005,.0005],coarse_time_s=1e-5,refined_time_s=1e-5)
TIME=dict(base_pressure=FIELD,refined_pressure=FIELD,base_dt_s=1e-7,refined_dt_s=5e-8,
          base_time_s=1e-5,refined_time_s=1e-5)
ENERGY=dict(initial_pressure=FIELD,final_pressure=FIELD,initial_velocity=[ZERO,ZERO],
            final_velocity=[ZERO,ZERO],density_kg_m3=1000,sound_speed_m_s=1500,
            cell_volume_m3=1e-9,source_free=True)

def test_valid_raw_numeric_checks_never_grant_trust():
    for result in (gate.grid_convergence(**GRID),gate.time_step_convergence(**TIME),
                   gate.source_free_energy(**ENERGY),
                   gate.equivalent_parameters(base_pressure=FIELD,equivalent_pressure=FIELD,
                     base_parameters={"frequency_mhz":.5,"dx_mm":.25},
                     equivalent_parameters={"frequency_hz":500000,"dx_m":.00025},
                     base_time_s=1e-5,equivalent_time_s=1e-5)):
        assert result["passed"] and result["relative_error"]==0
        assert all(result[key] is False for key in gate.FALSE_TRUST)

@pytest.mark.parametrize("change",[
    dict(refined_dx_m=[.001,.0005]),dict(refined_time_s=2e-5),
    dict(refined_pressure=[[1.02]*4 for _ in range(4)]),
    dict(refined_pressure=[[True]*4 for _ in range(4)]),
    dict(coarse_time_s=0),dict(coarse_dx_m=[float("nan"),.001])])
def test_grid_drift_or_invalid_raw_evidence_rejected(change):
    assert not gate.grid_convergence(**dict(GRID,**change))["passed"]

@pytest.mark.parametrize("change",[dict(refined_dt_s=1e-7),dict(refined_time_s=2e-5),
                                   dict(refined_pressure=[[1.02]*2 for _ in range(2)]),
                                   dict(refined_pressure=ZERO)])
def test_temporal_refinement_requires_same_physical_time_and_convergence(change):
    assert not gate.time_step_convergence(**dict(TIME,**change))["passed"]

def test_energy_uses_pressure_and_particle_velocity_not_producer_summary():
    assert not gate.source_free_energy(**dict(ENERGY,final_pressure=[[1.02]*2 for _ in range(2)]))["passed"]
    assert not gate.source_free_energy(**dict(ENERGY,final_velocity=[FIELD,FIELD]))["passed"]
    assert gate.source_free_energy(**dict(ENERGY,final_pressure=ZERO))["passed"]
    assert not gate.source_free_energy(**dict(ENERGY,source_free=False))["passed"]
    assert not gate.source_free_energy(**dict(ENERGY,initial_velocity=[]))["passed"]
    assert not gate.source_free_energy(**dict(ENERGY,density_kg_m3=True))["passed"]

def test_nonequivalent_parameters_and_zero_reference_not_convergence():
    result=gate.equivalent_parameters(base_pressure=FIELD,equivalent_pressure=FIELD,
      base_parameters={"frequency_mhz":.5},equivalent_parameters={"frequency_hz":600000},
      base_time_s=1e-5,equivalent_time_s=1e-5)
    assert not result["passed"]
    assert not gate.time_step_convergence(**dict(TIME,base_pressure=ZERO))["passed"]

def test_large_ragged_and_overflow_arrays_are_rejected():
    assert not gate.time_step_convergence(**dict(TIME,base_pressure=[[1],[1,2]]))["passed"]
    assert not gate.time_step_convergence(**dict(TIME,base_pressure=[[1.0]*1001 for _ in range(100)]))["passed"]
    assert not gate.source_free_energy(**dict(ENERGY,initial_pressure=[[10**400]*2 for _ in range(2)]))["passed"]


def test_fine_midpoint_oscillation_is_not_hidden_by_common_nodes():
    fine=[[1.0]*4 for _ in range(4)]
    fine[1][1]=1.1
    args=dict(GRID,refined_pressure=fine)
    assert gate.grid_convergence(**args)["passed"]
    full=gate.grid_convergence_full_periodic(**args,periodic=True)
    assert not full["passed"] and full["common_nodes_relative_error"]==0
    assert full["whole_refined_field_relative_error"]>0.09
    assert not any(full[key] for key in gate.FALSE_TRUST)

def test_periodic_projection_requires_matching_nodal_grid_and_boundary():
    assert gate.grid_convergence_full_periodic(**GRID,periodic=True)["passed"]
    assert not gate.grid_convergence_full_periodic(**GRID,periodic=False)["passed"]
    assert not gate.grid_convergence_full_periodic(**dict(GRID,refined_dx_m=[.001,.001]),periodic=True)["passed"]
