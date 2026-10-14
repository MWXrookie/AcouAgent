import json
import subprocess
import sys
import time
import pytest
from scripts.validation import executor_host_observation as observation


def snapshot():
    return dict(container_id="a" * 64, image_id="sha256:" + "b" * 64,
                running=True, started_at="2026-10-02T00:00:00Z", restart_count=0)


def test_collect_fixed_command(monkeypatch):
    seen = []
    def run(command, **kwargs):
        seen.append((command, kwargs))
        return json.dumps(snapshot())
    monkeypatch.setattr(observation, "_bounded_output", run)
    result = observation.collect_window()
    assert result["check"]["lifecycle_stable"]
    assert not result["check"]["run_bound"] and not result["check"]["trusted_run"]
    assert len(seen) == 2
    assert all(command[-1] == "jwave-executor" and kwargs["timeout"] == 10 and kwargs["limit"] == 2048 for command,kwargs in seen)
    assert all("Env" not in command[-2] for command,_ in seen)


@pytest.mark.parametrize("field,value", [("container_id","c"*64),("image_id","sha256:"+"d"*64),
    ("started_at","restart"),("restart_count",1),("running",False),("restart_count",True),
    ("container_id","short"),("image_id","latest")])
def test_drift_or_invalid_rejected(field, value):
    before = snapshot()
    after = dict(before, **{field:value})
    assert not observation.compare_window(before, after)["lifecycle_stable"]


def test_invalid_cli_output(monkeypatch):
    monkeypatch.setattr(observation, "_bounded_output", lambda *args,**kwargs: '{}')
    with pytest.raises(ValueError): observation.collect()


def test_cli_timeout_propagates(monkeypatch):
    def run(*args, **kwargs): raise subprocess.TimeoutExpired("docker",10)
    monkeypatch.setattr(observation, "_bounded_output", run)
    with pytest.raises(subprocess.TimeoutExpired): observation.collect()


@pytest.mark.parametrize("stamp", ["restart", "2026-02-30T00:00:00Z",
    "2026-10-02T25:00:00Z", "2026-10-02T00:00:00", "2026-10-02T00:00:00+08:00",
    "0001-01-01T00:00:00Z", True])
def test_invalid_start_time_rejected(stamp):
    item = dict(snapshot(), started_at=stamp)
    assert not observation.compare_window(item, item)["lifecycle_stable"]


def test_real_docker_nanosecond_timestamp_accepted():
    item = dict(snapshot(), started_at="2026-10-01T16:45:19.723653576Z")
    assert observation.compare_window(item, item)["lifecycle_stable"]


def test_duplicate_json_keys_rejected(monkeypatch):
    raw = json.dumps(snapshot())[:-1] + ',"running":true}'
    monkeypatch.setattr(observation, "_bounded_output",
                        lambda *args, **kwargs: raw)
    with pytest.raises(ValueError, match="duplicate_observation_key"):
        observation.collect()


@pytest.mark.parametrize("size", [0, 2048])
def test_real_process_output_boundary(size):
    command = [sys.executable, "-c", f"import os; os.write(1,b'x'*{size})"]
    assert observation._bounded_output(command, timeout=2) == "x" * size


@pytest.mark.parametrize("size", [2049, 500000])
def test_real_process_excess_output_stops(size):
    command = [sys.executable, "-c", f"import os; os.write(1,b'x'*{size})"]
    with pytest.raises(ValueError, match="oversize_container_observation"):
        observation._bounded_output(command, timeout=2)


def test_real_process_stderr_not_retained():
    command = [sys.executable, "-c", "import os; os.write(2,b'x'*500000); os.write(1,b'ok')"]
    assert observation._bounded_output(command, timeout=2) == "ok"


def test_real_process_nonzero_exit():
    with pytest.raises(subprocess.CalledProcessError) as error:
        observation._bounded_output([sys.executable, "-c", "raise SystemExit(7)"], timeout=2)
    assert error.value.returncode == 7 and error.value.stderr is None


@pytest.mark.parametrize("script", ["import time; time.sleep(10)",
    "import os,time; p=os.fork(); time.sleep(10) if p==0 else os._exit(0)"])
def test_real_process_timeout_and_inherited_pipe(script):
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        observation._bounded_output([sys.executable, "-c", script], timeout=0.2)
    assert time.monotonic() - started < 2


def test_real_process_invalid_utf8_rejected():
    with pytest.raises(UnicodeDecodeError):
        observation._bounded_output([sys.executable, "-c", "import os; os.write(1,b'\\xff')"], timeout=2)
