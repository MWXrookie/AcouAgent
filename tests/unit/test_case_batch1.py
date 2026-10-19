import hashlib
import json
import math
import pytest
from scripts.validation import case_batch1 as batch
from scripts.validation import case_batch_registry as registry
from scripts.validation import interface_evidence, attenuation_evidence

@pytest.mark.parametrize("case", batch.CASE_IDS)
def test_recipe_exact_configuration_and_no_trust(case):
    cfg, code = batch.specification(case)
    compile(code, "<diagnostic>", "exec")
    assert "case-batch1-" + case in code
    assert batch.assess(case, dict(code_sha256=hashlib.sha256(code.encode()).hexdigest(),
           exit_code=True, timed_out=False, stderr="", stdout="{}"))["numeric_pass"] is False
    assert "trusted_run" not in code


def test_base_plane_recipe_emits_the_current_diagnostic_configuration():
    cfg, code = registry.specification("base-plane_wave")
    assert cfg["domain_N"] == [352, 320]
    assert cfg["t_end_s"] == 24e-6
    assert cfg["sensor_indices"] == [[208, 160], [256, 160]]
    for exact in ("jw.Domain((352,320)", "t_end=24e-6", "domain_N=[352,320]",
                  "sensor_indices=[[208,160],[256,160]]",
                  "field[:,208,160,0]", "field[:,256,160,0]"):
        assert exact in code

@pytest.mark.parametrize("field,value", [
    ("stderr", "warning"), ("exit_code", 1), ("timed_out", True),
    ("code_sha256", "0"*64), ("stdout", "{}")])
def test_five_rejection_cases(field, value):
    case = "attenuation-coeff08"
    cfg, code = batch.specification(case)
    ratio = attenuation_evidence.theory_ratio(cfg)
    raw = dict(raw_evidence=dict(profile="case-batch1-"+case, configuration=cfg,
        units=dict(pressure="Pa", attenuation="dB/(MHz^2 cm)"),
        pressure_complex=[[1., 0.], [ratio, 0.]]))
    record = dict(code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                  exit_code=0, timed_out=False, stderr="", stdout=json.dumps(raw))
    result = batch.assess(case, record)
    assert result["numeric_pass"] is True
    assert all(result[k] is False for k in
        ("trusted_run", "runtime_approved", "run_bound", "parameters_bound", "actual_use_authenticated"))
    record[field] = value
    assert batch.assess(case, record)["numeric_pass"] is False

def test_variant_cannot_expand_public_fixed_profile():
    cfg, _ = batch.specification("attenuation-coeff08")
    raw = dict(raw_evidence=dict(profile=attenuation_evidence.PROFILE, configuration=cfg,
        units=dict(pressure="Pa", attenuation="dB/(MHz^2 cm)"),
        pressure_complex=[[1.,0.], [attenuation_evidence.theory_ratio(cfg),0.]]))
    assert attenuation_evidence.validate(json.dumps(raw))["numeric_pass"] is False

def test_wrong_physics_is_not_successful_exit():
    cfg, code = batch.specification("attenuation-coeff08")
    raw = dict(raw_evidence=dict(profile="case-batch1-attenuation-coeff08", configuration=cfg,
        units=dict(pressure="Pa", attenuation="dB/(MHz^2 cm)"),pressure_complex=[[1.,0.], [1.,0.]]))
    record=dict(code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                exit_code=0,timed_out=False,stderr="",stdout=json.dumps(raw))
    assert batch.assess("attenuation-coeff08",record)["numeric_pass"] is False
