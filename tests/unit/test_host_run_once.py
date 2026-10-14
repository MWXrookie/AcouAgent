import json
from pathlib import Path
import pytest
from host_observer import run_once as operator

def test_one_shot_target_requires_exact_operator_schema(tmp_path):
    path=tmp_path/"target.json"
    path.write_text('{"ip":"127.0.0.1","ip":"10.0.0.1"}')
    with pytest.raises(ValueError):operator.read_target(path)
    path.write_text('{"url":"https://example.invalid"}')
    with pytest.raises(ValueError):operator.read_target(path)

def test_evidence_never_overwrites_existing_file(tmp_path):
    path=tmp_path/"existing.json"
    operator.exclusive_json(path,{"value":"first"})
    with pytest.raises(FileExistsError):operator.exclusive_json(path,{"value":"second"})
    assert json.loads(path.read_text())=={"value":"first"}

def test_failed_forward_leaves_dispatch_without_automatic_retry(tmp_path,monkeypatch):
    from host_observer.forwarder import Target
    calls=[]
    def fail(*a,**kw):
        calls.append(1)
        raise ValueError("test failure")
    monkeypatch.setattr(operator,"forward_with_approved_key",fail)
    target=Target("127.0.0.1",8010,"a"*64,"sha256:"+("b"*64),"2026-10-08T00:00:00Z")
    with pytest.raises(ValueError):operator.observe_once(target,None,"test-token",tmp_path)
    assert len(calls)==1
    files=list(tmp_path.iterdir())
    assert len(files)==1 and files[0].name.endswith("-dispatch.json")
    assert "test-token" not in files[0].read_text()

def test_target_fifo_rejected_without_waiting_for_writer(tmp_path):
    import os
    path=tmp_path/"target-fifo"
    os.mkfifo(path)
    with pytest.raises(ValueError,match="regular_target"):
        operator.read_target(path)
