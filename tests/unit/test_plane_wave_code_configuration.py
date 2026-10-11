from pathlib import Path
from scripts.validation.plane_wave_code_configuration import derive_configuration, verify_configuration
from scripts.validation.plane_wave_evidence import EXPECTED_CONFIGURATION
from scripts.validation import plane_wave_run_binding as binding
import pytest

def recipe():
    return (binding.DIRECTORY / "recipes/val1_plane_wave_v2.py").read_text()

def test_reviewed_recipe_independently_derives_configuration():
    assert derive_configuration(recipe()) == EXPECTED_CONFIGURATION
    checked = verify_configuration(recipe(), EXPECTED_CONFIGURATION)
    assert checked["static_configuration_matched"]
    assert not checked["parameters_bound"] and not checked["trusted_run"]

@pytest.mark.parametrize("before,after", [
    ("sound_speed=1500.0", "sound_speed=1600.0"),
    ("x0 + 144", "x0 + 145"),
    ("CFL = 0.1", "CFL = 0.2"),
    ("smooth_initial=False", "smooth_initial=True"),
    ("dx = 0.25e-3", "dx = unknown()"),
])
def test_actual_call_or_parameter_drift_fails(before, after):
    assert not verify_configuration(recipe().replace(before, after), EXPECTED_CONFIGURATION)["static_configuration_matched"]

def test_duplicate_assignment_is_not_silently_used():
    code = recipe().replace("    A = 1.0", "    dx = 0.001\n    A = 1.0")
    assert not verify_configuration(code, EXPECTED_CONFIGURATION)["static_configuration_matched"]
