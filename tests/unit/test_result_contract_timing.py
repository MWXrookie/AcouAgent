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

def test_metadata_upgrade_is_idempotent(tmp_path):
    graph = {"nodes": [{"id": "1783991079665", "data": {"prompt_template": [
        {"role": "user", "text": "keep my request"}
    ]}}]}
    updated = json.loads(apply(tmp_path, graph).stdout)
    text = updated["nodes"][0]["data"]["prompt_template"][0]["text"]
    assert "actual_source_index" in text and "actual_uniform_sound_speed" in text
    assert "不得为凑字段伪造模型条件" in text
    assert json.loads(apply(tmp_path, updated).stdout) == updated


def test_producer_snippet_round_trips_to_consumer(tmp_path):
    import runpy
    from app.analysis import _extract_sensor
    from app.physics_timing import assess_sensor_timing
    module = runpy.run_path(str(SCRIPT))
    fixture = """
import json
actual_uniform_sound_speed=1500
actual_source_frequency_hz=500000
actual_source_index=[64,64]
actual_sensor_index=[84,64]
actual_grid_spacing_m=[.00025,.00025]
sensor_payload={"time":[i*5e-8 for i in range(31)],"pressure":[0]*31}
"""
    footer = """
print("__ACOU_SENSOR_START__")
print(json.dumps(sensor_payload, allow_nan=False))
print("__ACOU_SENSOR_END__")
"""
    path = tmp_path / "producer_fixture.py"
    path.write_text(fixture + module["SENSOR_METADATA_SNIPPET"] + footer)
    produced = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True)
    sensor = _extract_sensor(produced.stdout)
    result = assess_sensor_timing(sensor)
    assert result["status"] == "insufficient"
    assert result["metadata_authenticated"] is False
    assert result["trust_verified"] is False


def test_time_origin_upgrade_covers_primary_and_retry_without_other_changes(tmp_path):
    import copy
    graph={"nodes":[{"id":node,"data":{"prompt_template":[{"role":"user","text":"keep "+node}]}}
                   for node in ("1783991079665","1786095555003","unrelated")]}
    saved=copy.deepcopy(graph)
    updated=json.loads(apply(tmp_path,graph).stdout)
    assert graph==saved and updated["nodes"][2]==saved["nodes"][2]
    for node in updated["nodes"][:2]:
        assert "jwave 压力输出实际时刻（v3）" in node["data"]["prompt_template"][0]["text"]
    assert json.loads(apply(tmp_path,updated).stdout)==updated

def test_time_origin_snippet_emits_post_update_axis_and_preserves_pressure(tmp_path):
    import runpy
    from app.analysis import _extract_sensor
    from app.physics_timing import assess_sensor_timing
    module=runpy.run_path(str(SCRIPT))
    namespace=dict(actual_uniform_sound_speed=1500,actual_source_frequency_hz=500000,
                   actual_source_index=[64,64],actual_sensor_index=[84,64],
                   actual_grid_spacing_m=[.00025,.00025],
                   sensor_payload={"time":[i*5e-8 for i in range(31)],"pressure":[0]*31},
                   sensor_pressure=[0]*31,time_axis=type("Axis",(),{"dt":5e-8})())
    exec(module["SENSOR_METADATA_SNIPPET"],namespace)
    exec(module["SENSOR_TIME_ORIGIN_SNIPPET"],namespace)
    payload=namespace["sensor_payload"]
    assert payload["time"][0]==5e-8 and payload["pressure"]==[0]*31
    sensor=_extract_sensor("__ACOU_SENSOR_START__"+json.dumps(payload)+"__ACOU_SENSOR_END__")
    result=assess_sensor_timing(sensor)
    assert result["status"]=="insufficient" and result["end_time_s"]==payload["time"][-1]
    assert not result["metadata_authenticated"]
