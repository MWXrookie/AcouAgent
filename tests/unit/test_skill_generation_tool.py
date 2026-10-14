import inspect
import json
import pytest
from app import config,tools
from tests.unit.test_skill_reference_registry import proof
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared

def registered():
    class MCP:
        def __init__(self):self.functions={}
        def tool(self,func):
            self.functions[func.__name__]=func
            return func
    mcp=MCP();tools.register(mcp);return mcp.functions

def test_context_tool_has_no_learning_or_caller_authority_inputs(monkeypatch):
    functions=registered()
    tool=functions["build_simulation_generation_context"]
    assert set(inspect.signature(tool).parameters)=={"original_context","scenario_type","parameters_json"}
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
    result=tool("input","initial_pressure_2d","{}")
    assert result["context"]=="input" and result["skill_usage"]["skills"]==[]
    with pytest.raises(ValueError):tool("x"*65537,"initial_pressure_2d","{}")
    # Public search contract remains separate, read only, and empty on disabled.
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",False)
    assert functions["search_simulation_skill"]("initial_pressure_2d",'{"dimensions":2}',3)["skills"]==[]

def test_context_tool_uses_only_registered_signed_server_evidence(proof):
    from app import skill_reference_registry
    store,key,call,evidence=proof
    assert skill_reference_registry.register(evidence)["recorded"]
    generated=registered()["build_simulation_generation_context"]("generation input","plane_wave_2d",call["parameters_json"])
    assert not generated["skill_usage"]["fallback"]
    assert len(generated["skill_usage"]["skills"])==1
    assert not generated["skill_usage"]["actual_use_authenticated"]
    serialized=json.dumps(generated)
    assert "observation_document" not in serialized and "approval_document" not in serialized
    with pytest.raises(TypeError):
        registered()["build_simulation_generation_context"]("input","plane_wave_2d",call["parameters_json"],
                                                             trusted_run=True)


def test_read_only_workflow_pointer_rechecks_current_authority(proof,monkeypatch):
    from app import skill_reference_registry
    from tests.unit.test_skill_authority import root
    store,key,call,evidence=proof
    assert skill_reference_registry.register(evidence)["recorded"]
    funcs=registered()
    built=funcs["build_simulation_generation_context"]("original","plane_wave_2d",call["parameters_json"])
    usage=built["skill_usage"]
    kwargs=dict(original_context="original",scenario_type="plane_wave_2d",
                parameters_json=call["parameters_json"],decision_id=usage["decision_id"],
                generation_run_id=usage["generation_intent_id"])
    reader=funcs["read_simulation_generation_context"]
    with store._read_db() as db:
        before=db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]
    assert reader(**kwargs)==built["context"]
    assert reader(**dict(kwargs,original_context="forged context"))=="forged context"
    assert reader(**dict(kwargs,decision_id="forged"))=="original"
    assert skill_reference_registry.consumer.authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    assert reader(**kwargs)=="original"
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==before
