"""Controlled Helmholtz admission and reuse in the existing skill library.

Only server-built code, managed executor history and pinned independent numerical
references authorize this path. No caller grade, claimed hash or stdout PASS does.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import time
import uuid
from collections import OrderedDict
from functools import lru_cache
import threading
from datetime import datetime, timezone

from app import config, dashboard, execution, skill_general_profiles as profiles
from app.skill_drafts import DraftStore, DraftError, _revision
from app.skill_result_assets import ResultAssetStore
from app.skill_spec import prepare_skill_spec
from app.skill_validation_jobs import ValidationJobs, canonical, digest
from app.skill_validation_worker import read_unique_history, verify_recorded_attempt

VALIDATOR = "general-helmholtz.v1"
PREFIX = "\n\nValidated controlled Helmholtz reference (exact parameters and result contract):\n"
ERRORS = (ValueError, TypeError, KeyError, IndexError, UnicodeError, OverflowError,
          RecursionError, sqlite3.Error, OSError, TimeoutError)


def is_general_query(value):
    return type(value) is dict and value.get("normalizer_version") == "normalizer_v2"


def normalize_query(scenario, raw):
    from app.skill_retrieval import _unique, _nonfinite
    if type(raw) is not str or len(raw.encode("utf-8")) > 4096:
        raise ValueError("general_query_budget")
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
    if not is_general_query(value) or set(value) != {"normalizer_version", "parameters", "result_spec"}:
        raise ValueError("general_query_contract")
    spec = dict(schema_version="skill-spec.v1", name="Query", description="Exact query",
                scenario_type=scenario, parameters=value["parameters"], result_spec=value["result_spec"],
                code_template="pass\n", validation_request={"policy": "server_required"})
    prepared = prepare_skill_spec(canonical(spec))
    return dict(normalizer_version="normalizer_v2", parameters=prepared["spec"]["parameters"],
                result_spec=prepared["spec"]["result_spec"])


def _query_spec(scenario, envelope):
    query = normalize_query(scenario, canonical(envelope))
    return dict(schema_version="skill-spec.v1", name="Query", description="Exact query", scenario_type=scenario,
                parameters=query["parameters"], result_spec=query["result_spec"], code_template="pass\n",
                validation_request={"policy": "server_required"})


def query_fingerprint(scenario, envelope):
    return prepare_skill_spec(canonical(_query_spec(scenario, envelope)))["case_fingerprint"]


def enabled():
    return (config.SKILL_AUTHORING_ENABLED is True and config.SKILL_VALIDATION_ENABLED is True
            and config.SKILL_GENERAL_ADMISSION_ENABLED is True)


class GeneralLibrary:
    def __init__(self, store, assets, reference_root, runtime_sha256):
        self.store, self.assets = store, assets
        self.reference_root = Path(reference_root)
        self.runtime_sha256 = runtime_sha256
        self._preflights = OrderedDict()
        self._preflight_lock = threading.Lock()
        profiles.policy("B01", runtime_sha256)  # strict operator-selected runtime identity

    @staticmethod
    def _enabled():
        if not enabled():
            raise DraftError("general_admission_disabled")

    def save_controlled_draft(self, draft_id, *, actor_id, expected_revision, apply=False):
        """Preview the exact diff; explicit apply saves a new immutable version."""
        self._enabled(); _revision(expected_revision)
        drafts = DraftStore(self.store)
        original = drafts.get(draft_id, actor_id=actor_id)
        if original["revision"] != expected_revision:
            raise DraftError("revision_conflict")
        derived = profiles.build_controlled_spec(canonical(original["spec"]))
        if not apply or not derived["changed"]:
            return derived
        with drafts._connection(True) as db:
            head = drafts._head(db, draft_id, actor_id)
            if head["revision"] != expected_revision or head["state"] != "editing":
                raise DraftError("revision_conflict")
            version, now = head["current_version"] + 1, time.time_ns() // 1_000_000
            drafts._version(db, draft_id, version, derived, now)
            binding = digest(canonical({k: derived[k] for k in ("source_content_sha256", "derived_content_sha256",
                                                               "adapter_sha256", "construction_sha256")}))
            db.execute("INSERT INTO skill_draft_requests VALUES(?,?,?,?,?,?,?)",
                       (actor_id, "controlled-from-v" + str(head["current_version"]), derived["content_sha256"],
                        binding, draft_id, version, now))
            db.execute("UPDATE skill_drafts SET current_version=?,revision=revision+1,updated_ms=? WHERE draft_id=? AND revision=?",
                       (version, now, draft_id, expected_revision))
        return dict(derived, draft=drafts.get(draft_id, actor_id=actor_id))

    def install_profile(self, case):
        """Operator-only reference enrollment against immutable upstream byte pins."""
        self._enabled()
        policy = profiles.policy(case, self.runtime_sha256)
        path = self.reference_root / (case + ".npy")
        profiles.load_reference(case, path)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != policy["reference_sha256"]:
            raise ValueError("reference_changed")
        sha = policy["reference_sha256"]
        metadata = {"case": case, "kind": "pinned_independent_reference.v1"}
        with DraftStore(self.store)._connection() as db:
            old = db.execute("SELECT * FROM skill_validation_assets WHERE asset_sha256=?", (sha,)).fetchone()
        if old is None:
            self.assets._put(raw, metadata, "reference", "npy")
        with DraftStore(self.store)._connection(True) as db:
            row = db.execute("SELECT * FROM skill_validation_assets WHERE asset_sha256=?", (sha,)).fetchone()
            if (row is None or row["role"] != "reference" or json.loads(row["metadata_json"]) != metadata
                    or self.assets._bytes(self.assets._path(row["storage_key"])) != raw):
                raise ValueError("reference_role_conflict")
            db.execute("UPDATE skill_validation_assets SET provenance='server_reviewed' WHERE asset_sha256=?", (sha,))
            existing = db.execute("SELECT * FROM skill_validation_profiles WHERE profile_id=? AND version=1",
                                  (policy["profile_id"],)).fetchone()
            if existing is None:
                db.execute("INSERT INTO skill_validation_profiles VALUES(?,1,?,?,?,?,'enabled',?)",
                           (policy["profile_id"], canonical(policy), policy["policy_sha256"], policy["validator_id"],
                            policy["validator_sha256"], time.time_ns() // 1_000_000))
            self.require_profile(db, case, self.runtime_sha256)
        return {"profile_id": policy["profile_id"], "reference_sha256": sha, "registered": True}

    def require_profile(self, db, case, runtime_sha256):
        self._enabled()
        if runtime_sha256 != self.runtime_sha256:
            raise ValueError("general_runtime_mismatch")
        p = profiles.policy(case, runtime_sha256)
        row = db.execute("SELECT * FROM skill_validation_profiles WHERE profile_id=? AND version=1", (p["profile_id"],)).fetchone()
        if (row is None or row["state"] != "enabled" or row["policy_json"] != canonical(p)
                or row["policy_sha256"] != p["policy_sha256"] or row["validator_id"] != p["validator_id"]
                or row["validator_sha256"] != p["validator_sha256"]):
            raise ValueError("general_profile_unavailable")
        asset = db.execute("SELECT * FROM skill_validation_assets WHERE asset_sha256=?", (p["reference_sha256"],)).fetchone()
        if (asset is None or asset["role"] != "reference" or asset["provenance"] != "server_reviewed"
                or asset["storage_key"] != p["reference_sha256"] + ".npy"
                or json.loads(asset["metadata_json"]) != {"case": case, "kind": "pinned_independent_reference.v1"}):
            raise ValueError("independent_reference_unavailable")
        self.assets._root()
        raw = self.assets._bytes(self.assets._path(asset["storage_key"]))
        if len(raw) != asset["byte_count"] or hashlib.sha256(raw).hexdigest() != p["reference_sha256"]:
            raise ValueError("independent_reference_changed")
        return row

    @staticmethod
    def _job(db, job_id):
        row = ValidationJobs._row(db, job_id)
        return ValidationJobs._view(db, row)

    def _comparison(self, job, manifest_sha):
        prepared = profiles.build_controlled_spec(canonical(job["spec"]))
        case = profiles.match_profile(prepared)
        if case is None or prepared["code_template"] != job["spec"]["code_template"]:
            raise ValueError("controlled_solver_required")
        p = profiles.policy(case, self.runtime_sha256)
        if job["intent"] != "validate" or job["profile_id"] != p["profile_id"]:
            raise ValueError("validation_profile_mismatch")
        attempt = job["attempt"]
        history = read_unique_history(attempt["run_id"], attempt["request_nonce"])
        receipt_sha = verify_recorded_attempt(job, history)
        receipt = json.loads(history["executor_evidence_json"])["receipt"]
        source = Path(__file__).resolve().parents[1] / "executor" / "executor.py"
        if (receipt["runtime"]["library_versions"] != p["library_versions"]
                or receipt["runtime"]["executor_source_sha256"] != hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                or history["exit_code"] != 0 or history["timed_out"] is not False):
            raise ValueError("unapproved_solver_runtime")
        manifest = self.assets.read_manifest(manifest_sha)
        expected = {k: attempt[k] for k in ("run_id", "request_nonce", "request_sha256", "code_sha256", "parameters_sha256", "runtime_sha256", "attempt_no")}
        expected.update(job_id=job["job_id"], content_sha256=job["content_sha256"], case_fingerprint=job["case_fingerprint"],
                        result_spec=job["spec"]["result_spec"], receipt_sha256=receipt_sha)
        if any(manifest[k] != value for k, value in expected.items()):
            raise ValueError("execution_manifest_mismatch")
        from app.skill_result_assets import decode_executor_field
        actual = self.assets.read_field(manifest["field_asset_sha256"], manifest["result_spec"])
        # Check stored output is exactly the receipt-bound original, not just a self-consistent file.
        original = decode_executor_field(history, manifest["result_spec"])
        import numpy as np
        if actual.dtype != original.dtype or not np.array_equal(actual, original):
            raise ValueError("result_not_execution_bound")
        reference = profiles.load_reference(case, self.assets._path(p["reference_sha256"] + ".npy"))
        report = profiles.compare(case, actual, reference)
        proof = dict(version="independent-helmholtz-proof.v1", **expected,
                    execution_manifest_sha256=manifest_sha, field_asset_sha256=manifest["field_asset_sha256"],
                    policy_sha256=p["policy_sha256"], reference_sha256=p["reference_sha256"],
                    validator_sha256=p["validator_sha256"], adapter_sha256=p["adapter_sha256"],
                    construction_sha256=prepared["construction_sha256"], comparison=report)
        with self._preflight_lock:
            self._preflights[(job["job_id"], attempt["run_id"], manifest_sha)] = canonical(proof)
            while len(self._preflights) > 64:
                self._preflights.popitem(last=False)
        return proof

    def validate_result(self, job, execution_manifest_sha256):
        self._enabled()
        case = profiles.match_profile(job["spec"])
        with DraftStore(self.store)._connection() as db:
            self.require_profile(db, case, self.runtime_sha256)
        proof = self._comparison(job, execution_manifest_sha256)
        raw = canonical(proof).encode("utf-8")
        asset = self.assets._put(raw, {"kind": "independent-helmholtz-proof.v1"}, "evidence", "json")
        binding = digest(canonical(proof))
        with DraftStore(self.store)._connection(True) as db:
            current = self._job(db, job["job_id"])
            self.require_profile(db, case, self.runtime_sha256)
            if current["attempt"]["run_id"] != job["attempt"]["run_id"]:
                raise ValueError("attempt_changed")
            old = db.execute("SELECT * FROM skill_validation_evidence WHERE job_id=? AND attempt_no=?",
                             (job["job_id"], job["attempt"]["attempt_no"])).fetchone()
            if old is None:
                db.execute("INSERT INTO skill_validation_evidence VALUES(?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), job["job_id"], job["attempt"]["attempt_no"], binding,
                            asset["asset_sha256"], "passed" if proof["comparison"]["numeric_pass"] else "failed", time.time_ns() // 1_000_000))
            elif old["binding_sha256"] != binding or old["manifest_sha256"] != asset["asset_sha256"]:
                raise ValueError("validation_evidence_conflict")
        return {"proof_sha256": asset["asset_sha256"], "passed": proof["comparison"]["numeric_pass"]}

    def verify_proof(self, db, job, manifest_sha256, receipt_sha256):
        case = profiles.match_profile(job["spec"])
        self.require_profile(db, case, self.runtime_sha256)
        row = db.execute("SELECT * FROM skill_validation_evidence WHERE job_id=? AND attempt_no=?",
                         (job["job_id"], job["attempt"]["attempt_no"])).fetchone()
        if row is None:
            raise ValueError("independent_proof_missing")
        asset = db.execute("SELECT * FROM skill_validation_assets WHERE asset_sha256=?", (row["manifest_sha256"],)).fetchone()
        if (asset is None or asset["role"] != "evidence" or asset["provenance"] != "unreviewed"
                or json.loads(asset["metadata_json"]) != {"kind": "independent-helmholtz-proof.v1"}):
            raise ValueError("independent_proof_asset_invalid")
        raw = self.assets._bytes(self.assets._path(asset["storage_key"]))
        if hashlib.sha256(raw).hexdigest() != row["manifest_sha256"] or len(raw) != asset["byte_count"]:
            raise ValueError("independent_proof_changed")
        proof = json.loads(raw)
        # Numerical parsing/comparison happens before acquiring a write lock.
        # Under that lock, rehash actual files/history and bind the immutable
        # preflight result; a cache miss fails closed, never skips comparison.
        if db.execute("PRAGMA query_only").fetchone()[0]:
            recomputed = self._comparison(job, manifest_sha256)
        else:
            with self._preflight_lock:
                cached = self._preflights.get((job["job_id"], job["attempt"]["run_id"], manifest_sha256))
            if cached is None:
                raise ValueError("independent_preflight_required")
            recomputed = json.loads(cached)
            history = read_unique_history(job["attempt"]["run_id"], job["attempt"]["request_nonce"])
            if verify_recorded_attempt(job, history) != recomputed["receipt_sha256"]:
                raise ValueError("history_changed_since_preflight")
            for sha, role in ((manifest_sha256, "evidence"), (recomputed["field_asset_sha256"], "result")):
                item = db.execute("SELECT * FROM skill_validation_assets WHERE asset_sha256=?", (sha,)).fetchone()
                if item is None or item["role"] != role or item["provenance"] != "unreviewed":
                    raise ValueError("preflight_asset_changed")
                actual_bytes = self.assets._bytes(self.assets._path(item["storage_key"]))
                if len(actual_bytes) != item["byte_count"] or hashlib.sha256(actual_bytes).hexdigest() != sha:
                    raise ValueError("preflight_asset_changed")
        if (proof != recomputed or proof["receipt_sha256"] != receipt_sha256
                or digest(canonical(proof)) != row["binding_sha256"]
                or row["verdict"] != ("passed" if proof["comparison"]["numeric_pass"] else "failed")):
            raise ValueError("independent_proof_mismatch")
        return proof["comparison"]["numeric_pass"]

    def admit(self, job_id, *, actor_id, expected_revision):
        self._enabled(); _revision(expected_revision)
        with DraftStore(self.store)._connection() as before:
            pre_job = ValidationJobs._view(before, ValidationJobs._row(before, job_id, actor_id))
            pre_attempt = pre_job["attempt"]
            self.verify_proof(before, pre_job, pre_attempt["output_manifest_sha256"], pre_attempt["receipt_sha256"])
        with DraftStore(self.store)._connection(True) as db:
            row = ValidationJobs._row(db, job_id, actor_id)
            job = ValidationJobs._view(db, row)
            prior = db.execute("SELECT * FROM skill_validation_admissions WHERE job_id=?", (job_id,)).fetchone()
            if prior:
                current = self.verify_connection(db, {"skill_id": prior["skill_id"], "version": prior["skill_version"]},
                    job["spec"]["scenario_type"], dict(normalizer_version="normalizer_v2", parameters=job["spec"]["parameters"], result_spec=job["spec"]["result_spec"]))
                return {"admitted": current is not None, "duplicate": True, "skill_id": prior["skill_id"], "version": prior["skill_version"]}
            if job["revision"] != expected_revision:
                raise ValueError("revision_conflict")
            if job["state"] != "validated" or job["cancel_ms"] is not None:
                raise ValueError("job_not_admissible")
            head = DraftStore._head(db, job["draft_id"], actor_id)
            if head["current_version"] != job["draft_version"] or head["state"] == "archived":
                raise ValueError("draft_changed_since_validation")
            attempt = job["attempt"]
            if not self.verify_proof(db, job, attempt["output_manifest_sha256"], attempt["receipt_sha256"]):
                raise ValueError("independent_validation_failed")
            existing = db.execute("SELECT * FROM simulation_skills WHERE fingerprint=?", (job["case_fingerprint"],)).fetchone()
            if existing and (existing["status"] not in ("candidate", "active") or existing["archived"]):
                raise ValueError("withdrawn_skill_cannot_be_reactivated")
            if existing and db.execute("SELECT 1 FROM skill_feedback WHERE skill_id=? LIMIT 1", (existing["skill_id"],)).fetchone():
                raise ValueError("negative_feedback_prevents_readmission")
            evidence = db.execute("SELECT * FROM skill_validation_evidence WHERE job_id=? AND attempt_no=?", (job_id, attempt["attempt_no"])).fetchone()
            # The ordinary candidate API continues to require Q2. Only this verified transaction activates it.
            spec = job["spec"]
            physics = {"spec": spec, "job_id": job_id, "proof_sha256": evidence["manifest_sha256"],
                       "normalizer_version": "normalizer_v2", "case_fingerprint": job["case_fingerprint"]}
            skill_id = self.store.create_candidate(scenario_type=spec["scenario_type"], state_text=spec["name"],
                normalized_params=spec["parameters"], code_template=spec["code_template"],
                preconditions={"dimensions": 2, "parameters_sha256": digest(canonical(spec["parameters"]))},
                postconditions={"verdict": "normal", "validation_scope": "exact_magnitude_reference"},
                physics_evidence=physics, source_run_id=attempt["run_id"], validator_version=VALIDATOR,
                quality_level="Q2", _prepared_spec=spec, _existing_connection=db, idempotent_source_run=True)
            version = db.execute("SELECT max(version) FROM skill_versions WHERE skill_id=?", (skill_id,)).fetchone()[0]
            now = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO skill_validation_admissions VALUES(?,?,?,?,?,?)", (job_id, evidence["evidence_id"],
                       skill_id, version, digest(canonical(dict(job_id=job_id, proof=evidence["manifest_sha256"], skill_id=skill_id, version=version))), time.time_ns() // 1_000_000))
            db.execute("UPDATE simulation_skills SET status='active',quality_level='Q4',updated_at=? WHERE skill_id=?", (now, skill_id))
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES(?,?,?,?)",
                       (skill_id, "general_physics_admitted", canonical(dict(job_id=job_id, version=version, proof_sha256=evidence["manifest_sha256"], quality_level="Q4")), now))
            db.execute("UPDATE skill_validation_jobs SET state='admitted',revision=revision+1,updated_ms=? WHERE job_id=?", (time.time_ns() // 1_000_000, job_id))
            self._enabled()
            return {"admitted": True, "duplicate": False, "skill_id": skill_id, "version": version, "quality_level": "Q4"}

    def verify_connection(self, db, candidate, scenario, envelope):
        self._enabled()
        if not is_general_query(envelope):
            return None
        row = db.execute("""SELECT s.status,s.quality_level,s.archived,s.fingerprint,s.scenario_type,
            v.* FROM simulation_skills s JOIN skill_versions v ON v.skill_id=s.skill_id
            WHERE s.skill_id=? AND v.version=? AND v.version=(SELECT max(version) FROM skill_versions WHERE skill_id=s.skill_id)""",
                         (candidate["skill_id"], candidate["version"])).fetchone()
        if (row is None or row["status"] != "active" or row["quality_level"] != "Q4" or row["archived"]
                or row["validator_version"] != VALIDATOR or row["scenario_type"] != scenario
                or row["fingerprint"] != query_fingerprint(scenario, envelope)):
            return None
        if db.execute("SELECT 1 FROM skill_feedback WHERE skill_id=? LIMIT 1", (candidate["skill_id"],)).fetchone():
            return None
        evidence = json.loads(row["physics_evidence"])
        admission = db.execute("SELECT * FROM skill_validation_admissions WHERE job_id=? AND skill_id=? AND skill_version=?",
                               (evidence.get("job_id"), candidate["skill_id"], candidate["version"])).fetchone()
        if admission is None:
            return None
        job = self._job(db, evidence["job_id"])
        attempt = job["attempt"]
        registered = db.execute("SELECT * FROM skill_validation_evidence WHERE evidence_id=?", (admission["evidence_id"],)).fetchone()
        expected_admission = digest(canonical(dict(job_id=job["job_id"], proof=registered["manifest_sha256"] if registered else None,
                                                   skill_id=candidate["skill_id"], version=candidate["version"])))
        if (job["state"] != "admitted" or job["cancel_ms"] is not None or evidence.get("spec") != job["spec"]
                or registered is None or registered["job_id"] != job["job_id"] or registered["attempt_no"] != attempt["attempt_no"]
                or admission["admission_binding_sha256"] != expected_admission
                or evidence.get("proof_sha256") != registered["manifest_sha256"]
                or evidence.get("normalizer_version") != "normalizer_v2" or evidence.get("case_fingerprint") != job["case_fingerprint"]
                or job["case_fingerprint"] != row["fingerprint"] or envelope["result_spec"] != job["spec"]["result_spec"]
                or row["code_template"] != job["spec"]["code_template"] or row["code_hash"] != job["code_sha256"]
                or json.loads(row["normalized_params"]) != envelope["parameters"]
                or row["source_run_id"] != attempt["run_id"]
                or not self.verify_proof(db, job, attempt["output_manifest_sha256"], attempt["receipt_sha256"])):
            return None
        return dict(skill_id=candidate["skill_id"], version=candidate["version"], scenario_type=scenario,
                    scope=VALIDATOR, case_fingerprint=job["case_fingerprint"], code_sha256=job["code_sha256"],
                    proof_sha256=evidence["proof_sha256"], source_run_id=attempt["run_id"],
                    validation_scope="exact_magnitude_reference", units="numerical_source_normalization",
                    actual_use_authenticated=False)


@lru_cache(maxsize=8)
def _configured_library(path, assets, references, runtime):
    from app.skill_store import SkillStore
    store = SkillStore(Path(path))
    return GeneralLibrary(store, ResultAssetStore(store, assets), references, runtime)


def get_library(store=None):
    if store is None:
        from app.skill_library_service import STORE
        store = STORE
    return _configured_library(str(store.path.resolve()), config.SKILL_GENERAL_ASSET_ROOT,
                               config.SKILL_GENERAL_REFERENCE_ROOT, config.SKILL_GENERAL_RUNTIME_SHA256)


def verify_connection(store, db, candidate, scenario, parameters):
    if not enabled() or not is_general_query(parameters):
        return None
    return get_library(store).verify_connection(db, candidate, scenario, parameters)


def _context(original, reference, code):
    return original + PREFIX + canonical(dict(reference, code=code, actual_use_authenticated=False))


def consume(*, decision_id, nonce, generation_run_id, scenario_type, parameters_json, original_context):
    from app import skill_generation_decision as decisions, skill_library_service as service, skill_retrieval
    denied = dict(decisions._base(original_context, "general_reference_unavailable"), consumed=False)
    if not enabled() or not decisions.enabled():
        return denied
    try:
        uuid.UUID(generation_run_id)
        params, ps, cs = decisions._request(scenario_type, parameters_json, original_context)
        if not is_general_query(params):
            return denied
        store = service.STORE
        library = get_library(store)
        with store._read_db() as read_db:
            for candidate in skill_retrieval._shortlist(store, read_db, scenario_type, params, 3):
                library.verify_connection(read_db, candidate, scenario_type, params)
        with store._db_existing_write() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM skill_generation_decisions WHERE decision_id=?", (decision_id,)).fetchone()
            now = time.time_ns()
            if (row is None or row["nonce"] != nonce or row["scenario_type"] != scenario_type or row["parameters_sha256"] != ps
                    or row["context_sha256"] != cs or not row["created_ns"] <= now < row["expires_ns"]
                    or db.execute("SELECT 1 FROM skill_generation_consumptions WHERE decision_id=? OR generation_run_id=?", (decision_id, generation_run_id)).fetchone()):
                return denied
            current = skill_retrieval._shortlist(store, db, scenario_type, params, 3)
            if canonical(current) != row["metadata_snapshot_json"]:
                return denied
            ref = next((value for c in current if (value := library.verify_connection(db, c, scenario_type, params)) is not None), None)
            if ref is None:
                return denied
            code = db.execute("SELECT code_template FROM skill_versions WHERE skill_id=? AND version=?", (ref["skill_id"], ref["version"])).fetchone()[0]
            db.execute("INSERT INTO skill_generation_consumptions VALUES(?,?,?,?,?)",
                       (decision_id, generation_run_id, now, canonical([ref]), "general_validated_reference_delivery"))
            if not enabled() or time.time_ns() >= row["expires_ns"]:
                raise ValueError("general_reference_expired")
            return dict(denied, consumed=True, context=_context(original_context, ref, code), skills=[ref], fallback=False,
                        reason="general_validated_reference_delivery", audit_kind="general_validated_reference_delivery")
    except ERRORS:
        return denied


def read_connection(db, *, decision_id, generation_run_id, scenario_type, parameters_json, original_context, _decision_at=None):
    from app import skill_generation_decision as decisions
    if not enabled() or not decisions.enabled():
        return original_context
    params, ps, cs = decisions._request(scenario_type, parameters_json, original_context)
    if not is_general_query(params):
        return original_context
    row = db.execute("""SELECT d.*,c.metadata_current_json,c.fallback_reason consumption_reason FROM skill_generation_decisions d
        JOIN skill_generation_consumptions c ON c.decision_id=d.decision_id
        WHERE d.decision_id=? AND c.generation_run_id=?""", (decision_id, generation_run_id)).fetchone()
    at = time.time_ns() if _decision_at is None else _decision_at
    if (row is None or row["consumption_reason"] != "general_validated_reference_delivery" or row["parameters_sha256"] != ps
            or row["context_sha256"] != cs or row["scenario_type"] != scenario_type or not row["created_ns"] <= at < row["expires_ns"]):
        return original_context
    refs = json.loads(row["metadata_current_json"])
    if type(refs) is not list or len(refs) != 1:
        return original_context
    ref = get_library().verify_connection(db, refs[0], scenario_type, params)
    if ref != refs[0]:
        return original_context
    code = db.execute("SELECT code_template FROM skill_versions WHERE skill_id=? AND version=?", (ref["skill_id"], ref["version"])).fetchone()[0]
    return _context(original_context, ref, code)


def _analysis(library, job, manifest):
    view = library.assets.present(manifest["field_asset_sha256"], manifest["result_spec"])
    proof = library._comparison(job, job["attempt"]["output_manifest_sha256"])
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(6, 4.5)); canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(); image = ax.imshow(view["amplitude"], origin="lower")
    ax.set_xlabel("y grid index"); ax.set_ylabel("x grid index")
    fig.colorbar(image, ax=ax, label="Magnitude (numerical source normalization)")
    buffer = io.BytesIO(); canvas.print_png(buffer)
    error = proof["comparison"]["relative_magnitude_linf"]
    return dict(verdict="normal", quality_level="Q4", max_pressure=view["max_pressure"], rms_pressure=view["rms_pressure"],
                field_shape=view["shape"], has_signal=True, units=view["units"],
                summary=f"Independent exact magnitude comparison passed: E={error:.8g}; phase is not certified.",
                heatmap_png_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
                physics_checks={"independent_magnitude_error": error, "phase_compared": False})


def execute_reference(*, decision_id, generation_run_id, scenario_type, parameters_json, original_context):
    """A newly bound run and fresh independent comparison; never replay a dispatch."""
    from app import skill_library_service as service, skill_generation_decision as decisions
    denied = dict(recorded=False, execution_reuse_verified=False, actual_use_authenticated=False,
                  model_adoption_authenticated=False, reason="general_reference_execution_unavailable")
    if not enabled() or not decisions.enabled():
        return denied
    library = get_library(service.STORE)
    run, nonce = str(uuid.uuid4()), uuid.uuid4().hex
    try:
        reference = dict(decision_id=decision_id, generation_run_id=generation_run_id, scenario_type=scenario_type,
                         parameters_json=parameters_json, original_context=original_context)
        with service.STORE._read_db() as before:
            if read_connection(before, **reference) == original_context:
                return denied
        with service.STORE._db_existing_write() as db:
            db.execute("BEGIN IMMEDIATE")
            context = read_connection(db, **reference)
            if context == original_context:
                return denied
            ref = json.loads(context[len(original_context + PREFIX):])
            admission = db.execute("SELECT job_id FROM skill_validation_admissions WHERE skill_id=? AND skill_version=?", (ref["skill_id"], ref["version"])).fetchone()
            source_job = library._job(db, admission[0])
            request = dict(code=ref["code"], timeout_seconds=30, request_nonce=nonce)
            db.execute("INSERT INTO skill_local_reference_dispatches VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (run, generation_run_id, decision_id, ref["skill_id"], ref["version"], canonical(reference),
                        digest(canonical(reference)), nonce, digest(canonical(request)), time.time_ns()))
        # No skill DB transaction is held during solver execution.
        result = execution._execute_code(ref["code"], 30, request_nonce=nonce)
        dashboard.record_execution("general_reference_execute", ref["code"], result["exit_code"], result["timed_out"],
                                   result.get("duration_ms"), result["stdout"], result["stderr"], run_id=run, execution_evidence=result.get("execution_evidence"))
        job = dict(source_job)
        job["attempt"] = dict(source_job["attempt"], run_id=run, request_nonce=nonce, request_sha256=digest(canonical(request)))
        from app.skill_validation_worker import ValidationWorker
        bound = ValidationWorker(None, library.assets)._bind(job)
        if not bound["succeeded"]:
            return dict(denied, run_id=run)
        comparison = library._comparison(job, bound["manifest_sha256"])
        if not comparison["comparison"]["numeric_pass"]:
            return dict(denied, run_id=run)
        job["attempt"]["output_manifest_sha256"] = bound["manifest_sha256"]
        with service.STORE._db_existing_write() as db:
            db.execute("BEGIN IMMEDIATE")
            at = db.execute("SELECT registered_ns FROM skill_local_reference_dispatches WHERE run_id=?", (run,)).fetchone()[0]
            if read_connection(db, **reference, _decision_at=at) == original_context:
                return dict(denied, run_id=run)
            db.execute("INSERT INTO skill_local_reference_completions VALUES(?,?,?,?,?)",
                       (run, "verified", bound["manifest_sha256"], hashlib.sha256(result["stdout"].encode()).hexdigest(), time.time_ns()))
            db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES(?,?,?,?)",
                       (ref["skill_id"], "general_reference_execution_verified", canonical(dict(run_id=run, manifest_sha256=bound["manifest_sha256"],
                        relative_magnitude_linf=comparison["comparison"]["relative_magnitude_linf"], phase_compared=False)), datetime.now(timezone.utc).isoformat()))
        analyzed = _analysis(library, job, library.assets.read_manifest(bound["manifest_sha256"]))
        dashboard.record_analysis_event(0, analyzed["verdict"], analyzed["max_pressure"], analyzed["rms_pressure"],
            analyzed["field_shape"], analyzed["has_signal"], analyzed["summary"] + " Units: numerical source normalization; not calibrated Pa.",
            analyzed["heatmap_png_base64"], None, "", "", run_id=run)
        return dict(denied, recorded=True, execution_reuse_verified=True, run_id=run,
                    reason="general_reference_execution_verified", analysis=analyzed)
    except Exception:
        # An uncertain dispatch is never retried for this immutable generation intent.
        return dict(denied, run_id=run)
