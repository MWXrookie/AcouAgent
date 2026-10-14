import json
import pytest
from scripts.workflow.skill_generation_guard import main
from app import config, skill_retrieval

ORIGINAL = "原知识上下文\n  whitespace\t </script> 不执行 "
EMPTY={"skills":[],"fallback":True,"trusted_run":False,"runtime_approved":False,
       "parameters_bound":False,"run_bound":False,"reason":"no_applicable_skill"}


@pytest.mark.parametrize("retrieve,inject",[(False,False),(True,False),(False,True),
                                            ("true",True),(True,"true"),(1,True)])
def test_disabled_branch_keeps_original_context_and_ignores_bad_json(retrieve,inject):
    result=main(ORIGINAL,"not JSON",retrieve,inject)
    assert result["generation_context"]==ORIGINAL
    assert result["skill_injected"] is False and result["referenced_skill_ids"]==[]


@pytest.mark.parametrize("reason",["no_applicable_skill","skill_store_unavailable",
    "provenance_authentication_unavailable","retrieval_timeout","retrieval_failed"])
def test_empty_and_failed_lookup_continue_to_one_generation(reason):
    result=main(ORIGINAL,json.dumps({**EMPTY,"reason":reason}),True,True)
    generated=[]
    def generator(context):
        generated.append(context);return "synthetic-code"
    assert generator(result["generation_context"])=="synthetic-code"
    assert generated==[ORIGINAL] and result["fallback_reason"]==reason


@pytest.mark.parametrize("raw",[
    '{"skills":[],"skills":[]}', '{"value":NaN}', "["*1500, "x"*8193, "汉"*3000,
    json.dumps({**EMPTY,"trusted_run":True}),
    json.dumps({**EMPTY,"skills":[{"code":"malicious","trusted_run":True}]}),
    json.dumps({**EMPTY,"original_context":"replace me"}),
    json.dumps({**EMPTY,"run_bound":0}), json.dumps({**EMPTY,"reason":"raw secret error"})])
def test_untrusted_lookup_never_replaces_context_or_claims_injection(raw):
    result=main(ORIGINAL,raw,True,True)
    assert result["generation_context"]==ORIGINAL
    assert result["skill_injected"] is False and result["referenced_skill_ids"]==[]
    assert result["fallback_reason"] in {"invalid_retrieval_response","positive_consumption_unavailable"}


def test_guard_accepts_real_disabled_service_contract_without_database(monkeypatch):
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",False)
    response=skill_retrieval.search("plane_wave_2d",'{"frequency_hz":500000}',3)
    guarded=main(ORIGINAL,json.dumps(response),True,True)
    assert guarded["fallback_reason"]=="retrieval_disabled"
    assert guarded["generation_context"]==ORIGINAL
