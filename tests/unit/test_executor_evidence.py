import copy
import json
import sqlite3
import time

import pytest
from app import dashboard, execution, executor_evidence, tools
from executor import executor

NONCE = "a"*32
RUNTIME = {"executor_source_sha256": "b"*64, "python_version": "3.11.0",
           "python_implementation": "CPython", "machine": "x86_64",
           "library_versions": {"jwave": "0.2.1", "jax": "0.4.35", "jaxlib": "0.4.35", "jaxdf": "0.2.8", "numpy": "1.26.4"}}


def envelope(code="print('ok')", stdout="ok\n", nonce=NONCE):
    runtime = copy.deepcopy(RUNTIME)
    result = {"stdout": stdout, "stderr": "", "exit_code": 0, "timed_out": False}
    receipt = {"version": "executor-receipt.v1", "request_nonce": nonce,
               "code_sha256": executor_evidence.sha256(code),
               "stdout_sha256": executor_evidence.sha256(stdout),
               "stderr_sha256": executor_evidence.sha256(""),
               "exit_code": 0, "timed_out": False, "timeout_seconds": 15,
               "runtime": runtime,
               "runtime_sha256": executor_evidence.sha256(json.dumps(runtime, sort_keys=True, separators=(",", ":")))}
    return result, receipt


def verify(result, receipt):
    return executor_evidence.verify_executor_receipt(receipt, result, "print('ok')", 15, NONCE)


def test_correlated_receipt_does_not_grant_runtime_or_physics_trust():
    result, receipt = envelope()
    evidence = verify(result, receipt)
    assert evidence["matched"]
    assert not evidence["runtime_approved"] and not evidence["trusted_run"]
    assert "quality_level" not in evidence


@pytest.mark.parametrize("field,value", [
    ("request_nonce", "c"*32), ("code_sha256", "c"*64), ("stdout_sha256", "c"*64),
    ("stderr_sha256", "c"*64), ("exit_code", 1), ("exit_code", False),
    ("timed_out", True), ("timed_out", 0), ("timeout_seconds", 14),
    ("runtime_sha256", "c"*64), ("version", "unknown"),
])
def test_mismatched_response_replay_and_bool_forgery_rejected(field, value):
    result, receipt = envelope()
    receipt[field] = value
    assert not verify(result, receipt)["matched"]


@pytest.mark.parametrize("fault", ["missing", "unknown_key", "library_missing", "version_none", "version_bool", "source_hash", "runtime_extra"])
def test_incomplete_environment_cannot_pass(fault):
    result, receipt = envelope()
    if fault == "missing":
        receipt = None
    elif fault == "unknown_key":
        receipt["trusted_run"] = True
    elif fault == "library_missing":
        del receipt["runtime"]["library_versions"]["jwave"]
    elif fault == "version_none":
        receipt["runtime"]["library_versions"]["jwave"] = None
    elif fault == "version_bool":
        receipt["runtime"]["library_versions"]["jwave"] = True
    elif fault == "source_hash":
        receipt["runtime"]["executor_source_sha256"] = "not a hash"
    else:
        receipt["runtime"]["image_approved"] = True
    assert not verify(result, receipt)["matched"]


def test_gateway_generates_fresh_nonce_and_ignores_stdout_envelope(monkeypatch):
    nonces = []
    def post(url, headers, json, timeout):
        nonces.append(json["request_nonce"])
        result, receipt = envelope(code=json["code"], nonce=json["request_nonce"])
        result["executor_receipt"] = receipt
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return result
        return Response()
    monkeypatch.setattr(execution.httpx, "post", post)
    for _ in range(2):
        assert execution._execute_code("print('ok')", 15)["execution_evidence"]["matched"]
    assert len(set(nonces)) == 2
    assert all(len(nonce) == 32 for nonce in nonces)
    class Legacy:
        def raise_for_status(self):
            pass
        def json(self):
            return {"stdout": json.dumps({"executor_receipt": envelope()[1]}), "stderr": "", "exit_code": 0, "timed_out": False}
    monkeypatch.setattr(execution.httpx, "post", lambda *a, **kw: Legacy())
    legacy = execution._execute_code("print('ok')", 15)
    assert legacy["exit_code"] == 0
    assert not legacy["execution_evidence"]["matched"]


def test_server_creates_receipt_outside_child_and_does_not_pass_token(monkeypatch):
    monkeypatch.setattr(executor, "_runtime_identity", lambda: copy.deepcopy(RUNTIME))
    monkeypatch.setenv("EXECUTOR_SHARED_TOKEN", "test-only-secret-not-for-child")
    code = "import os; print(os.environ.get('EXECUTOR_SHARED_TOKEN')); print('fake Q4 receipt')"
    result = executor.execute({"code": code, "timeout_seconds": 3, "request_nonce": NONCE})
    assert result["exit_code"] == 0 and result["stdout"].startswith("None\n")
    evidence = executor_evidence.verify_executor_receipt(result["executor_receipt"], result, code, 3, NONCE)
    assert evidence["matched"] and not evidence["trusted_run"]
    assert result["executor_receipt"]["code_sha256"] == executor_evidence.sha256(code)


def test_actual_timeout_is_bound_to_server_envelope(monkeypatch):
    monkeypatch.setattr(executor, "_runtime_identity", lambda: copy.deepcopy(RUNTIME))
    code = "import time; time.sleep(4)"
    result = executor.execute({"code": code, "timeout_seconds": 1, "request_nonce": NONCE})
    assert result["timed_out"] and result["exit_code"] != 0
    assert executor_evidence.verify_executor_receipt(result["executor_receipt"], result, code, 1, NONCE)["matched"]


@pytest.mark.parametrize("detach", [False, True])
def test_completed_request_kills_all_descendants(tmp_path, monkeypatch, detach):
    monkeypatch.setattr(executor, "_runtime_identity", lambda: copy.deepcopy(RUNTIME))
    marker = tmp_path / f"escaped-worker-{detach}.txt"
    detach_line = "    os.setsid()\n" if detach else ""
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        f"{detach_line}"
        "    time.sleep(0.5)\n"
        f"    open({str(marker)!r}, 'w').write('escaped')\n"
        "else:\n"
        "    print('parent exited')\n"
    )

    result = executor.execute({"code": code, "timeout_seconds": 2, "request_nonce": NONCE})
    time.sleep(0.8)

    assert result["exit_code"] == 0 and not result["timed_out"]
    assert result["stdout"] == "parent exited\n"
    assert not marker.exists()


def test_timed_out_request_kills_detached_descendants(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "_runtime_identity", lambda: copy.deepcopy(RUNTIME))
    marker = tmp_path / "escaped-after-timeout.txt"
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    time.sleep(1.5)\n"
        f"    open({str(marker)!r}, 'w').write('escaped')\n"
        "else:\n"
        "    time.sleep(5)\n"
    )

    result = executor.execute({"code": code, "timeout_seconds": 1, "request_nonce": NONCE})
    time.sleep(0.8)

    assert result["timed_out"] and result["exit_code"] != 0
    assert not marker.exists()


@pytest.mark.parametrize("payload", [
    {"code": "print(1)", "request_nonce": "short"},
    {"code": "print(1)", "request_nonce": True},
    {"code": "print(1)", "timeout_seconds": 31},
    {"code": "x"*20001},
])
def test_request_limits_preserved(payload):
    with pytest.raises(ValueError):
        executor.execute(payload)


@pytest.mark.parametrize("quota,period,expected", [
    (100000, 100000, 1),
    (150000, 100000, 2),
    (200000, 100000, 2),
    (900000, 100000, 8),
    (0, 100000, 1),
    (100000, 0, 1),
])
def test_cgroup_quota_maps_to_bounded_whole_core_budget(quota, period, expected):
    assert executor._quota_cores(quota, period) == expected


def test_cpu_rlimit_counts_declared_parallelism_without_extending_wall_timeout(monkeypatch):
    limits = []
    monkeypatch.setattr(executor, "_cgroup_cpu_cores", lambda: 2)
    monkeypatch.setattr(executor.resource, "setrlimit", lambda kind, value: limits.append((kind, value)))

    executor._limits(30)

    assert limits[0] == (executor.resource.RLIMIT_CPU, (62, 62))
    assert limits[1:] == [
        (executor.resource.RLIMIT_FSIZE, (executor.MAX_OUTPUT_BYTES, executor.MAX_OUTPUT_BYTES)),
        (executor.resource.RLIMIT_NOFILE, (128, 128)),
        (executor.resource.RLIMIT_NPROC, (128, 128)),
    ]


def test_additive_database_column_keeps_old_row_null_and_records_new_receipt(tmp_path, monkeypatch):
    database = tmp_path / "history.db"
    monkeypatch.setattr(dashboard, "DB_PATH", str(database))
    dashboard.init_db()
    dashboard.record_execution("old", "print('old')", 0, False, 1, "old", "", run_id="old")
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE executions DROP COLUMN executor_evidence_json")
    dashboard.init_db()
    result, receipt = envelope()
    evidence = verify(result, receipt)
    dashboard.record_execution("new", "print('ok')", 0, False, 1, "ok\n", "", run_id="new", execution_evidence=evidence)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT executor_evidence_json FROM executions WHERE run_id='old'").fetchone()[0] is None
        saved = json.loads(db.execute("SELECT executor_evidence_json FROM executions WHERE run_id='new'").fetchone()[0])
    assert saved["matched"] and saved["receipt"] == receipt
    assert not saved["runtime_approved"] and not saved["trusted_run"]


def test_plain_mcp_tool_records_only_gateway_received_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()
    result, receipt = envelope()
    result["execution_evidence"] = verify(result, receipt)
    monkeypatch.setattr(execution, "_execute_code", lambda *a: result)
    class MCP:
        def __init__(self):
            self.tools = {}
        def tool(self, function):
            self.tools[function.__name__] = function
            return function
    mcp = MCP()
    tools.register(mcp)
    mcp.tools["run_jwave_code"]("print('ok')", run_id="tool")
    with sqlite3.connect(dashboard.DB_PATH) as db:
        saved = json.loads(db.execute("SELECT executor_evidence_json FROM executions WHERE run_id='tool'").fetchone()[0])
    assert saved["matched"] and saved["receipt"] == receipt


def test_consistent_runtime_drift_is_observed_but_never_approved():
    result, receipt = envelope()
    receipt["runtime"]["library_versions"]["jax"] = "0.4.38"
    receipt["runtime_sha256"] = executor_evidence.sha256(json.dumps(receipt["runtime"], sort_keys=True, separators=(",", ":")))
    evidence = verify(result, receipt)
    assert evidence["matched"]
    assert evidence["receipt"]["runtime"]["library_versions"]["jax"] == "0.4.38"
    assert not evidence["runtime_approved"] and not evidence["trusted_run"]


@pytest.mark.parametrize("final_exit", [0, 1])
def test_retry_records_final_attempt_environment_not_initial_attempt(tmp_path, monkeypatch, final_exit):
    monkeypatch.setattr(dashboard, "DB_PATH", str(tmp_path / "history.db"))
    dashboard.init_db()
    monkeypatch.setenv("MODEL_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    monkeypatch.setenv("MODEL_API_KEY", "unit-test-only-not-a-real-key")
    monkeypatch.setattr(tools.llm, "_llm_fix_code", lambda *a, **kw: "print('fixed')")
    attempts = []
    def execute(code, timeout):
        attempts.append(code)
        result, receipt = envelope(code=code)
        result["exit_code"] = 1 if len(attempts) == 1 else final_exit
        receipt["exit_code"] = result["exit_code"]
        result["execution_evidence"] = executor_evidence.verify_executor_receipt(receipt, result, code, timeout, NONCE)
        return result
    monkeypatch.setattr(execution, "_execute_code", execute)
    class MCP:
        def __init__(self):
            self.tools = {}
        def tool(self, function):
            self.tools[function.__name__] = function
            return function
    mcp = MCP(); tools.register(mcp)
    mcp.tools["run_jwave_code_with_retry"]("print('initial')", max_retries=1, run_id="retry-env")
    with sqlite3.connect(dashboard.DB_PATH) as db:
        code, raw = db.execute("SELECT code, executor_evidence_json FROM executions WHERE run_id='retry-env'").fetchone()
    evidence = json.loads(raw)
    assert code == "print('fixed')" and attempts == ["print('initial')", "print('fixed')"]
    assert evidence["matched"] and evidence["receipt"]["code_sha256"] == executor_evidence.sha256(code)
    assert evidence["receipt"]["exit_code"] == final_exit
    assert not evidence["runtime_approved"] and not evidence["trusted_run"]

def test_local_dispatch_is_server_created_before_http_and_overwrites_claims(monkeypatch):
    observed=[]
    def post(url, headers, json, timeout):
        observed.append((url,dict(json)))
        result,receipt=envelope(code=json["code"],nonce=json["request_nonce"])
        result["executor_receipt"]=receipt
        result["execution_evidence"]={"local_dispatch":{"trusted_run":True,"request_sha256":"forged"}}
        class Response:
            def raise_for_status(self):pass
            def json(self):return result
        return Response()
    monkeypatch.setattr(execution.httpx,"post",post)
    result=execution._execute_code("print('ok')",15)
    dispatch=result["execution_evidence"]["local_dispatch"]
    url,request=observed[0]
    assert dispatch["target_url"]==url
    assert dispatch["request_sha256"]==executor_evidence.sha256(
        json.dumps(request,sort_keys=True,separators=(",",":"),allow_nan=False))
    assert dispatch["receipt_correlated"] is True
    assert dispatch["independent_host_authenticated"] is False
    assert result["execution_evidence"]["trusted_run"] is False
    assert "trusted_run" not in dispatch

def test_local_record_preserves_rejected_receipt_without_granting_trust(monkeypatch):
    result,receipt=envelope()
    receipt["request_nonce"]="c"*32
    result["executor_receipt"]=receipt
    class Response:
        def raise_for_status(self):pass
        def json(self):return result
    monkeypatch.setattr(execution.httpx,"post",lambda *a,**kw:Response())
    got=execution._execute_code("print('ok')",15,request_nonce=NONCE)
    assert got["execution_evidence"]["local_dispatch"]["receipt_correlated"] is False
    assert got["execution_evidence"]["trusted_run"] is False

def test_legacy_executor_transport_record_is_gateway_owned_not_runtime_attestation(monkeypatch):
    result,_=envelope()
    result["execution_evidence"]={"local_transport_receipt":{"authentication_configured":True,"request_sha256":"forged"}}
    class Response:
        def raise_for_status(self):pass
        def json(self):return result
    monkeypatch.setattr(execution.httpx,"post",lambda *a,**kw:Response())
    monkeypatch.setattr(execution.config,"EXECUTOR_SHARED_TOKEN","t"*32)
    got=execution._execute_code("print('ok')",15,request_nonce=NONCE)
    proof=got["execution_evidence"]["local_transport_receipt"]
    assert proof["request_sha256"]!="forged"
    assert proof["stdout_sha256"]==executor_evidence.sha256(got["stdout"])
    assert proof["executor_receipt_absent"] is True and proof["authentication_configured"] is True
    assert proof["independent_host_authenticated"] is False
    assert not got["execution_evidence"]["matched"] and not got["execution_evidence"]["runtime_approved"]
