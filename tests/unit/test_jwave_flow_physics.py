import json

from cores.jwave_flow.workflow.config import Settings
from cores.jwave_flow.workflow.physics import check_params


def test_nested_si_materials_satisfy_jwave_flow_parameter_precheck():
    params = {
        "frequency_hz": 500000,
        "materials": {
            "water": {"sound_speed_m_s": 1500, "density_kg_m3": 1000},
            "skin": {"sound_speed_m_s": 1610, "density_kg_m3": 1090},
            "brain": {"sound_speed_m_s": 1560, "density_kg_m3": 1040},
        },
        "geometry": {"center_m": [0.105, 0, 0]},
        "sampling": {"grid_shape_xy": [241, 141], "step_m": 0.0005},
    }
    report = check_params(json.dumps(params), Settings())
    assert report.ok is True
    assert report.scalars["sound_speed"] == 1500
    assert report.scalars["frequency"] == 500000
    assert report.scalars["grid_size"] == [241, 141]


def test_jwave_flow_parameter_precheck_still_rejects_missing_physics():
    report = check_params(json.dumps({"materials": {"water": {"density_kg_m3": 1000}}}), Settings())
    assert report.ok is False
    assert {item.code for item in report.errors} >= {"E_NO_SOUND_SPEED", "E_NO_FREQUENCY"}
