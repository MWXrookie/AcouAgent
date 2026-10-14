#!/usr/bin/env python3
"""Generate or verify a sanitized, reproducible skill-library audit report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("/app/data/skill_store.db")
DEFAULT_JSON = Path("/app/data/audit/skill-library-audit.json")
DEFAULT_MARKDOWN = Path("/app/data/audit/skill-library-audit.md")
MAX_SKILLS = 10_000
MAX_VERSIONS = 100_000
MAX_EVENTS = 1_000_000
SAFE_EVENT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_SCENARIO = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_VERSION_TEXT = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SAFE_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
CORE_TABLES = {
    "schema_migrations",
    "simulation_skills",
    "skill_versions",
    "skill_events",
    "skill_feedback",
}
COUNTED_TABLES = (
    "simulation_skills",
    "skill_versions",
    "skill_events",
    "skill_feedback",
    "skill_retrievals",
    "skill_generation_decisions",
    "skill_generation_consumptions",
)
REQUIRED_TRIGGERS = {
    "skill_events_no_update",
    "skill_events_no_delete",
    "skill_events_no_replace",
    "skill_feedback_no_update",
    "skill_feedback_no_delete",
    "skill_feedback_no_replace",
}


class AuditError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest_without_field(report: dict[str, Any]) -> str:
    content = dict(report)
    content.pop("report_sha256", None)
    return hashlib.sha256(_canonical(content)).hexdigest()


def verify_report(report: dict[str, Any]) -> bool:
    expected = report.get("report_sha256")
    return isinstance(expected, str) and len(expected) == 64 and expected == _digest_without_field(report)


def _source_digest(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value)) if str(uuid.UUID(value)) == value.lower() else None
    except (ValueError, AttributeError, TypeError):
        return None


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise AuditError("skill_database_missing")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("BEGIN")
    return db


def build_report(path: Path) -> dict[str, Any]:
    db = _read_only(path)
    try:
        table_names = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        trigger_names = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        missing_tables = sorted(CORE_TABLES - table_names)
        missing_triggers = sorted(REQUIRED_TRIGGERS - trigger_names)
        if missing_tables:
            raise AuditError("skill_schema_incomplete")

        table_counts = {
            name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in COUNTED_TABLES
            if name in table_names
        }
        if table_counts.get("simulation_skills", 0) > MAX_SKILLS:
            raise AuditError("skill_count_limit_exceeded")
        if table_counts.get("skill_versions", 0) > MAX_VERSIONS:
            raise AuditError("version_count_limit_exceeded")
        if table_counts.get("skill_events", 0) > MAX_EVENTS:
            raise AuditError("event_count_limit_exceeded")

        migrations = [row[0] for row in db.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )]
        migrations_consecutive = migrations == list(range(1, max(migrations, default=0) + 1))
        quick_check = [row[0] for row in db.execute("PRAGMA quick_check")]
        foreign_key_violations = len(db.execute("PRAGMA foreign_key_check").fetchall())

        event_counts: dict[str, Counter[str]] = defaultdict(Counter)
        hidden_event_types = 0
        for row in db.execute(
            "SELECT skill_id,event_type,COUNT(*) count FROM skill_events "
            "GROUP BY skill_id,event_type ORDER BY skill_id,event_type"
        ):
            event_type = row["event_type"]
            if not isinstance(event_type, str) or SAFE_EVENT.fullmatch(event_type) is None:
                event_type = "content_hidden"
                hidden_event_types += row["count"]
            event_counts[row["skill_id"]][event_type] += row["count"]

        feedback_counts = {
            row["skill_id"]: row["count"]
            for row in db.execute(
                "SELECT skill_id,COUNT(*) count FROM skill_feedback GROUP BY skill_id"
            )
        }
        rows = db.execute(
            """WITH latest AS (
                   SELECT skill_id,MAX(version) version FROM skill_versions GROUP BY skill_id
               ), version_counts AS (
                   SELECT skill_id,COUNT(*) count FROM skill_versions GROUP BY skill_id
               )
               SELECT s.skill_id,s.scenario_type,s.status,s.quality_level,s.archived,
                      s.success_count,s.failure_count,s.updated_at,
                      v.version,v.code_template,v.code_hash,v.source_run_id,
                      v.validator_version,v.reference_skill_ids,
                      COALESCE(vc.count,0) version_count
               FROM simulation_skills s
               LEFT JOIN latest l ON l.skill_id=s.skill_id
               LEFT JOIN skill_versions v ON v.skill_id=l.skill_id AND v.version=l.version
               LEFT JOIN version_counts vc ON vc.skill_id=s.skill_id
               ORDER BY s.skill_id LIMIT ?""",
            (MAX_SKILLS + 1,),
        ).fetchall()
        if len(rows) > MAX_SKILLS:
            raise AuditError("skill_count_limit_exceeded")

        skills = []
        status_counts: Counter[str] = Counter()
        quality_counts: Counter[str] = Counter()
        code_hash_failures = 0
        invalid_reference_lists = 0
        missing_latest_versions = 0
        unsafe_metadata_rows = 0
        for row in rows:
            status_counts[row["status"]] += 1
            quality_counts[row["quality_level"]] += 1
            code = row["code_template"]
            stored_code_hash = row["code_hash"]
            code_hash_safe = (
                isinstance(stored_code_hash, str)
                and SAFE_HEX_SHA256.fullmatch(stored_code_hash) is not None
            )
            code_hash_verified = (
                isinstance(code, str)
                and code_hash_safe
                and hashlib.sha256(code.encode("utf-8")).hexdigest() == stored_code_hash
            )
            if not code_hash_verified:
                code_hash_failures += 1
            try:
                references = json.loads(row["reference_skill_ids"] or "[]")
                if (
                    not isinstance(references, list)
                    or len(references) > 3
                    or any(_safe_uuid(item) is None for item in references)
                    or len(set(references)) != len(references)
                ):
                    raise ValueError("invalid references")
            except (json.JSONDecodeError, TypeError, ValueError):
                references = []
                invalid_reference_lists += 1
            if row["version"] is None:
                missing_latest_versions += 1
            safe_skill_id = _safe_uuid(row["skill_id"])
            safe_scenario = (
                row["scenario_type"]
                if isinstance(row["scenario_type"], str)
                and SAFE_SCENARIO.fullmatch(row["scenario_type"]) is not None
                else None
            )
            safe_validator = (
                row["validator_version"]
                if isinstance(row["validator_version"], str)
                and SAFE_VERSION_TEXT.fullmatch(row["validator_version"]) is not None
                else None
            )
            safe_updated_at = _safe_timestamp(row["updated_at"])
            if any(item is None for item in (
                safe_skill_id, safe_scenario, safe_validator, safe_updated_at
            )):
                unsafe_metadata_rows += 1
            per_skill_events = dict(sorted(event_counts[row["skill_id"]].items()))
            skills.append({
                "skill_id": safe_skill_id or "content_hidden",
                "scenario_type": safe_scenario or "content_hidden",
                "status": row["status"],
                "quality_level": row["quality_level"],
                "archived": bool(row["archived"]),
                "success_count": row["success_count"],
                "failure_count": row["failure_count"],
                "feedback_count": feedback_counts.get(row["skill_id"], 0),
                "updated_at": safe_updated_at,
                "version_count": row["version_count"],
                "latest_version": row["version"],
                "latest_code_sha256": stored_code_hash if code_hash_safe else None,
                "code_hash_verified": code_hash_verified,
                "validator_version": safe_validator,
                "source_run_sha256": _source_digest(row["source_run_id"]),
                "reference_skill_ids": sorted(references),
                "event_count": sum(per_skill_events.values()),
                "event_type_counts": per_skill_events,
            })

        checks = {
            "sqlite_quick_check": quick_check == ["ok"],
            "foreign_key_violations": foreign_key_violations,
            "schema_migrations_consecutive": migrations_consecutive,
            "append_only_triggers_present": not missing_triggers,
            "missing_triggers": missing_triggers,
            "code_hash_failures": code_hash_failures,
            "invalid_reference_lists": invalid_reference_lists,
            "missing_latest_versions": missing_latest_versions,
            "hidden_event_types": hidden_event_types,
            "unsafe_metadata_rows": unsafe_metadata_rows,
        }
        healthy = (
            checks["sqlite_quick_check"]
            and foreign_key_violations == 0
            and migrations_consecutive
            and not missing_triggers
            and code_hash_failures == 0
            and invalid_reference_lists == 0
            and missing_latest_versions == 0
            and unsafe_metadata_rows == 0
        )
        report = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "healthy": healthy,
            "trust_verified": False,
            "trust_statement": (
                "此报告证明库存和审计结构的一致性，不证明模型采用、真人身份、"
                "跨主机来源认证或任意场景物理正确。"
            ),
            "database_schema_version": max(migrations, default=0),
            "summary": {
                "table_counts": table_counts,
                "status_counts": dict(sorted(status_counts.items())),
                "quality_counts": dict(sorted(quality_counts.items())),
                "archived_count": sum(1 for item in skills if item["archived"]),
                "total_failures": sum(item["failure_count"] for item in skills),
                "total_feedback": sum(item["feedback_count"] for item in skills),
            },
            "checks": checks,
            "skills": skills,
        }
        report["report_sha256"] = _digest_without_field(report)
        return report
    finally:
        db.close()


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    checks = report["checks"]
    lines = [
        "# AcouAgent 技能库审计报告",
        "",
        f"- 生成时间：`{report['generated_at_utc']}`",
        f"- 报告 SHA-256：`{report['report_sha256']}`",
        f"- 数据库 schema：`{report['database_schema_version']}`",
        f"- 结构健康：`{str(report['healthy']).lower()}`",
        f"- 技能总数：`{summary['table_counts'].get('simulation_skills', 0)}`",
        f"- 版本总数：`{summary['table_counts'].get('skill_versions', 0)}`",
        f"- 事件总数：`{summary['table_counts'].get('skill_events', 0)}`",
        "",
        "> " + report["trust_statement"],
        "",
        "## 一致性检查",
        "",
        f"- SQLite quick_check：`{checks['sqlite_quick_check']}`",
        f"- 外键违规：`{checks['foreign_key_violations']}`",
        f"- 审计只追加触发器：`{checks['append_only_triggers_present']}`",
        f"- 代码哈希失败：`{checks['code_hash_failures']}`",
        f"- 非法引用列表：`{checks['invalid_reference_lists']}`",
        f"- 不安全元数据行：`{checks['unsafe_metadata_rows']}`",
        "",
        "## 库存",
        "",
        "| skill_id | 场景 | 状态 | 等级 | 版本 | 失败 | 反馈 | 代码哈希 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["skills"]:
        lines.append(
            f"| `{item['skill_id']}` | {item['scenario_type']} | {item['status']} | "
            f"{item['quality_level']} | {item['version_count']} | {item['failure_count']} | "
            f"{item['feedback_count']} | `{str(item['latest_code_sha256'])[:12]}…` |"
        )
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        try:
            report = json.loads(args.verify.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            print(json.dumps({"valid": False, "error": "report_unreadable"}))
            return 1
        valid = isinstance(report, dict) and verify_report(report)
        print(json.dumps({"valid": valid, "report_sha256": report.get("report_sha256")}))
        return 0 if valid else 1
    try:
        report = build_report(args.db)
        _atomic_write(args.json, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        _atomic_write(args.markdown, render_markdown(report))
    except (AuditError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        code = str(exc) if isinstance(exc, AuditError) else "audit_generation_failed"
        print(json.dumps({"healthy": False, "error": code}))
        return 1
    print(json.dumps({
        "healthy": report["healthy"],
        "report_sha256": report["report_sha256"],
        "json": str(args.json),
        "markdown": str(args.markdown),
    }, ensure_ascii=False))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
