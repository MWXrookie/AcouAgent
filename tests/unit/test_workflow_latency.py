import json
from pathlib import Path
import pytest
from scripts.workflow.optimize_latency import optimize

def test_migration_preserves_consumed_contracts():
    path=Path(__file__).resolve().parents[2]/"docs/dify_workflow_backup/2026-10-07-latency/published-before.json"
    original=json.loads(path.read_text())
    new=optimize(original)
    assert optimize(new)==new
    assert len(new["nodes"])==20
    assert len({n["id"] for n in new["nodes"]})==20
    assert all(n["id"]!="1783990970058" for n in new["nodes"])
    old={n["id"]:n for n in original["nodes"]}
    current={n["id"]:n for n in new["nodes"]}
    for id in ["1783991079665","1786095555003"]:
        assert current[id]["data"]["model"]["name"]=="deepseek-v4-flash"
        assert current[id]["data"]["model"]["completion_params"]["thinking"] is False
        assert current[id]["data"]["model"]["completion_params"]["max_tokens"] == 4096
        assert current[id]["data"]["prompt_template"]==old[id]["data"]["prompt_template"]
    assert current["1786091111002"]["data"]["tool_parameters"]["params_json"]["value"] == "{{#1783991048409.text#}}"
    for id in ["1786091111003","1786095555001","5655556822808"]:
        assert current[id]==old[id]

def test_reject_consumed_analysis():
    g={"nodes":[{"id":"1783990970058","data":{}},{"id":"consumer","data":{"ref":"1783990970058"}}],"edges":[]}
    with pytest.raises(ValueError): optimize(g)
