"""Repeatable real executor validation/admission/reuse in disposable data only.

Requires explicit skill switches, reviewed runtime and independent references.
No paid model calls. Never uses the configured production databases for writes.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile

from app import config, dashboard, skill_library_service as service, skill_retrieval
from app.skill_store import SkillStore
from app.skill_drafts import DraftStore
from app.skill_general_library import get_library, enabled
from app.skill_general_validation import GeneralValidationJobs, GeneralValidationWorker


def run(scratch_root):
    if not enabled() or not config.SKILL_RETRIEVAL_ENABLED or not config.SKILL_INJECTION_ENABLED or not config.SKILL_LIBRARY_ENABLED:
        raise ValueError("explicit_smoke_switches_required")
    original_store, original_history, original_assets = service.STORE, dashboard.DB_PATH, config.SKILL_GENERAL_ASSET_ROOT
    scratch_root = Path(scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="general-smoke-", dir=scratch_root) as temporary:
            root = Path(temporary)
            service.STORE = store = SkillStore(root / "skills.db")
            store.migrate_skill_authoring()
            dashboard.DB_PATH = str(root / "history.db"); dashboard.init_db()
            config.SKILL_GENERAL_ASSET_ROOT = str(root / "assets")
            library = get_library(store)
            report = []
            examples = Path(__file__).resolve().parents[2] / "docs/skills-next/examples"
            for case, filename in (("B01", "manual-skill.json"), ("B02", "b02-manual-skill.json"), ("B03", "b03-manual-skill.json")):
                library.install_profile(case)
                draft = DraftStore(store).create((examples / filename).read_text(encoding="utf-8"), actor_id="smoke", idempotency_key=case)
                preview = library.save_controlled_draft(draft["draft_id"], actor_id="smoke", expected_revision=1)
                assert preview["changed"] and preview["diff"]
                saved = library.save_controlled_draft(draft["draft_id"], actor_id="smoke", expected_revision=1, apply=True)["draft"]
                jobs = GeneralValidationJobs(store, library.runtime_sha256, case, library)
                queued = jobs.enqueue(draft["draft_id"], actor_id="smoke", expected_revision=saved["revision"], idempotency_key=case)
                checked = GeneralValidationWorker(jobs, library.assets, library).run_once("smoke-worker")
                assert checked["state"] == "validated", (checked["state"], checked["reason_code"])
                admitted = library.admit(queued["job_id"], actor_id="smoke", expected_revision=checked["revision"])
                assert admitted["admitted"]
                spec = checked["spec"]
                params = json.dumps(dict(normalizer_version="normalizer_v2", parameters=spec["parameters"], result_spec=spec["result_spec"]), sort_keys=True, separators=(",", ":"))
                assert len(skill_retrieval.search(spec["scenario_type"], params)["skills"]) == 1
                context = "Repeat this exact independently validated acoustic case."
                generation = skill_retrieval.generation_context(context, scenario_type=spec["scenario_type"], parameters_json=params, _existing_store=True)
                usage = generation["skill_usage"]
                assert usage["reason"] == "general_validated_reference_delivery", usage
                from app.local_workflow import _direct_reference
                result, outcome = _direct_reference(usage, spec["scenario_type"], params, context)
                assert result and outcome["execution_reuse_verified"], outcome
                assert outcome["run_id"] != checked["attempt"]["run_id"]
                proof = library._comparison(checked, checked["attempt"]["output_manifest_sha256"])
                with store._read_db() as db:
                    stamp = db.execute("SELECT updated_at FROM simulation_skills WHERE skill_id=?", (admitted["skill_id"],)).fetchone()[0]
                store.set_archived(admitted["skill_id"], archived=True, expected_updated_at=stamp, actor="internal-system", reason="smoke withdrawal")
                assert not skill_retrieval.search(spec["scenario_type"], params)["skills"]
                with sqlite3.connect(dashboard.DB_PATH) as db:
                    durations = [r[0] for r in db.execute("SELECT duration_ms FROM executions WHERE run_id IN (?,?) ORDER BY id", (checked["attempt"]["run_id"], outcome["run_id"]))]
                    assert db.execute("SELECT count(*) FROM analysis_events WHERE run_id=?", (outcome["run_id"],)).fetchone()[0] == 1
                report.append(dict(case=case, validation_run=checked["attempt"]["run_id"], reuse_run=outcome["run_id"],
                    comparison=proof["comparison"], durations_ms=durations, runtime_sha256=library.runtime_sha256,
                    validation_manifest_sha256=checked["attempt"]["output_manifest_sha256"],
                    reuse_summary=outcome["analysis"]["summary"], units=outcome["analysis"]["units"],
                    archived_retrieval_denied=True))
            with store._read_db() as db:
                assert db.execute("SELECT count(*) FROM skill_validation_admissions").fetchone()[0] == 3
                assert db.execute("SELECT count(*) FROM skill_local_reference_completions").fetchone()[0] == 3
            with sqlite3.connect(dashboard.DB_PATH) as db:
                assert db.execute("SELECT count(*) FROM executions").fetchone()[0] == 6
            return dict(kind="real_independent_validation_admission_reuse", results=report,
                        production_database_writes=0, paid_model_calls=0)
    finally:
        service.STORE, dashboard.DB_PATH, config.SKILL_GENERAL_ASSET_ROOT = original_store, original_history, original_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.scratch_root), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
