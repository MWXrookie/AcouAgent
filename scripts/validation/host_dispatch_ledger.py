"""Internal temporary dispatch ledger; no public API or production root grant."""
import hashlib
import ipaddress
import json
import re
import time
import uuid
from scripts.validation import host_consumption_prototype as ledger
from scripts.validation import host_forward_signature as signature
from scripts.validation import host_forward_binding as binding
from scripts.validation import executor_host_observation as docker

def initialize_test_dispatches(path):
    with ledger._db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE test_host_dispatches(
            run_id TEXT PRIMARY KEY,nonce TEXT UNIQUE,context_json TEXT NOT NULL,
            context_sha256 TEXT NOT NULL,created_ns INTEGER NOT NULL)""")
        for operation in ("UPDATE","DELETE"):
            db.execute(f"""CREATE TRIGGER test_dispatch_no_{operation.lower()}
                BEFORE {operation} ON test_host_dispatches BEGIN
                SELECT RAISE(ABORT,'append-only'); END""")
        db.execute("""CREATE TRIGGER test_dispatch_no_replace BEFORE INSERT ON test_host_dispatches
            WHEN EXISTS(SELECT 1 FROM test_host_dispatches WHERE run_id=NEW.run_id OR nonce=NEW.nonce)
            BEGIN SELECT RAISE(ABORT,'append-only'); END""")

def record_test_dispatch(path,context):
    # Operator/server context is recorded before forwarding, never copied from
    # an uploaded observation. now_ns is supplied by this process, not the input.
    if type(context) is not dict or set(context)!=binding.CONTEXT-{"now_ns","registered_ns"}:
        raise ValueError("invalid_dispatch_context")
    if (type(context["root_id"]) is not str or not re.fullmatch("[A-Za-z0-9_-]{1,64}",context["root_id"])
            or type(context["revision"]) is not int or context["revision"]<1
            or type(context["run_id"]) is not str or str(uuid.UUID(context["run_id"]))!=context["run_id"]
            or type(context["nonce"]) is not str or not re.fullmatch("[0-9a-f]{32}",context["nonce"])
            or type(context["target_port"]) is not int or not 1<=context["target_port"]<=65535):
        raise ValueError("invalid_dispatch_context")
    ip=ipaddress.IPv4Address(context["target_ip"])
    identity={k:context[k] for k in ("container_id","image_id","started_at")}
    identity.update(running=True,restart_count=0)
    if (not docker._valid(identity) or not(ip.is_private or ip.is_loopback)
            or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
        raise ValueError("invalid_dispatch_target")
    first,last=context["dispatch_not_before_ns"],context["dispatch_not_after_ns"]
    now=time.time_ns()
    if (type(first) is not int or type(last) is not int or not 0<=first<=now<=last<=2**63-1
            or last-first>90_000_000_000):
        raise ValueError("invalid_dispatch_window")
    raw=signature.canonical(context)
    if len(raw)>2048:raise ValueError("dispatch_budget")
    with ledger._db(path) as db:
        db.execute("BEGIN IMMEDIATE")
        root=db.execute("SELECT revision,state FROM roots WHERE id=? ORDER BY revision DESC LIMIT 1",(context["root_id"],)).fetchone()
        if not root or root!=(context["revision"],"test-active"):
            raise ValueError("unavailable_test_root")
        db.execute("INSERT INTO test_host_dispatches VALUES(?,?,?,?,?)",
            (context["run_id"],context["nonce"],raw.decode(),hashlib.sha256(raw).hexdigest(),now))

def inspect_test_dispatch(db,run_id,nonce,now_ns):
    if not db.in_transaction:return None
    row=db.execute("SELECT context_json,context_sha256,created_ns FROM test_host_dispatches WHERE run_id=? AND nonce=?",(run_id,nonce)).fetchone()
    if row is None:return None
    raw=row[0]
    if type(raw) is not str or len(raw.encode())>2048 or hashlib.sha256(raw.encode()).hexdigest()!=row[1]:
        return None
    context=json.loads(raw,object_pairs_hook=signature.unique,parse_constant=signature.reject)
    if (type(context) is not dict or set(context)!=binding.CONTEXT-{"now_ns","registered_ns"}
            or context["run_id"]!=run_id or context["nonce"]!=nonce
            or signature.canonical(context).decode()!=raw):
        return None
    return dict(context,now_ns=now_ns,registered_ns=row[2])
