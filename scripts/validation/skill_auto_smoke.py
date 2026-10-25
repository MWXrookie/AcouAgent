"""Real first solve -> automatic learning -> reuse, using disposable data.

Operator supplies reviewed runtime/references and explicitly enables the flags.
No per-case validation/admission commands or paid model calls are made.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from app import config, dashboard, skill_library_service as service, skill_retrieval
from app.skill_auto_source import execute_source, start_source
from app.skill_auto_learning import AutoLearning, enabled
from app.skill_auto_worker import start_worker
from app.skill_general_library import get_library


def run(scratch_root):
    if not enabled() or not all((config.SKILL_LIBRARY_ENABLED, config.SKILL_RETRIEVAL_ENABLED, config.SKILL_INJECTION_ENABLED)):
        raise ValueError("explicit_learning_smoke_flags_required")
    from app.skill_store import SkillStore
    original = service.STORE, dashboard.DB_PATH, config.SKILL_GENERAL_ASSET_ROOT, config.SKILL_LEARNING_ENABLED
    worker = None
    Path(scratch_root).mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="automatic-smoke-", dir=scratch_root) as temporary:
            root = Path(temporary)
            service.STORE = store = SkillStore(root / "skills.db"); store.migrate_skill_authoring()
            dashboard.DB_PATH = str(root / "history.db"); dashboard.init_db()
            config.SKILL_GENERAL_ASSET_ROOT = str(root / "assets")
            library = get_library(store)
            # One operator bootstrap of approved independent assets, not approval of learned skills.
            for case in ("B01", "B02", "B03"):
                library.install_profile(case)
            examples = Path(__file__).resolve().parents[2] / "docs/skills-next/examples"
            items = []
            for case, filename in (("B01", "manual-skill.json"), ("B02", "b02-manual-skill.json"), ("B03", "b03-manual-skill.json")):
                spec = json.loads((examples / filename).read_text(encoding="utf-8"))
                parameters = json.dumps(dict(normalizer_version="normalizer_v2", parameters=spec["parameters"], result_spec=spec["result_spec"]), sort_keys=True, separators=(",", ":"))
                if case != "B03":
                    result = execute_source(scenario_type=spec["scenario_type"], parameters_json=parameters, request_key=case)
                    assert result["source_execution_verified"] and result["analysis"]["quality_level"] == "Q2", result
                    job_id = result["job_id"]
                else:
                    # Source queued before process-style restart: only the real background pump advances it.
                    job_id = start_source(spec, request_key=case, library=library)["job_id"]
                items.append(dict(case=case, spec=spec, parameters=parameters, source_job_id=job_id))
            worker = start_worker()
            assert worker is not None and worker.is_alive()
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                with store._read_db() as db:
                    count = db.execute("SELECT count(*) FROM skill_validation_admissions").fetchone()[0]
                if count == 3:
                    break
                time.sleep(.25)
            else:
                raise AssertionError("automatic_background_admission_timeout")
            worker.stop(); worker = None
            engine = AutoLearning(library)
            before = engine.counts()
            assert before == dict(candidates=3, validated=3, admitted=3, reused=0), before
            sources = []
            with store._read_db() as db:
                for item in items:
                    row = db.execute("SELECT run_id FROM skill_validation_attempts WHERE job_id=?", (item["source_job_id"],)).fetchone()
                    sources.append(row[0])
            for run_id in sources:
                assert engine.observe(run_id)["admitted"]
            assert engine.counts() == before
            # Closing learning must not revoke already independently admitted skills.
            config.SKILL_LEARNING_ENABLED = False
            reports = []
            from app.local_workflow import _direct_reference
            for item, source_run in zip(items, sources):
                spec, parameters = item["spec"], item["parameters"]
                context = "Repeat this exact automatic acoustic skill."
                generated = skill_retrieval.generation_context(context, scenario_type=spec["scenario_type"], parameters_json=parameters, _existing_store=True)
                usage = generated["skill_usage"]
                assert usage["reason"] == "general_validated_reference_delivery", usage
                result, outcome = _direct_reference(usage, spec["scenario_type"], parameters, context)
                assert result and outcome["execution_reuse_verified"] and outcome["run_id"] != source_run, outcome
                reports.append(dict(case=item["case"], source_run=source_run, reuse_run=outcome["run_id"],
                    summary=outcome["analysis"]["summary"], units=outcome["analysis"]["units"], learning_off_reuse_verified=True))
            config.SKILL_LEARNING_ENABLED = True
            after = engine.counts(); assert after == dict(candidates=3, validated=3, admitted=3, reused=3), after
            with sqlite3.connect(dashboard.DB_PATH) as db:
                executions = db.execute("SELECT tool_name,count(*) FROM executions GROUP BY tool_name").fetchall()
                assert sum(row[1] for row in executions) == 9, executions
            return dict(kind="real_automatic_learning_and_reuse", counts=after, executions=dict(executions),
                        reports=reports, background_recovery_verified=True, production_database_writes=0, paid_model_calls=0)
    finally:
        if worker is not None:
            worker.stop()
        service.STORE, dashboard.DB_PATH, config.SKILL_GENERAL_ASSET_ROOT, config.SKILL_LEARNING_ENABLED = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.scratch_root), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
