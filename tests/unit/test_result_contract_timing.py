import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/workflow/_apply_result_contract.py"

def apply(tmp_path, graph):
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)

def test_old_contract_is_upgraded_without_losing_prompt(tmp_path):
    graph = {"nodes": [{"id": "1783991079665", "data": {"prompt_template": [
        {"role": "user", "text": "preserve instructions\n## 结果输出契约（必须遵守）\nold output requirements"}
    ]}}], "edges": []}
    result = apply(tmp_path, graph)
    assert result.returncode == 0
    updated = json.loads(result.stdout)
    text = updated["nodes"][0]["data"]["prompt_template"][0]["text"]
    assert "preserve instructions" in text and "old output requirements" in text
    assert "主波尚未到达传感器" in text
    assert "不证明稳态" in text
    assert json.loads(apply(tmp_path, updated).stdout) == updated

def test_new_contract_retains_field_and_sensor_output(tmp_path):
    graph = {"nodes": [{"id": "1783991079665", "data": {"prompt_template": [{"role": "user", "text": "base"}]}}]}
    result = json.loads(apply(tmp_path, graph).stdout)
    text = result["nodes"][0]["data"]["prompt_template"][0]["text"]
    assert "__ACOU_FIELD_START__" in text and "__ACOU_SENSOR_START__" in text
    assert "不得擅自延长" in text

def test_missing_target_refuses_update(tmp_path):
    assert apply(tmp_path, {"nodes": []}).returncode != 0
