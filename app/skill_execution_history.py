"""Read-only verification of a new fixed-recipe daily execution.

This never admits a source, consumes a reference, or authenticates an end user.
Source intake still uses its separate pinned timeout/source contract.
"""
from app import skill_local_profile as local
from scripts.validation.host_forward_signature import canonical

def checked_daily_history(run_id):
    # Daily runtime uses its configured 15s request; source intake's pinned 30s
    # contract remains unchanged. This observer confers no admission authority.
    import sqlite3
    from pathlib import Path
    from app import dashboard,executor_evidence
    from scripts.validation.periodic_eigenmodes import validate
    with sqlite3.connect(Path(dashboard.DB_PATH).resolve().as_uri()+"?mode=ro",uri=True,timeout=1) as db:
        db.row_factory=sqlite3.Row;db.set_progress_handler(lambda:1,1000000)
        rows=db.execute("""SELECT record_version,substr(code,1,20001) code,code_sha256,
            exit_code,timed_out,substr(stdout,1,500001) stdout,substr(stderr,1,100001) stderr,
            substr(executor_evidence_json,1,16001) evidence FROM executions WHERE run_id=? LIMIT 2""",(run_id,)).fetchall()
    if len(rows)!=1:raise ValueError("history_ambiguous")
    row=rows[0]
    if row["record_version"]!="gateway-execution.v1" or row["exit_code"]!=0 or row["timed_out"]!=0 or row["stderr"]!="":
        raise ValueError("history_execution_failed")
    code=row["code"];raw=row["stdout"]
    if len(code.encode())>20000 or len(raw.encode())>500000 or len(row["evidence"].encode())>16000:
        raise ValueError("history_budget")
    evidence=local._json(row["evidence"]);dispatch=evidence["local_dispatch"]
    timeout=dispatch["timeout_seconds"];nonce=dispatch["request_nonce"]
    if evidence.get("gateway_timeout_seconds",timeout)!=timeout or evidence.get("gateway_request_nonce",nonce)!=nonce:
        raise ValueError("daily_gateway_dispatch_mismatch")
    if type(timeout)is not int or not 1<=timeout<=30:raise ValueError("daily_timeout")
    spec=local.catalog.from_raw(local._json(raw))
    if code!=local._recipe(spec["scope"]) or row["code_sha256"]!=spec["code_sha256"]:raise ValueError("history_code_changed")
    request=canonical(dict(code=code,timeout_seconds=timeout,request_nonce=nonce)).decode()
    sha=local.pins.digest(request)
    expected=dict(version="local-dispatch.v1",source_kind="managed_executor_transport",
        request_sha256=sha,code_sha256=local.pins.digest(code),request_nonce=nonce,timeout_seconds=timeout,
        target_url="http://jwave-executor:8010/execute",trust_boundary="gateway_and_internal_executor",
        independent_host_authenticated=False,receipt_correlated=False)
    receipt=evidence.get("local_transport_receipt")
    if receipt is not None:
        if dispatch!=expected:raise ValueError("daily_dispatch_changed")
        required=dict(version="gateway-transport-receipt.v1",request_sha256=sha,request_nonce=nonce,
            stdout_sha256=local.pins.digest(raw),stderr_sha256=local.pins.digest(""),exit_code=0,timed_out=False,
            executor_receipt_absent=True,authentication_configured=True,independent_host_authenticated=False)
        if receipt!=required:raise ValueError("daily_receipt_changed")
    else:
        expected["receipt_correlated"]=True
        if dispatch!=expected:raise ValueError("daily_dispatch_changed")
        if executor_evidence.verify_executor_receipt(evidence["receipt"],
            dict(stdout=raw,stderr="",exit_code=0,timed_out=False),code,timeout,nonce)["matched"]is not True:
            raise ValueError("daily_executor_receipt")
    if spec["scope"]==local.PROFILE:
        numeric=local.validate_plane_wave(local._json(raw))
    elif spec["scope"] in local.catalog.PERIODIC_RECIPES:
        numeric=validate(local._json(raw))
    else:
        from scripts.validation.interface_waveform import validate_wide
        numeric=validate_wide(raw)
    if numeric.get("numeric_pass")is not True:raise ValueError("daily_physics")
    return dict(stdout=raw),code,numeric
