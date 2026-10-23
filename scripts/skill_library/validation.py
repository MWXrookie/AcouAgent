"""Explicit internal trial operations; no model calls or scientific admission."""
import argparse
import json
from pathlib import Path
import sqlite3
import uuid

from app.skill_store import SkillStore
from app.skill_validation_jobs import ValidationJobs
from app.skill_result_assets import ResultAssetStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install-profile", "enqueue", "run-once", "recover",
                                            "reconcile", "status", "cancel", "result", "audit-assets", "cleanup-orphans"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--runtime-sha256", required=True, help="Operator-reviewed executor runtime digest")
    parser.add_argument("--draft-id")
    parser.add_argument("--job-id")
    parser.add_argument("--actor", default="internal-operator")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--idempotency-key")
    args = parser.parse_args()
    # Flags come from operator environment; this command never enables or migrates silently.
    try:
        store = SkillStore(args.database)
        jobs = ValidationJobs(store, args.runtime_sha256)
        assets = ResultAssetStore(store, args.assets)
        if args.command == "install-profile":
            result = jobs.install_trial_profile()
        elif args.command == "enqueue":
            if not args.draft_id or args.expected_revision is None or not args.idempotency_key:
                parser.error("enqueue requires --draft-id, --expected-revision and --idempotency-key")
            result = jobs.enqueue(args.draft_id, actor_id=args.actor,
                                  expected_revision=args.expected_revision, idempotency_key=args.idempotency_key)
        elif args.command == "recover":
            result = {"recovered": jobs.recover_expired()}
        elif args.command in ("run-once", "reconcile"):
            from app.skill_validation_worker import ValidationWorker
            worker = ValidationWorker(jobs, assets)
            if args.command == "reconcile":
                if not args.job_id:
                    parser.error("reconcile requires --job-id")
                result = worker.reconcile(args.job_id)
            else:
                result = worker.run_once("trial-worker-" + uuid.uuid4().hex)
        elif args.command in ("audit-assets", "cleanup-orphans"):
            result = assets.audit_assets() if args.command == "audit-assets" else assets.cleanup_orphans()
        else:
            if not args.job_id:
                parser.error("this command requires --job-id")
            result = jobs.get(args.job_id, actor_id=args.actor)
            if args.command == "cancel":
                if args.expected_revision is None:
                    parser.error("cancel requires --expected-revision")
                result = jobs.cancel(args.job_id, actor_id=args.actor, expected_revision=args.expected_revision)
            elif args.command == "result":
                attempt = result["attempt"]
                if result["state"] not in ("rejected", "cancelled") or not attempt or not attempt["output_manifest_sha256"]:
                    raise ValueError("result_not_available")
                manifest = assets.read_manifest(attempt["output_manifest_sha256"])
                expected = {k: attempt[k] for k in ("run_id", "request_nonce", "request_sha256",
                            "code_sha256", "parameters_sha256", "runtime_sha256", "receipt_sha256", "attempt_no")}
                expected.update(job_id=result["job_id"], content_sha256=result["content_sha256"],
                                case_fingerprint=result["case_fingerprint"], result_spec=result["spec"]["result_spec"])
                if any(manifest.get(key) != value for key, value in expected.items()):
                    raise ValueError("result_binding_mismatch")
                result = assets.present(manifest["field_asset_sha256"], manifest["result_spec"])
        if isinstance(result, dict):
            # Summaries never dump submitted code, internal SQL or filesystem paths.
            result = {k: v for k, v in result.items() if k != "spec"}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, TimeoutError):
        print(json.dumps({"error": "trial_operation_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
