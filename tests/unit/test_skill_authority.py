import hashlib
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from app import config,skill_library_service as service,skill_authority as authority
from app.skill_store import SkillStore
from tests.unit.test_host_forwarder import IDENTITY

@pytest.fixture
def prepared(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"skills.db");store.migrate()
    monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    monkeypatch.setattr(authority.approved,"approved_public_key",lambda:public)
    monkeypatch.setattr(authority.time,"time_ns",lambda:100_000_000_000)
    return store,key

def signed(key,payload):
    return json.dumps({"payload":payload,"signature":key.sign(authority.DOMAIN+authority.canonical(payload)).hex()})

def root(key,revision=1,action="approve",**changes):
    body=dict(purpose="root_lifecycle",root_id=authority.approved.ROOT_ID,revision=revision,
              action=action,profile=authority.approved.PROFILE,valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000)
    body.update(changes);return signed(key,body)

def context():
    return dict(root_id=authority.approved.ROOT_ID,revision=1,run_id=str(uuid.uuid4()),nonce=uuid.uuid4().hex,
                target_ip="127.0.0.1",target_port=8010,
                container_id=IDENTITY["container_id"],image_id=IDENTITY["image_id"],started_at=IDENTITY["started_at"],
                dispatch_not_before_ns=99_000_000_000,dispatch_not_after_ns=159_000_000_000)

def approval(key,ctx,**changes):
    body=dict(purpose="approve_run",root_id=ctx["root_id"],revision=ctx["revision"],
              profile=authority.approved.PROFILE,run_id=ctx["run_id"],nonce=ctx["nonce"],
              context_sha256=hashlib.sha256(authority.canonical(ctx)).hexdigest())
    body.update(authority.fixed_scope());body.update(changes);return signed(key,body)

def test_owner_root_and_run_are_separate_and_same_db_rechecked(prepared):
    store,key=prepared;ctx=context()
    assert not authority.record_dispatch(ctx,approval(key,ctx))["recorded"]
    assert authority.record_root_statement(root(key))["recorded"]
    assert authority.record_dispatch(ctx,approval(key,ctx))["recorded"]
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        found=authority.inspect_connection(db,ctx["run_id"],ctx["nonce"],100_000_000_000)
        assert found["registered_ns"]==100_000_000_000
    assert authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    with store._db_existing_write() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError,match="revision"):authority.inspect_connection(db,ctx["run_id"],ctx["nonce"],100_000_000_000)
    assert not authority.record_root_statement(root(key,3))["recorded"]

@pytest.mark.parametrize("changes",[
    {"revision":2},{"action":"active"},{"valid_from_ns":True},{"valid_until_ns":100_000_000_000},
    {"valid_until_ns":200_000_000_000_000},{"runtime_approved":True},{"profile":"generic"}])
def test_root_signature_does_not_bypass_policy(prepared,changes):
    store,key=prepared
    assert not authority.record_root_statement(root(key,**changes))["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_authority_roots").fetchone()[0]==0

def test_wrong_root_and_signature_scope_rejected(prepared):
    store,key=prepared
    assert not authority.record_root_statement(root(Ed25519PrivateKey.generate()))["recorded"]
    doc=json.loads(root(key));doc["payload"]["valid_until_ns"]+=1
    assert not authority.record_root_statement(json.dumps(doc))["recorded"]
    assert not authority.record_root_statement(approval(key,context()))["recorded"]

@pytest.mark.parametrize("field,value",[
    ("target_port",True),("target_ip","8.8.8.8"),("nonce","0"),("revision",True),
    ("dispatch_not_after_ns",200_000_000_000),("dispatch_not_before_ns",101_000_000_000)])
def test_signed_context_still_requires_target_and_window(prepared,field,value):
    store,key=prepared;assert authority.record_root_statement(root(key))["recorded"]
    ctx=context();ctx[field]=value
    assert not authority.record_dispatch(ctx,approval(key,ctx))["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==0

def test_expiry_and_context_drift_refuse_without_registration(prepared,monkeypatch):
    store,key=prepared;assert authority.record_root_statement(root(key))["recorded"]
    ctx=context();doc=approval(key,ctx);ctx["target_port"]+=1
    assert not authority.record_dispatch(ctx,doc)["recorded"]
    monkeypatch.setattr(authority.time,"time_ns",lambda:180_000_000_000)
    assert not authority.record_dispatch(context(),approval(key,context()))["recorded"]

def test_dispatch_concurrent_once_and_append_only(prepared):
    store,key=prepared;assert authority.record_root_statement(root(key))["recorded"]
    ctx=context();doc=approval(key,ctx)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:authority.record_dispatch(ctx,doc),range(2)))
    assert sum(r["recorded"] for r in results)==1
    assert all(not r[k] for r in results for k in authority.TRUST)
    with store._db_existing_write() as db:
        for table in ("skill_authority_roots","skill_host_dispatches"):
            with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM "+table)
        row=db.execute("SELECT * FROM skill_host_dispatches").fetchone()
        values=list(row);values[0]=str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT OR REPLACE INTO skill_host_dispatches VALUES (?,?,?,?,?,?,?,?)",values)

def test_dispatch_fault_rolls_back_and_can_retry(prepared):
    store,key=prepared;assert authority.record_root_statement(root(key))["recorded"]
    ctx=context();doc=approval(key,ctx)
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON skill_host_dispatches BEGIN SELECT RAISE(ABORT,'injected'); END")
    assert not authority.record_dispatch(ctx,doc)["recorded"]
    with store._db_existing_write() as db:
        assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==0
        db.execute("DROP TRIGGER injected")
    assert authority.record_dispatch(ctx,doc)["recorded"]

def test_disabled_or_missing_never_creates_database(tmp_path,monkeypatch):
    store=SkillStore(tmp_path/"missing.db");monkeypatch.setattr(service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",False)
    assert not authority.record_root_statement("invalid")["recorded"] and not store.path.exists()
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    key=Ed25519PrivateKey.generate()
    assert not authority.record_root_statement(root(key))["recorded"] and not store.path.exists()

def test_host_signer_requires_explicit_policy_and_never_enrolls(prepared):
    from host_observer import authority_signing as signing
    store,key=prepared
    with pytest.raises(ValueError):
        signing.root_policy(key,revision=1,action="approve",valid_from_ns=90_000_000_000,valid_until_ns=None)
    doc=signing.root_policy(key,revision=1,action="approve",
                           valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000)
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_authority_roots").fetchone()[0]==0
    assert authority.record_root_statement(doc)["recorded"]
    ctx=context();approval_doc=signing.dispatch_approval(key,context=ctx)
    assert authority.record_dispatch(ctx,approval_doc)["recorded"]
    with pytest.raises(ValueError,match="operator_key_not_approved"):
        signing.dispatch_approval(Ed25519PrivateKey.generate(),context=context())

@pytest.mark.parametrize("first,last",[(True,180_000_000_000),(0,200_000_000_000_000)])
def test_signed_imported_root_still_rechecks_policy_in_transaction(prepared,first,last):
    store,key=prepared
    document=root(key,valid_from_ns=first,valid_until_ns=last)
    # A valid signature alone must not make malformed/imported row policy valid.
    with store._db_existing_write() as db:
        db.execute("INSERT INTO skill_authority_roots VALUES (?,?,?,?,?,?)",
            (authority.approved.ROOT_ID,1,"approve",first,last,document))
    ctx=context()
    assert not authority.record_dispatch(ctx,approval(key,ctx))["recorded"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_host_dispatches").fetchone()[0]==0
