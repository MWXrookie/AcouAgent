import hashlib
import json
import sqlite3
import pytest
from app import config,skill_reference_registry as registry,skill_retrieval
from host_observer.authority_signing import skill_reference_policy
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,root

@pytest.fixture
def proof(available):
    store,key,call,payload=available
    policy=skill_reference_policy(key,skill_id=call["skill_id"],version=1,
        parameters_sha256=payload["parameters_sha256"],observation_sha256=payload["observation_sha256"],
        revision=1,valid_from_ns=90_000_000_000,valid_until_ns=180_000_000_000)
    evidence={k:call[k] for k in ("skill_id","version","observation_document","stdout","stderr")}
    evidence["approval_document"]=policy
    return store,key,call,evidence

def test_registered_public_evidence_is_found_without_manual_generation_proof(proof):
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    assert registry.register(evidence)["duplicate"]
    assert registry.find("plane_wave_2d",call["parameters_json"])==evidence
    result=skill_retrieval.generation_context("automatic",scenario_type="plane_wave_2d",
                                            parameters_json=call["parameters_json"])
    assert result["context"].startswith("automatic\n") and not result["skill_usage"]["fallback"]
    assert not result["skill_usage"]["actual_use_authenticated"]
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_reference_capsules").fetchone()[0]==1

@pytest.mark.parametrize("mutation",["stderr","secret","extra_raw","status","nonfinite","policy","candidate","revoked"])
def test_unsafe_or_unapproved_capsules_never_persist(proof,mutation):
    store,key,call,evidence=proof
    if mutation=="stderr":evidence["stderr"]="debug logs"
    elif mutation in {"secret","extra_raw","status","nonfinite"}:
        raw=json.loads(evidence["stdout"])
        if mutation=="secret":raw["token"]="hidden"
        elif mutation=="extra_raw":raw["raw_evidence"]["note"]="arbitrary"
        elif mutation=="status":raw["status"]="API_TOKEN"
        else:raw["theory"]["peak"]=float("nan")
        evidence["stdout"]=json.dumps(raw)
    elif mutation=="policy":evidence["approval_document"]=call["approval_document"]
    elif mutation=="candidate":
        with store._db_existing_write() as db:
            db.execute("UPDATE simulation_skills SET status='candidate' WHERE skill_id=?",(call["skill_id"],))
    elif mutation=="revoked":
        assert registry.consumer.authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    assert not registry.register(evidence)["recorded"]
    with store._read_db() as db:assert db.execute("SELECT count(*) FROM skill_reference_capsules").fetchone()[0]==0

def test_capsule_append_only_and_registration_rolls_back(proof):
    store,key,call,evidence=proof
    with store._db_existing_write() as db:
        db.execute("CREATE TRIGGER fault BEFORE INSERT ON skill_reference_capsules BEGIN SELECT RAISE(ABORT,'fault'); END")
    assert not registry.register(evidence)["recorded"]
    with store._db_existing_write() as db:db.execute("DROP TRIGGER fault")
    assert registry.register(evidence)["recorded"]
    with store._db_existing_write() as db:
        row=db.execute("SELECT * FROM skill_reference_capsules").fetchone()
        for sql in ("DELETE FROM skill_reference_capsules","UPDATE skill_reference_capsules SET valid_until_ns=1"):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT OR REPLACE INTO skill_reference_capsules VALUES (?,?,?,?,?,?,?,?,?)",tuple(row))

def test_default_off_and_revocation_fail_closed_without_returning_code(proof,monkeypatch):
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    assert registry.consumer.authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    result=skill_retrieval.generation_context("revoked",scenario_type="plane_wave_2d",
                                            parameters_json=call["parameters_json"])
    assert result["context"]=="revoked" and result["skill_usage"]["skills"]==[]
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
    assert registry.find("plane_wave_2d",call["parameters_json"]) is None
    assert not registry.register(evidence)["recorded"]

def test_schema10_upgrade_is_atomic_and_preserves_history(proof):
    store,key,call,evidence=proof
    with sqlite3.connect(store.path) as db:
        original=db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
        db.execute("DROP TABLE skill_local_reference_completions")
        db.execute("DROP TABLE skill_local_reference_dispatches")
        db.execute("DELETE FROM schema_migrations WHERE version=17")
        db.execute("DROP TABLE skill_local_sources")
        db.execute("DROP TABLE skill_reference_executions")
        db.execute("DROP TABLE skill_reference_dispatches")
        db.execute("DROP TABLE skill_reference_capsules")
        db.execute("DROP INDEX idx_candidate_source_version")
        db.execute("DROP INDEX idx_candidate_source_observation")
        db.execute("DROP INDEX idx_skill_event_history")
        db.execute("DROP INDEX idx_skill_revalidation_epoch")
        db.execute("DROP INDEX idx_skill_dispatch_batch_case")
        db.execute("DELETE FROM schema_migrations WHERE version>=11")
        db.execute("CREATE INDEX idx_reference_capsule_lookup ON skill_events(created_at)")
    with pytest.raises(sqlite3.OperationalError):store.migrate()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==10
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_reference_capsules'").fetchone()
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==original
        db.execute("DROP INDEX idx_reference_capsule_lookup")
    store.migrate()
    assert registry.register(evidence)["recorded"]
