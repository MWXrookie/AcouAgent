"""Operator status/recovery for the configured automatic-learning service."""
import argparse
import json
import sqlite3
import uuid

from app.skill_auto_learning import AutoLearning, enabled
from app.skill_general_library import get_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "observe", "tick"))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        if not enabled():
            result = {"reason": "learning_disabled"}
        else:
            engine = AutoLearning(get_library())
            if args.command == "status":
                result = {"counts": engine.counts()}
            elif args.command == "observe":
                if not args.run_id:
                    parser.error("observe requires --run-id")
                result = engine.observe(args.run_id)
            else:
                result = engine.tick("operator-recovery-" + uuid.uuid4().hex)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, TimeoutError):
        print(json.dumps({"error": "automatic_learning_operation_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
