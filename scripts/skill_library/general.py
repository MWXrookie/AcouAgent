"""Explicit operator preparation, validation and admission of controlled skills."""
import argparse
import json
from pathlib import Path
import sqlite3
import uuid

from app.skill_general_library import GeneralLibrary
from app.skill_general_validation import GeneralValidationJobs, GeneralValidationWorker
from app.skill_result_assets import ResultAssetStore
from app.skill_store import SkillStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install-profile", "preview-draft", "apply-draft", "enqueue",
                                            "run-once", "reconcile", "admit", "status"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--case", choices=("B01", "B02", "B03"), required=True)
    parser.add_argument("--actor", default="internal-operator")
    parser.add_argument("--draft-id")
    parser.add_argument("--job-id")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    try:
        store = SkillStore(args.database)
        assets = ResultAssetStore(store, args.assets)
        library = GeneralLibrary(store, assets, args.references, args.runtime_sha256)
        jobs = GeneralValidationJobs(store, args.runtime_sha256, args.case, library)
        if args.command == "install-profile":
            result = library.install_profile(args.case)
        elif args.command in ("preview-draft", "apply-draft", "enqueue"):
            if not args.draft_id or args.expected_revision is None:
                parser.error("this command requires --draft-id and --expected-revision")
            if args.command == "enqueue":
                if not args.idempotency_key:
                    parser.error("enqueue requires --idempotency-key")
                result = jobs.enqueue(args.draft_id, actor_id=args.actor, expected_revision=args.expected_revision,
                                      idempotency_key=args.idempotency_key)
            else:
                prepared = library.save_controlled_draft(args.draft_id, actor_id=args.actor,
                    expected_revision=args.expected_revision, apply=args.command == "apply-draft")
                result = {key: prepared[key] for key in ("changed", "diff", "source_content_sha256",
                          "derived_content_sha256", "adapter_sha256", "construction_sha256")}
                if "draft" in prepared:
                    result["saved_version"] = prepared["draft"]["version"]
                    result["revision"] = prepared["draft"]["revision"]
        elif args.command == "run-once":
            result = GeneralValidationWorker(jobs, assets, library).run_once("general-worker-" + uuid.uuid4().hex)
        else:
            if not args.job_id:
                parser.error("this command requires --job-id")
            if args.command == "admit":
                if args.expected_revision is None:
                    parser.error("admit requires --expected-revision")
                result = library.admit(args.job_id, actor_id=args.actor, expected_revision=args.expected_revision)
            elif args.command == "reconcile":
                result = GeneralValidationWorker(jobs, assets, library).reconcile(args.job_id)
            else:
                result = jobs.get(args.job_id, actor_id=args.actor)
        if isinstance(result, dict):
            result = {key: value for key, value in result.items() if key != "spec"}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, TimeoutError):
        print(json.dumps({"error": "general_operation_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
