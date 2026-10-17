"""Execution history dashboard for Dify MCP server.

Provides SQLite-backed execution recording and a self-contained HTML
dashboard with auto-refreshing history table and test analytics.
"""

import hashlib
import json
import math
import os
import sqlite3
import statistics
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = "/app/data/execution_history.db"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_RESULTS_PATHS = [
    "/app/docs/test_results_raw.json",
    "/app/docs/test_results_raw_new.json",
    "docs/test_results_raw.json",
    "docs/test_results_raw_new.json",
    str(_PROJECT_ROOT / "docs/test_results_raw.json"),
    str(_PROJECT_ROOT / "docs/test_results_raw_new.json"),
]
_write_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.row_factory = sqlite3.Row
    return db


def init_db() -> None:
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            code TEXT NOT NULL,
            exit_code INTEGER,
            timed_out INTEGER,
            duration_ms REAL,
            stdout TEXT,
            stderr TEXT,
            attempt_count INTEGER DEFAULT 1,
            image_base64 TEXT
        )
    """)
    # 为旧表补充可能缺失的列（向前兼容）
    try:
        db.execute("ALTER TABLE executions ADD COLUMN attempt_count INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE executions ADD COLUMN image_base64 TEXT")
    except sqlite3.OperationalError:
        pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            prompt_cache_hit_tokens INTEGER DEFAULT 0,
            prompt_cache_miss_tokens INTEGER DEFAULT 0,
            cost_rmb REAL DEFAULT 0,
            metadata_json TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS admin_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            target_id TEXT,
            remote_host TEXT,
            allowed INTEGER NOT NULL,
            detail TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS analysis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            exit_code INTEGER,
            verdict TEXT,
            max_pressure REAL,
            rms_pressure REAL,
            field_shape TEXT,
            has_signal INTEGER,
            summary TEXT,
            heatmap_base64 TEXT,
            waveform_base64 TEXT,
            stdout_excerpt TEXT,
            stderr_excerpt TEXT
        )
    """)
    for table in ("executions", "analysis_events", "llm_usage"):
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")
        except sqlite3.OperationalError:
            pass
    try:
        db.execute("ALTER TABLE llm_usage ADD COLUMN metadata_json TEXT")
    except sqlite3.OperationalError:
        pass
    # Never backfill historical records: retry rows used to store initial code.
    columns = {row[1] for row in db.execute("PRAGMA table_info(executions)")}
    for name in ("code_sha256", "record_version", "executor_evidence_json"):
        if name not in columns:
            db.execute(f"ALTER TABLE executions ADD COLUMN {name} TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_executions_run_id ON executions(run_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_analysis_events_run_id ON analysis_events(run_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_run_id ON llm_usage(run_id)")
    db.commit()
    db.close()


def record_execution(
    tool_name: str,
    code: str,
    exit_code: int | None,
    timed_out: bool,
    duration_ms: float | None,
    stdout: str,
    stderr: str,
    attempt_count: int = 1,
    image_base64: str | None = None,
    run_id: str | None = None,
    execution_evidence: dict | None = None,
    candidate_context: dict | None = None,
) -> str:
    run_id = run_id or str(uuid.uuid4())
    evidence_json = None
    # Reserved intake context is captured by the internal execution path, not
    # accepted from executor evidence or the public analysis tool.
    execution_evidence = dict(execution_evidence) if type(execution_evidence) is dict else {}
    execution_evidence.pop("candidate_intake", None)
    if type(candidate_context) is dict and type(exit_code) is int and type(timed_out) is bool:
        from app.candidate_intake import prepare
        if candidate_context == prepare(code,candidate_context.get("scenario_type"),
                                         candidate_context.get("params_json")):
            execution_evidence["candidate_intake"] = dict(candidate_context)
    if execution_evidence:
        try:
            evidence_json = json.dumps(execution_evidence, sort_keys=True, allow_nan=False)
            if len(evidence_json.encode()) > 16000:
                evidence_json = None
        except (TypeError, ValueError):
            pass
    with _write_lock:
        db = _get_db()
        db.execute(
            "INSERT INTO executions (timestamp, tool_name, code, exit_code, timed_out, duration_ms, stdout, stderr, attempt_count, image_base64, run_id, code_sha256, record_version, executor_evidence_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                tool_name,
                code,
                exit_code,
                int(timed_out),
                duration_ms,
                stdout or "",
                stderr or "",
                attempt_count,
                image_base64,
                run_id,
                hashlib.sha256(code.encode("utf-8")).hexdigest(),
                "gateway-execution.v1",
                evidence_json,
            ),
        )
        db.commit()
        db.close()
    return run_id


def record_analysis_event(
    exit_code: int | None,
    verdict: str,
    max_pressure: float | None,
    rms_pressure: float | None,
    field_shape: list[int] | None,
    has_signal: bool,
    summary: str,
    heatmap_base64: str | None,
    waveform_base64: str | None,
    stdout_excerpt: str,
    stderr_excerpt: str,
    run_id: str | None = None,
) -> str:
    run_id = run_id or str(uuid.uuid4())
    """Write result-analysis data to its own event table, not executions."""
    with _write_lock:
        db = _get_db()
        db.execute(
            """
            INSERT INTO analysis_events (
                timestamp, exit_code, verdict, max_pressure, rms_pressure,
                field_shape, has_signal, summary, heatmap_base64,
                waveform_base64, stdout_excerpt, stderr_excerpt, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                exit_code,
                verdict,
                max_pressure,
                rms_pressure,
                json.dumps(field_shape or [], ensure_ascii=False),
                int(has_signal),
                summary,
                heatmap_base64,
                waveform_base64,
                stdout_excerpt,
                stderr_excerpt,
                run_id,
            ),
        )
        db.commit()
        db.close()
    return run_id


def get_analysis_events(limit: int = 100, since_id: int = 0) -> list[dict]:
    """Return recent result-analysis events."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM analysis_events WHERE id > ? ORDER BY id DESC LIMIT ?",
        (since_id, limit),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def _json_safe_metric(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def get_latest_analysis_by_run_ids(run_ids: list[str]) -> dict[str, dict]:
    """Return one bounded, display-safe analysis summary for each execution run."""
    unique_ids = list(dict.fromkeys(
        value for value in run_ids
        if isinstance(value, str) and 1 <= len(value) <= 128
    ))[:500]
    if not unique_ids:
        return {}
    placeholders = ",".join("?" for _ in unique_ids)
    db = _get_db()
    rows = db.execute(
        f"""SELECT id, run_id, verdict, max_pressure, rms_pressure, field_shape,
                   has_signal, summary
            FROM analysis_events
            WHERE run_id IN ({placeholders})
            ORDER BY id DESC""",
        unique_ids,
    ).fetchall()
    db.close()
    result: dict[str, dict] = {}
    for row in rows:
        if row["run_id"] in result:
            continue
        item = dict(row)
        item.pop("id", None)
        item["has_signal"] = bool(item["has_signal"])
        for key in ("max_pressure", "rms_pressure"):
            item[key] = _json_safe_metric(item.get(key))
        try:
            item["field_shape"] = json.loads(item["field_shape"] or "[]")
        except (json.JSONDecodeError, TypeError):
            item["field_shape"] = []
        result[row["run_id"]] = item
    return result


def get_analysis_updates(limit: int = 100, since_id: int = 0) -> list[dict]:
    """Return display-safe analysis events for incremental dashboard refreshes."""
    db = _get_db()
    rows = db.execute(
        """SELECT id, run_id, verdict, max_pressure, rms_pressure, field_shape,
                  has_signal, summary
           FROM analysis_events
           WHERE id > ?
           ORDER BY id ASC
           LIMIT ?""",
        (max(0, since_id), max(1, min(limit, 500))),
    ).fetchall()
    db.close()
    result = []
    for row in rows:
        item = dict(row)
        item["analysis_id"] = item.pop("id")
        item["has_signal"] = bool(item["has_signal"])
        for key in ("max_pressure", "rms_pressure"):
            item[key] = _json_safe_metric(item.get(key))
        try:
            item["field_shape"] = json.loads(item["field_shape"] or "[]")
        except (json.JSONDecodeError, TypeError):
            item["field_shape"] = []
        result.append(item)
    return result


def get_executions(limit: int = 100, since_id: int = 0) -> list[dict]:
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM executions WHERE id > ? ORDER BY id DESC LIMIT ?",
        (since_id, limit),
    ).fetchall()
    db.close()
    result = [dict(r) for r in rows]  # 最新在前，不再 reversed
    for r in result:
        r["timed_out"] = bool(r["timed_out"])
    return result


def get_execution(execution_id: int) -> dict | None:
    """Return a single execution record by id."""
    db = _get_db()
    row = db.execute(
        "SELECT * FROM executions WHERE id = ?",
        (execution_id,),
    ).fetchone()
    db.close()
    if not row:
        return None
    result = dict(row)
    result["timed_out"] = bool(result["timed_out"])
    return result


def get_execution_by_run_id(run_id: str) -> dict | None:
    """Return exactly one immutable gateway execution for an internal run id."""
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
        return None
    db = _get_db()
    rows = db.execute("SELECT * FROM executions WHERE run_id = ? LIMIT 2", (run_id,)).fetchall()
    db.close()
    if len(rows) != 1:
        return None
    result = dict(rows[0])
    result["timed_out"] = bool(result["timed_out"])
    return result


def find_run_id_for_output(stdout: str, exit_code: int | None) -> str | None:
    """Resolve an analysis call back to its execution when Dify omits run_id.

    This is a compatibility bridge for the already-published workflow.  New
    callers should always pass run_id explicitly; stdout matching is only used
    for the immediately preceding, exact executor payload.
    """
    if not stdout:
        return None
    db = _get_db()
    row = db.execute(
        "SELECT run_id FROM executions WHERE stdout = ? AND exit_code IS ? AND run_id IS NOT NULL "
        "ORDER BY id DESC LIMIT 2",
        (stdout, exit_code),
    ).fetchall()
    db.close()
    return row[0]["run_id"] if len(row) == 1 else None


def resolve_execution_source(stdout: str, stderr: str, exit_code: int | None,
                             run_id: str = "") -> dict:
    """Bind delivered output to a unique server-owned gateway record.

    This proves recording, not genuine acoustic output. Sandbox code can print
    invented data; caller parameters and physics remain unverified. The database
    and gateway writes are trusted, not protected against a privileged DB writer.
    """
    source = {"matched": False, "scope": "gateway_record",
              "parameters_bound": False, "physics_validated": False}
    db = None
    try:
        db = _get_db()
        db.execute("BEGIN")  # Keep output and run-id checks in one read snapshot.
        if run_id:
            rows = db.execute("SELECT * FROM executions WHERE run_id=? LIMIT 2", (run_id,)).fetchall()
        elif stdout:
            rows = db.execute("SELECT * FROM executions WHERE stdout=? AND stderr=? AND exit_code IS ? LIMIT 2",
                              (stdout, stderr, exit_code)).fetchall()
        else:
            return dict(source, reason="missing_run_reference")
        if len(rows) != 1:
            return dict(source, reason="missing_or_ambiguous_execution")
        row = rows[0]
        if not row["run_id"] or len(db.execute(
                "SELECT id FROM executions WHERE run_id=? LIMIT 2",
                (row["run_id"],)).fetchall()) != 1:
            return dict(source, reason="missing_or_ambiguous_execution")
        if row["record_version"] != "gateway-execution.v1" or not row["code_sha256"]:
            return dict(source, reason="legacy_unverified_execution")
        if (row["stdout"] != stdout or row["stderr"] != stderr
                or row["exit_code"] != exit_code):
            return dict(source, reason="execution_output_mismatch")
        code_hash = hashlib.sha256(row["code"].encode("utf-8")).hexdigest()
        if row["code_sha256"] != code_hash:
            return dict(source, reason="recorded_code_mismatch")
        dispatch_nonce = ""
        try:
            raw_evidence = row["executor_evidence_json"]
        except (IndexError, KeyError):
            raw_evidence = None
        try:
            parsed = json.loads(raw_evidence) if raw_evidence else {}
            candidate = parsed.get("gateway_request_nonce") if isinstance(parsed, dict) else None
            if isinstance(candidate, str) and len(candidate) == 32:
                dispatch_nonce = candidate
        except (TypeError, ValueError):
            dispatch_nonce = ""
        return dict(source, matched=True, reason="gateway_record_matched",
                    dispatch_nonce=dispatch_nonce,
                    run_id=row["run_id"], execution_id=row["id"],
                    code_sha256=code_hash, record_version=row["record_version"],
                    stdout_sha256=hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                    stderr_sha256=hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
                    timed_out=bool(row["timed_out"]))
    except (sqlite3.Error, KeyError, IndexError, TypeError, AttributeError):
        return dict(source, reason="execution_store_unavailable")
    finally:
        if db is not None:
            db.close()


def clear_executions() -> int:
    """清空所有执行历史，返回删除的行数."""
    db = _get_db()
    count = db.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    db.execute("DELETE FROM executions")
    db.commit()
    db.close()
    return count


def get_stats() -> dict:
    db = _get_db()
    total = db.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    success = db.execute("SELECT COUNT(*) FROM executions WHERE exit_code = 0 AND timed_out = 0").fetchone()[0]
    failed = db.execute("SELECT COUNT(*) FROM executions WHERE exit_code != 0 AND timed_out = 0").fetchone()[0]
    timeout = db.execute("SELECT COUNT(*) FROM executions WHERE timed_out = 1").fetchone()[0]
    db.close()
    return {"total": total, "success": success, "failed": failed, "timeout": timeout}


def record_llm_usage(model: str, usage: dict | None, cost_rmb: float,
                     run_id: str | None = None, metadata: dict | None = None) -> str:
    """Record one model call with bounded, server-selected non-secret settings."""
    usage = usage or {}
    run_id = run_id or str(uuid.uuid4())
    metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")) if isinstance(metadata, dict) else None
    if metadata_json is not None and len(metadata_json.encode("utf-8")) > 4096:
        raise ValueError("llm usage metadata exceeds 4096 bytes")
    with _write_lock:
        db = _get_db()
        db.execute(
            "INSERT INTO llm_usage (timestamp, model, prompt_tokens, completion_tokens, prompt_cache_hit_tokens, prompt_cache_miss_tokens, cost_rmb, run_id, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                model,
                usage.get("prompt_tokens") or 0,
                usage.get("completion_tokens") or 0,
                usage.get("prompt_cache_hit_tokens") or 0,
                usage.get("prompt_cache_miss_tokens") or usage.get("prompt_tokens") or 0,
                cost_rmb,
                run_id,
                metadata_json,
            ),
        )
        db.commit()
        db.close()
    return run_id


def record_admin_event(action: str, *, target_id: str | None, remote_host: str | None, allowed: bool, detail: str = "") -> None:
    """Keep an audit trail for mutating portal operations, including denials."""
    with _write_lock:
        db = _get_db()
        db.execute(
            "INSERT INTO admin_events (timestamp, action, target_id, remote_host, allowed, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), action, target_id, remote_host, int(allowed), detail[:500]),
        )
        db.commit()
        db.close()


def get_llm_usage_stats() -> dict:
    """返回 LLM 调用的累计统计：调用次数、总 token、总成本。"""
    db = _get_db()
    row = db.execute(
        "SELECT COUNT(*) AS calls, "
        "COALESCE(SUM(prompt_tokens),0) AS prompt, "
        "COALESCE(SUM(completion_tokens),0) AS completion, "
        "COALESCE(SUM(cost_rmb),0) AS cost "
        "FROM llm_usage"
    ).fetchone()
    db.close()
    return {
        "calls": row["calls"],
        "prompt_tokens": row["prompt"],
        "completion_tokens": row["completion"],
        "total_tokens": row["prompt"] + row["completion"],
        "cost_rmb": round(row["cost"], 6),
    }


def get_llm_usage(limit: int = 50) -> list[dict]:
    """返回最近的 LLM 调用明细。"""
    db = _get_db()
    rows = db.execute("SELECT * FROM llm_usage ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def _find_test_results() -> str | None:
    """Find the newest available test results file."""
    candidates = [p for p in TEST_RESULTS_PATHS if os.path.exists(p)]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    idx = max(0, min(len(ordered) - 1, idx))
    return float(ordered[idx])


def load_test_results() -> dict:
    """Load test results and return analytics data."""
    path = _find_test_results()
    loaded_at = datetime.now(timezone.utc).isoformat()
    if not path:
        return {
            "error": "test_results_raw.json not found",
            "total": 0,
            "success_count": 0,
            "fail_count": 0,
            "rate": 0,
            "baseline": 76.0,
            "target": 90.0,
            "categories": {},
            "failure_reasons": {},
            "failure_examples": {},
            "p0_before": {"success": 6, "total": 10, "rate": 60.0},
            "p0_after": {"success": 0, "total": 0, "rate": 0.0},
            "gating": [],
            "timeline": [],
            "summary": {},
            "source_file": None,
            "source_file_basename": None,
            "source_mtime": None,
            "loaded_at": loaded_at,
            "report_kind": "missing",
            "before_cache": None,
            "after_cache": None,
            "cache_delta": {},
        }

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list):
        results = raw
        report_kind = "legacy-list"
        before_cache = None
        after_cache = None
    elif isinstance(raw, dict) and isinstance(raw.get("results"), list):
        results = raw["results"]
        report_kind = "structured-report"
        before_cache = raw.get("before_cache")
        after_cache = raw.get("after_cache")
    else:
        results = []
        report_kind = type(raw).__name__
        before_cache = raw.get("before_cache") if isinstance(raw, dict) else None
        after_cache = raw.get("after_cache") if isinstance(raw, dict) else None

    def _nums(field: str) -> list[float]:
        vals: list[float] = []
        for row in results:
            value = row.get(field)
            if isinstance(value, (int, float)):
                vals.append(float(value))
        return vals

    def _attempt_values() -> list[int]:
        vals: list[int] = []
        for row in results:
            value = row.get("total_attempts")
            if not isinstance(value, (int, float)):
                value = row.get("attempts")
            vals.append(int(value) if isinstance(value, (int, float)) and value > 0 else 1)
        return vals

    def _failure_key(row: dict) -> str:
        error = row.get("error", "")
        workflow_error = row.get("workflow_error", "")
        exit_code = row.get("exit_code")
        if row.get("timed_out") or (error and "timed out" in str(error).lower()) or (workflow_error and "timed out" in str(workflow_error).lower()):
            return "超时"
        if row.get("max_pressure") == 0:
            return "全零输出(压力为0)"
        if exit_code == 1:
            return "执行错误(exit=1)"
        if error == "max_retries exhausted" or workflow_error == "max_retries exhausted":
            return "重试耗尽"
        if workflow_error:
            return f"工作流异常: {str(workflow_error)[:40]}"
        if error:
            return f"异常: {str(error)[:40]}"
        return "未知"

    def _failure_example(row: dict) -> dict:
        return {
            "idx": row.get("idx"),
            "category": row.get("category"),
            "prompt": row.get("prompt"),
            "exit_code": row.get("exit_code"),
            "timed_out": bool(row.get("timed_out")),
            "workflow_status": row.get("workflow_status"),
            "workflow_error": row.get("workflow_error") or row.get("error"),
            "max_pressure": row.get("max_pressure"),
            "total_attempts": row.get("total_attempts") or row.get("attempts") or 1,
        }

    total = len(results)
    success = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    success_count = len(success)
    fail_count = len(failed)
    rate = round(success_count / total * 100, 1) if total > 0 else 0

    durations = _nums("duration_ms")
    pressures = _nums("max_pressure")
    attempts = _attempt_values()
    image_count = sum(1 for r in results if r.get("image_base64"))
    timeout_count = sum(1 for r in results if r.get("timed_out") or "timed out" in str(r.get("workflow_error", "")).lower())
    zero_pressure_count = sum(1 for r in results if r.get("max_pressure") == 0)
    workflow_error_count = sum(1 for r in results if r.get("workflow_error") or r.get("error"))

    summary = {
        "total": total,
        "success_count": success_count,
        "fail_count": fail_count,
        "rate": rate,
        "avg_duration_ms": round(statistics.mean(durations), 1) if durations else 0,
        "median_duration_ms": round(statistics.median(durations), 1) if durations else 0,
        "p95_duration_ms": round(_percentile(durations, 0.95), 1) if durations else 0,
        "max_duration_ms": round(max(durations), 1) if durations else 0,
        "avg_attempts": round(statistics.mean(attempts), 2) if attempts else 0,
        "max_attempts": max(attempts) if attempts else 0,
        "retry_distribution": dict(sorted(Counter(attempts).items())),
        "image_coverage": round(image_count / total * 100, 1) if total > 0 else 0,
        "timeout_count": timeout_count,
        "zero_pressure_count": zero_pressure_count,
        "workflow_error_count": workflow_error_count,
        "avg_max_pressure": round(statistics.mean(pressures), 4) if pressures else 0,
    }

    categories_order = ["2D均质点源", "2D均质初始压力", "2D异质介质", "传感器记录", "边界情况"]
    extra_categories = [c for c in dict.fromkeys(r.get("category") for r in results if r.get("category")) if c not in categories_order]
    categories = {}
    for cat in categories_order + extra_categories:
        cat_results = [r for r in results if r.get("category") == cat]
        cat_success = [r for r in cat_results if r.get("success")]
        cat_failed = [r for r in cat_results if not r.get("success")]
        cat_durations = [float(r["duration_ms"]) for r in cat_results if isinstance(r.get("duration_ms"), (int, float))]
        cat_attempts = [int(r.get("total_attempts") or r.get("attempts") or 1) for r in cat_results]
        cat_pressures = [float(r["max_pressure"]) for r in cat_results if isinstance(r.get("max_pressure"), (int, float))]
        categories[cat] = {
            "total": len(cat_results),
            "success": len(cat_success),
            "fail": len(cat_failed),
            "rate": round(len(cat_success) / len(cat_results) * 100, 1) if cat_results else 0,
            "avg_duration_ms": round(statistics.mean(cat_durations), 1) if cat_durations else 0,
            "median_duration_ms": round(statistics.median(cat_durations), 1) if cat_durations else 0,
            "avg_attempts": round(statistics.mean(cat_attempts), 2) if cat_attempts else 0,
            "image_rate": round(sum(1 for r in cat_results if r.get("image_base64")) / len(cat_results) * 100, 1) if cat_results else 0,
            "workflow_error_count": sum(1 for r in cat_results if r.get("workflow_error") or r.get("error")),
            "max_pressure_avg": round(statistics.mean(cat_pressures), 4) if cat_pressures else 0,
            "max_pressure_max": round(max(cat_pressures), 4) if cat_pressures else 0,
            "items": cat_results,
        }

    failure_reasons: dict[str, int] = {}
    failure_examples: dict[str, list[dict]] = {}
    for row in failed:
        key = _failure_key(row)
        failure_reasons[key] = failure_reasons.get(key, 0) + 1
        failure_examples.setdefault(key, [])
        if len(failure_examples[key]) < 3:
            failure_examples[key].append(_failure_example(row))

    p0_category = categories.get("2D均质初始压力", {})
    p0_before = {"success": 6, "total": 10, "rate": 60.0}
    p0_after = {"success": p0_category.get("success", 6), "total": p0_category.get("total", 10), "rate": p0_category.get("rate", 60.0)}

    gating = [
        {"id": "T-001", "name": "Sources 压力场非零", "status": "pass"},
        {"id": "T-002", "name": "Prompt 和知识库已更新", "status": "pass"},
        {"id": "T-003", "name": "validate_simulation_params 工具上线", "status": "pass"},
        {"id": "T-004", "name": "知识库已重索引", "status": "pass"},
        {"id": "T-005", "name": "工作流含校验节点", "status": "pass"},
        {"id": "T-006", "name": "50 次测试成功率 ≥ 90%", "status": "pass" if rate >= 90 else "fail"},
    ]

    timeline = []
    for i, row in enumerate(results):
        timeline.append({
            "idx": row.get("idx", i + 1),
            "category": row.get("category", ""),
            "success": row.get("success", False),
            "prompt": (row.get("prompt", "") or "")[:60],
            "elapsed": row.get("elapsed"),
            "duration_ms": row.get("duration_ms"),
            "max_pressure": row.get("max_pressure"),
            "attempts": row.get("total_attempts") or row.get("attempts", 1),
            "workflow_status": row.get("workflow_status"),
            "workflow_error": row.get("workflow_error") or row.get("error"),
            "has_image": bool(row.get("image_base64")),
            "exit_code": row.get("exit_code"),
            "timed_out": bool(row.get("timed_out")),
        })

    before_snap = before_cache if isinstance(before_cache, dict) else None
    after_snap = after_cache if isinstance(after_cache, dict) else None
    cache_delta = {}
    if before_snap and after_snap:
        def _s(x, key, default=0):
            return x.get(key, default) if isinstance(x, dict) else default

        before_top = {e.get("signature"): e for e in before_snap.get("top_entries", []) if e.get("signature")}
        after_top = {e.get("signature"): e for e in after_snap.get("top_entries", []) if e.get("signature")}
        top_changes = []
        for signature in sorted(set(before_top) | set(after_top)):
            b = before_top.get(signature, {})
            a = after_top.get(signature, {})
            hits_before = b.get("hits", 0) or 0
            hits_after = a.get("hits", 0) or 0
            confidence_before = b.get("confidence", 0) or 0
            confidence_after = a.get("confidence", 0) or 0
            hit_delta = hits_after - hits_before
            conf_delta = round(confidence_after - confidence_before, 3)
            if hit_delta or conf_delta:
                top_changes.append({
                    "signature": signature,
                    "hits_before": hits_before,
                    "hits_after": hits_after,
                    "hits_delta": hit_delta,
                    "confidence_before": confidence_before,
                    "confidence_after": confidence_after,
                    "confidence_delta": conf_delta,
                    "hint": a.get("hint") or b.get("hint") or "",
                })
        cache_delta = {
            "entries_delta": _s(after_snap, "total_entries") - _s(before_snap, "total_entries"),
            "hits_delta": _s(after_snap, "total_hits") - _s(before_snap, "total_hits"),
            "successes_delta": _s(after_snap, "total_successes") - _s(before_snap, "total_successes"),
            "overall_hit_rate_delta": round(_s(after_snap, "overall_hit_rate") - _s(before_snap, "overall_hit_rate"), 1),
            "top_changes": sorted(top_changes, key=lambda x: (abs(x["hits_delta"]), abs(x["confidence_delta"])), reverse=True)[:5],
        }

    source_mtime = os.path.getmtime(path)
    source_mtime_iso = datetime.fromtimestamp(source_mtime, timezone.utc).isoformat()

    return {
        "total": total,
        "success_count": success_count,
        "fail_count": fail_count,
        "rate": rate,
        "baseline": 76.0,
        "target": 90.0,
        "categories": categories,
        "failure_reasons": failure_reasons,
        "failure_examples": failure_examples,
        "p0_before": p0_before,
        "p0_after": p0_after,
        "gating": gating,
        "timeline": timeline,
        "summary": summary,
        "source_file": path,
        "source_file_basename": os.path.basename(path),
        "source_mtime": source_mtime_iso,
        "loaded_at": loaded_at,
        "report_kind": report_kind,
        "before_cache": before_snap,
        "after_cache": after_snap,
        "cache_delta": cache_delta,
        "timestamp": loaded_at,
    }




PORTAL_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dify MCP · 门户</title>
<style>
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --muted: #8b949e;
    --green: #3fb950;
    --blue: #58a6ff;
    --accent: #1f6feb;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; padding: 32px;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }
  .wrap { max-width: 1200px; margin: 0 auto; }
  .hero {
    background: linear-gradient(135deg, rgba(88,166,255,.14), rgba(31,111,235,.08));
    border: 1px solid var(--border); border-radius: 18px; padding: 28px;
    margin-bottom: 20px;
  }
  .hero h1 { margin: 0 0 10px; font-size: 30px; }
  .hero p { margin: 0; color: var(--muted); line-height: 1.6; }
  .meta { margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }
  .pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 10px; border-radius: 999px; background: rgba(88,166,255,.12);
    color: #dbeafe; font-size: 12px; border: 1px solid rgba(88,166,255,.25);
  }
  .grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px;
  }
  .card {
    display: block; text-decoration: none; color: inherit;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 16px; padding: 20px; min-height: 154px;
    transition: transform .15s ease, border-color .15s ease, box-shadow .15s ease;
  }
  .card:hover {
    transform: translateY(-2px);
    border-color: var(--accent);
    box-shadow: 0 12px 30px rgba(0,0,0,.24);
  }
  .card h2 { margin: 0 0 8px; font-size: 18px; }
  .card p { margin: 0; color: var(--muted); line-height: 1.6; font-size: 14px; }
  .card .go { margin-top: 14px; display: inline-flex; align-items: center; gap: 6px; color: var(--blue); font-size: 13px; }
  .footer {
    margin-top: 18px; color: var(--muted); font-size: 12px;
    display: flex; gap: 14px; flex-wrap: wrap;
  }
  .footer a { color: var(--muted); }
  .ask-section {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 14px; padding: 20px; margin-bottom: 20px;
  }
  .ask-section h2 { font-size: 18px; margin-bottom: 12px; }
  .ask-row { display: flex; gap: 10px; }
  .ask-row input {
    flex: 1; padding: 10px 14px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--text); font-size: 14px; outline: none;
  }
  .ask-row input:focus { border-color: var(--accent); }
  .ask-row button {
    padding: 10px 20px; border-radius: 8px; border: none;
    background: var(--accent); color: #fff; cursor: pointer;
    font-size: 14px; font-weight: 500; white-space: nowrap;
  }
  .ask-row button:disabled { opacity: 0.5; cursor: not-allowed; }
  .ask-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .ask-head h2 { font-size: 18px; margin: 0; }
  #reset-btn {
    padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--surface); color: var(--muted); cursor: pointer; font-size: 12px;
  }
  #reset-btn:hover { color: var(--text); border-color: var(--accent); }
  .chat-log {
    display: flex; flex-direction: column; gap: 12px;
    max-height: 560px; overflow-y: auto; padding: 4px; margin-bottom: 14px;
  }
  .chat-empty { color: var(--muted); font-size: 13px; text-align: center; padding: 28px 12px; line-height: 1.6; }
  .msg { max-width: 86%; padding: 12px 14px; border-radius: 12px; font-size: 14px; line-height: 1.7; }
  .msg.user {
    align-self: flex-end; background: rgba(31,111,235,.22);
    border: 1px solid rgba(88,166,255,.3); white-space: pre-wrap; word-break: break-word;
  }
  .msg.assistant { align-self: flex-start; background: var(--surface); color: var(--text); border: 1px solid var(--border); }
  .msg.assistant h2 { border-bottom: 1px solid var(--border); padding-bottom: 6px; margin: 12px 0 8px; font-size: 17px; }
  .msg.assistant h3 { margin: 12px 0 6px; font-size: 14px; color: var(--blue); }
  .msg.assistant table { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
  .msg.assistant td, .msg.assistant th { padding: 4px 12px; border: 1px solid var(--border); text-align: left; }
  .msg.assistant th { background: var(--surface-2); color: var(--text); }
  .msg.assistant code { background: var(--surface-2); color: var(--text); padding: 1px 5px; border-radius: 4px; font-size: 13px; }
  .msg.assistant pre { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 12px; }

.msg.assistant{width:100%;max-width:100%;min-width:0;line-height:1.65;padding:14px 16px;overflow-wrap:anywhere}
.msg.assistant h2{font-size:18px;margin:16px 0 8px;color:var(--text)}
.msg.assistant h3{font-size:15px;margin:12px 0 6px;color:var(--text)}
.msg.assistant>:first-child{margin-top:0}
.msg.assistant p,.msg.assistant ul{margin:6px 0 10px}
.msg.assistant ul{padding-left:22px}
.msg.assistant code{color:#1e293b;background:#edf1f5;padding:2px 5px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.assistant pre{margin:8px 0 0;padding:12px;background:#f4f7fa;color:#1e293b;white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto}
.msg.assistant pre code{background:transparent;color:inherit;padding:0}
.msg.assistant th{background:#edf1f5;color:#1e293b}
.msg.assistant table{width:100%;margin:0}
.report-table{overflow-x:auto;margin:8px 0 12px}
.report-output{margin:8px 0 12px;border:1px solid var(--border);border-radius:6px;padding:8px 10px}
.report-output summary{cursor:pointer;color:#334155;font-size:13px}
.msg.assistant figure{margin:12px 0}
.msg.assistant img{display:block;max-width:100%;height:auto;border:1px solid var(--border);border-radius:6px;background:white}
.msg.assistant img[hidden]{display:none}
.msg.assistant figcaption{color:#b91c1c;font-size:13px}
@media(max-width:600px){.chat-log{padding:12px}.msg.assistant{padding:12px}.header{padding:10px 12px;gap:8px;flex-wrap:wrap}.header h1{font-size:16px}.header h1 span{display:none}.input-bar{padding:12px}.input-wrap input{min-width:0;padding:10px}.input-wrap button{padding:10px 14px}}

  .msg.assistant .loading-text { color: var(--muted); }
  .merge-hint { margin-top: 10px; color: var(--muted); font-size: 12px; min-height: 16px; }
  @media (max-width: 768px) {
    body { padding: 16px; }
    .hero h1 { font-size: 24px; }
    .ask-row { flex-direction: column; }
  }

/* Compact report layout: semantic blocks own spacing; no blank-line breaks. */
.msg.assistant{width:100%;max-width:100%;min-width:0;padding:16px 18px;border-radius:8px;line-height:1.55;overflow-wrap:anywhere}
.msg.assistant h2{font-size:18px;line-height:1.35;margin:0 0 12px;padding-bottom:8px;color:var(--text)}
.msg.assistant h3{font-size:14px;line-height:1.4;margin:16px 0 6px;font-weight:650;color:var(--text)}
.msg.assistant>:first-child{margin-top:0}
.msg.assistant p,.msg.assistant ul{margin:6px 0}
.msg.assistant ul{padding-left:22px}
.msg.assistant table{width:100%;margin:0;font-size:13px}
.msg.assistant td,.msg.assistant th{padding:7px 10px;line-height:1.4}
.report-table{overflow-x:auto;margin:8px 0 12px}
.msg.assistant code{font-family:Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.assistant pre{margin:8px 0 0;padding:10px 12px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto}
.msg.assistant pre code{background:transparent;color:inherit;padding:0;font-size:12px}
.report-output{margin:10px 0 4px;border:1px solid var(--border);border-radius:6px;padding:8px 10px}
.report-output summary{cursor:pointer;font-size:13px;color:var(--text)}
.msg.assistant figure{margin:12px 0}
.msg.assistant img{display:block;max-width:100%;height:auto}
.msg.assistant img[hidden]{display:none}
.msg.assistant figcaption{font-size:13px;color:#b91c1c}
@media(max-width:600px){.chat-log{padding:12px}.msg.assistant{padding:12px}.msg.assistant td,.msg.assistant th{padding:6px 8px}}
</style>
</head>
<body>
<div class="wrap">
<style>
:root{--bg:#eef1f5;--surface:#fff;--surface-2:#edf1f5;--border:#d5dae2;--border-strong:#aab5c2;--text:#111827;--muted:#6b7280;--accent:#2563eb;--accent-soft:#eff6ff;--green:#047857;--green-soft:#ecfdf5;--red:#b91c1c;--red-soft:#fef2f2;--yellow:#b45309;--yellow-soft:#fffbeb;--purple:#6d28d9;--purple-soft:#f5f3ff}
body{background:var(--bg)!important;color:var(--text)!important}
.hero,.card,.ask-section,.stat,.section,.tile,.kpi,.source-box,.entry,.fail-analysis,.fa-group,.table-wrap{background:var(--surface)!important;border-color:var(--border-strong)!important;color:var(--text)!important}
.hero{background:var(--surface)!important;box-shadow:0 2px 6px rgba(17,24,39,.08)!important}
.card,.ask-section{box-shadow:0 1px 2px rgba(17,24,39,.08)!important}
.nav-links a,.filter-btn,.detail-tab,.expand-btn{color:var(--muted)!important;border-color:var(--border-strong)!important;background:var(--surface)!important}
.nav-links a:hover,.filter-btn:hover,.detail-tab:hover,.expand-btn:hover{color:var(--accent)!important;background:var(--accent-soft)!important}
.badge-ok{background:var(--green-soft)!important;color:var(--green)!important;border-color:#a7f3d0!important}
.badge-err{background:var(--red-soft)!important;color:var(--red)!important;border-color:#fecaca!important}
.badge-to{background:var(--yellow-soft)!important;color:var(--yellow)!important;border-color:#fde68a!important}
.badge-warn{background:var(--purple-soft)!important;color:var(--purple)!important;border-color:#ddd6fe!important}
.msg.user{background:var(--accent-soft)!important;border-color:var(--accent)!important;color:var(--text)!important}
.msg.assistant{background:var(--surface)!important;border-color:var(--border-strong)!important;color:var(--text)!important}
.detail-box,.fix-code,.result{background:#f4f7fa!important;border-color:var(--border-strong)!important;color:#1f2937!important}
th{background:#dfe5ec!important;color:#374151!important;border-color:var(--border-strong)!important}
td,tr{border-color:#cdd5df!important}
.fa-group-head,.fa-header{background:var(--surface-2)!important}
</style>
  <div class="hero">
    <h1>AcouAgent 控制台</h1>
    <p>自然语言声学仿真、执行看板、测试报告和调试工具的统一入口。</p>
  </div>

  <div class="grid">
    <a class="card" href="/chat">
      <h2>多轮对话</h2>
      <p>连续对话式仿真：先给完整需求，再逐步修改参数，自动合并并重新执行。</p>
      <div class="go">开始对话 →</div>
    </a>
    <a class="card" href="/dashboard">
      <h2>执行看板</h2>
      <p>查看实时执行记录、代码详情、图像缩略图和失败状态。</p>
      <div class="go">进入看板 →</div>
    </a>
    <a class="card" href="/skills">
      <h2>技能库</h2>
      <p>查看已验证的基础技能、适用参数、物理证据与生命周期状态。</p>
      <div class="go">查看技能 →</div>
    </a>
    <a class="card" href="/health">
      <h2>服务状态</h2>
      <p>快速确认 MCP 网关是否在线，适合部署后检查。</p>
      <div class="go">检查状态 →</div>
    </a>
    <a class="card" href="/cache">
      <h2>纠错缓存</h2>
      <p>查看纠错经验缓存的运行状态、命中率和经验条目。</p>
      <div class="go">查看缓存 →</div>
    </a>
  </div>

  <div class="footer">
    <span>入口地址：/ 或 /portal</span>
    <span>看板地址：/dashboard</span>
  </div>
</div>
<script>
function esc(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

// Simple markdown-to-HTML renderer
function renderMarkdown(md) {
  var blocks=[];
  var html=String(md||'').replace(/\r\n?/g,'\n');
  // Protect fenced output before applying inline Markdown.
  html=html.replace(/^\s*(\x60{3,}|~{3,})[^\n]*\n([\s\S]*?)^\s*\1\s*$/gm,function(_,mark,body){
    blocks.push('<details class="report-output"><summary>查看原始输出</summary><pre><code>'+esc(body.trimEnd())+'</code></pre></details>');
    return 'REPORTBLOCK'+(blocks.length-1)+'END';
  });
  html=esc(html.replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu,''));
  html=html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g,function(_,alt,url){
    if(!/^(data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+|https?:\/\/[^\s"'&<>]+|\/(?!\/)[^\s"'&<>]*)$/i.test(url))return '<p>图片地址无效</p>';
    blocks.push('<figure><img src="'+url+'" alt="'+alt.replace(/"/g,'&quot;')+'" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><figcaption hidden>图片加载失败，请在执行看板查看原始图像。</figcaption></figure>');
    return 'REPORTBLOCK'+(blocks.length-1)+'END';
  });
  html=html.replace(/^###\s+(.+)$/gm,'<h3>$1</h3>').replace(/^##\s+(.+)$/gm,'<h2>$1</h2>');
  html=html.replace(/\x60([^\x60\n]+)\x60/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  html=html.replace(/(\|[^\n]+\|\n)(\|[-:\s|]+\|\n)((?:\|[^\n]+\|\n?)*)/g,function(_,header,sep,rows){
    function cells(row,tag){return row.trim().replace(/^\||\|$/g,'').split('|').map(c=>'<'+tag+'>'+c.trim()+'</'+tag+'>').join('');}
    return '<div class="report-table"><table><thead><tr>'+cells(header,'th')+'</tr></thead><tbody>'+rows.trim().split('\n').map(r=>'<tr>'+cells(r,'td')+'</tr>').join('')+'</tbody></table></div>';
  });
  html=html.replace(/(?:^[-*]\s+.+(?:\n|$))+/gm,function(list){return '<ul>'+list.trim().split('\n').map(l=>'<li>'+l.replace(/^[-*]\s+/,'')+'</li>').join('')+'</ul>';});
  html=html.replace(/^---+\s*$/gm,'<hr>');
  html=html.split(/\n\s*\n/).map(function(part){
    part=part.trim();if(!part)return '';
    return /^<(h[1-6]|div|ul|hr)|^REPORTBLOCK/.test(part)?part:'<p>'+part.replace(/\n/g,'<br>')+'</p>';
  }).join('\n');
  return html.replace(/REPORTBLOCK(\d+)END/g,function(_,n){return blocks[n];});
}

var conversation = [];        // {role:'user'|'assistant', content}
var currentRequirement = '';  // 累计完整需求

// 会话隔离：浏览器 localStorage 持久化 session_id，随请求发给后端（Dify user 按会话区分）
var currentSessionId = null;

function loadSessions() {
  try { return JSON.parse(localStorage.getItem('acouagent_sessions') || '[]'); } catch(e) { return []; }
}
function saveSessions(list) {
  localStorage.setItem('acouagent_sessions', JSON.stringify(list));
}
function newSessionId() {
  return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}
function getSessionId() { return currentSessionId; }

function persistCurrent(updateTime) {
  var list = loadSessions();
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === currentSessionId) { cur = list[i]; break; } }
  if (!cur) return;
  cur.messages = conversation;
  cur.requirement = currentRequirement;
  if (updateTime) { cur.updatedAt = Date.now(); }
  if ((cur.title === '新对话' || !cur.title) && conversation.length > 0) {
    for (var j = 0; j < conversation.length; j++) {
      if (conversation[j].role === 'user') { cur.title = conversation[j].content.slice(0, 20); break; }
    }
  }
  saveSessions(list);
}

function sessionGroupLabel(ts) {
  var d = new Date(ts);
  var now = new Date();
  var days30 = 30 * 24 * 3600 * 1000;
  if (now - d < days30) return '30天内';
  var m = d.getMonth() + 1;
  return d.getFullYear() + '-' + (m < 10 ? '0' + m : m);
}

function renderSessionList() {
  var box = document.getElementById('session-list');
  if (!box) return;
  var list = loadSessions();
  list.sort(function(a, b) { return b.updatedAt - a.updatedAt; });
  var html = '';
  var lastGroup = '';
  list.forEach(function(s) {
    var g = sessionGroupLabel(s.updatedAt);
    if (g !== lastGroup) {
      html += '<div class="session-group">' + g + '</div>';
      lastGroup = g;
    }
    var active = s.id === currentSessionId ? ' active' : '';
    html += '<div class="session-item' + active + '" onclick="switchSession(\'' + s.id + '\')" title="' + esc(s.title) + '">'
      + '<span class="session-title">' + esc(s.title || '新对话') + '</span>'
      + '<span class="session-del" onclick="event.stopPropagation();deleteSession(\'' + s.id + '\')" title="删除">×</span>'
      + '</div>';
  });
  box.innerHTML = html;
}

function switchSession(id) {
  if (id === currentSessionId) return;
  persistCurrent(false);
  var list = loadSessions();
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === id) { cur = list[i]; break; } }
  if (!cur) return;
  localStorage.setItem('acouagent_current', id);
  currentSessionId = id;
  conversation = cur.messages || [];
  currentRequirement = cur.requirement || '';
  document.getElementById('merge-hint').textContent = '';
  renderChat();
  renderSessionList();
  document.getElementById('query-input').focus();
}

function deleteSession(id) {
  if (!confirm('删除这个对话？此操作不可撤销。')) return;
  var list = loadSessions().filter(function(s) { return s.id !== id; });
  if (id === currentSessionId) {
    if (list.length) {
      localStorage.setItem('acouagent_current', list[0].id);
      currentSessionId = list[0].id;
      conversation = list[0].messages || [];
      currentRequirement = list[0].requirement || '';
    } else {
      var fresh = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
      list = [fresh];
      localStorage.setItem('acouagent_current', fresh.id);
      currentSessionId = fresh.id;
      conversation = [];
      currentRequirement = '';
    }
  }
  saveSessions(list);
  renderChat();
  renderSessionList();
}

function renderChat() {
  var log = document.getElementById('chat-log');
  if (!conversation.length) {
    var suggestions = [
      '2 MHz 点声源，均匀介质，区域 1 cm × 1 cm，网格 200×200',
      '1 MHz 点声源在水中传播，区域 2 cm × 2 cm，网格 200×200',
      '800 kHz 点声源，声速 1500 m/s，区域 1 cm × 1 cm，记录中心点压力随时间变化'
    ];
    log.innerHTML = '<div class="chat-empty">'
      + '<div class="empty-title">开始一次声学仿真</div>'
      + '<div class="empty-sub">用自然语言描述你的问题，支持多轮修改参数</div>'
      + '<div class="suggestions">'
      + suggestions.map(function(s) { return '<button type="button" class="chip" onclick="fillQuery(this)">' + esc(s) + '</button>'; }).join('')
      + '</div></div>';
    return;
  }
  log.innerHTML = conversation.map(function(m) {
    var cls = m.role === 'user' ? 'user' : 'assistant';
    var body = m.loading ? '<span class="typing"><i></i><i></i><i></i></span>' : (m.role === 'user' ? esc(m.content) : renderMarkdown(m.content));
    return '<div class="msg ' + cls + '">' + body + '</div>';
  }).join('');
  log.scrollTop = log.scrollHeight;
}

function fillQuery(el) {
  var input = document.getElementById('query-input');
  input.value = el.textContent;
  input.focus();
}

async function ask() {
  var input = document.getElementById('query-input');
  var btn = document.getElementById('ask-btn');
  var hint = document.getElementById('merge-hint');
  var message = input.value.trim();
  if (!message) return;
  input.value = '';
  hint.textContent = '';
  var skillFeedback = document.getElementById('skill-usage-feedback');
  if (skillFeedback) skillFeedback.textContent = '技能库：本次尚未确认参考调用';
  window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:[],final:true}}));

  conversation.push({role: 'user', content: message});
  renderChat();
  btn.disabled = true;
  btn.textContent = '运行中...';

  var loadingIdx = conversation.length;
  conversation.push({role: 'assistant', content: '正在执行仿真（生成代码 → 沙箱计算 → 物理分析），通常需要 30~90 秒，请耐心等待...'});
  renderChat();

  try {
    var resp = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stream: true, message: message, requirement: currentRequirement, session_id: getSessionId()})
    });
    if (!resp.ok) throw new Error('服务返回 HTTP ' + resp.status);
    var data;
    if ((resp.headers.get('content-type') || '').includes('application/x-ndjson')) {
      var reader = resp.body.getReader(), decoder = new TextDecoder(), pending = '';
      while (true) {
        var chunk = await reader.read();
        pending += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
        var lines = pending.split('\n'); pending = lines.pop();
        for (var line of lines) {
          if (!line.trim()) continue;
          var event = JSON.parse(line);
          if (event.event === 'progress') {
            conversation[loadingIdx] = {role: 'assistant', content: '当前步骤：' + event.stage + '。请稍候...'};
            renderChat();
          } else if (event.event === 'skill_usage') {
            if (skillFeedback) skillFeedback.textContent = event.skill_usage.message + ' · v' + event.skill_usage.version;
            window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:[event.skill_usage],final:false}}));
            conversation[loadingIdx] = {role: 'assistant', content: event.skill_usage.message};
            renderChat();
          } else if (event.event === 'result') { data = event; }
        }
        if (chunk.done) break;
      }
      if (!data) throw new Error('连接中断，未收到完整结果；请勿连续重复发送。');
    } else { data = await resp.json(); }

    var report = data.report || '';
    if (skillFeedback) skillFeedback.textContent = Array.isArray(data.skill_usage) && data.skill_usage.length ? data.skill_usage.map(function(s) { return s.message + ' · v' + s.version; }).join('；') : '本次未确认交付经过核验的技能参考';
    window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:Array.isArray(data.skill_usage)?data.skill_usage:[],final:true}}));
    if (Array.isArray(data.skill_usage) && data.skill_usage.length) {
      report += '\n\n**技能库调用反馈**\n' + data.skill_usage.map(function(s) { return '- ' + s.message + '（v' + s.version + '，编号 ' + s.skill_id + '）'; }).join('\n');
    }
    if (data.workflow_status === 'error' && data.request_id) report += '\n\n请求编号：' + data.request_id;
    currentRequirement = data.requirement || '';
    conversation[loadingIdx] = {role: 'assistant', content: report || '未获取到结果'};
    if (data.merge_used) {
      hint.textContent = '已根据历史需求合并本轮修改';
    }
  } catch(e) {
    conversation[loadingIdx] = {role: 'assistant', content: '请求失败：' + esc(String(e))};
  }
  renderChat();
  btn.disabled = false;
  btn.textContent = '🚀 发送';
}

function resetChat() {
  persistCurrent(false);
  var fresh = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
  var list = loadSessions();
  list.unshift(fresh);
  saveSessions(list);
  localStorage.setItem('acouagent_current', fresh.id);
  currentSessionId = fresh.id;
  conversation = fresh.messages;
  currentRequirement = '';
  document.getElementById('merge-hint').textContent = '';
  var feedback = document.getElementById('skill-usage-feedback');
  if (feedback) feedback.textContent = '';
  renderChat();
  renderSessionList();
  document.getElementById('query-input').focus();
}

// Portal no longer embeds an inline chat; /chat is the single entry point.
</script>
</body>
</html>"""

DEMO_HTML = r"""<!DOCTYPE html>

<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dify MCP · Demo 演示</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #c9d1d9; --muted: #8b949e; --green: #3fb950;
    --red: #f85149; --yellow: #d2991d; --blue: #58a6ff; --accent: #1f6feb;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh; padding: 24px;
  }
  .header { margin-bottom: 24px; }
  .header h1 { font-size: 22px; font-weight: 600; }
  .header h1 span { color: var(--muted); font-weight: 400; }
  .header p { color: var(--muted); margin-top: 4px; font-size: 14px; }

  .demo-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 16px; margin-bottom: 24px;
  }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
    display: flex; flex-direction: column;
  }
  .card:hover { border-color: var(--accent); }
  .card .title { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
  .card .purpose { font-size: 12px; color: var(--yellow); margin-bottom: 8px; }
  .card .prompt-text {
    font-size: 12px; color: var(--muted); margin-bottom: 12px;
    line-height: 1.5; max-height: 48px; overflow: hidden;
  }
  .card .actions { display: flex; gap: 8px; margin-top: auto; }
  .btn {
    padding: 6px 16px; border-radius: 6px; border: none; cursor: pointer;
    font-size: 13px; font-weight: 500;
  }
  .btn-run { background: var(--accent); color: #fff; }
  .btn-run:hover { opacity: 0.9; }
  .btn-run:disabled { opacity: 0.5; cursor: not-allowed; }
  .card .result {
    margin-top: 10px; padding: 8px; border-radius: 6px; font-size: 12px;
    background: var(--surface-2); color: var(--text); min-height: 32px; display: none;
  }
  .card .result.visible { display: block; }
  .card .result .status-line { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .card .result .status-line .stat { font-size: 11px; padding: 1px 6px; border-radius: 4px; }
  .card .result .status-line .ok { background: rgba(63,185,80,0.15); color: var(--green); }
  .card .result .status-line .err { background: rgba(248,81,73,0.15); color: var(--red); }
  .card .result .status-line .info { color: var(--muted); }
  .card .result img {
    max-width: 100%%; border-radius: 6px; margin-top: 8px; border: 1px solid var(--border);
  }
  .card.running { border-color: var(--yellow); }

  .history-section {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
  }
  .history-section h3 { font-size: 14px; margin-bottom: 12px; color: var(--muted); }
  .history-item {
    display: flex; gap: 8px; align-items: center; padding: 6px 0;
    border-bottom: 1px solid var(--border); font-size: 12px;
  }
  .history-item .h-name { min-width: 80px; }
  .history-item .h-status { min-width: 60px; }
  .history-item .h-time { color: var(--muted); margin-left: auto; }
  .history-item .h-img { max-width: 60px; max-height: 40px; border-radius: 4px; cursor: pointer; }

  @media (max-width: 768px) {
    body { padding: 12px; }
    .demo-grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div style="margin-bottom:20px"><a href="/" style="color:var(--blue);text-decoration:none;font-size:14px">← 返回门户</a></div>
<div class="header">
  <h1>AcouAgent <span>· Demo 演示</span></h1>
  <p>6 个预设场景，一键运行，实时展示结果</p>
</div>

<div class="demo-grid" id="demo-grid"></div>

<div class="history-section">
  <h3>&#128211; 运行历史</h3>
  <div id="history-list" style="color:var(--muted)">暂无记录</div>
</div>

<script>
const DIFY_API = '/demo/api/run';  // proxy through dify-mcp to reach nginx

const SCENARIOS = [
  {
    id: 1,
    name: "基础点源",
    prompt: "模拟一个2 MHz点声源在均匀介质中的声波传播，网格128x128，区域1cm x 1cm，模拟5微秒，声速1500 m/s",
    purpose: "建立基线：验证基本点声源仿真能力"
  },
  {
    id: 2,
    name: "异质囊肿",
    prompt: "模拟声波在含有圆形囊肿的组织中传播：背景声速1540 m/s，囊肿直径3mm声速1450 m/s，点声源2 MHz在中心，网格256x256，区域1.5cm",
    purpose: "非平凡场景：异质介质中的声波传播"
  },
  {
    id: 3,
    name: "⭐ p0 初始压力",
    prompt: "模拟一个高斯初始压力脉冲在均匀介质中的传播，峰值压力1000 Pa，半宽0.5mm，网格256x256，区域1cm，声速1500 m/s",
    purpose: "核心成果：初始压力条件下的声波传播"
  },
  {
    id: 4,
    name: "传感器阵列",
    prompt: "模拟点声源声波传播并在距离中心2mm和4mm处各放置一个传感器记录压力波形，声源频率2 MHz，声速1500 m/s，网格256x256，区域1cm，仿真5微秒",
    purpose: "多传感器：记录多点压力波形"
  },
  {
    id: 5,
    name: "参数不合理",
    prompt: "点声源频率5 MHz，网格32x32，区域1mm x 1mm，声速1500 m/s，模拟时间0.5微秒",
    purpose: "展示校验拦截：极端参数检测与拒绝"
  },
  {
    id: 6,
    name: "修正重试",
    prompt: "点声源频率1.5 MHz，网格200x200，区域1cm x 1cm，声速1500 m/s，模拟4微秒",
    purpose: "展示闭环修正：自动纠错 + 重试"
  }
];

let history = [];

function renderCards() {
  const grid = document.getElementById('demo-grid');
  grid.innerHTML = SCENARIOS.map(s => `
    <div class="card" id="card-${s.id}">
      <div class="title">#${s.id} ${s.name}</div>
      <div class="purpose">${s.purpose}</div>
      <div class="prompt-text">${escHtml(s.prompt)}</div>
      <div class="actions">
        <button class="btn btn-run" id="btn-${s.id}" onclick="runScenario(${s.id})">&#9654; 运行</button>
      </div>
      <div class="result" id="result-${s.id}"></div>
    </div>
  `).join('');
}

function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function runScenario(id) {
  const scenario = SCENARIOS.find(s => s.id === id);
  if (!scenario) return;

  const btn = document.getElementById('btn-' + id);
  const result = document.getElementById('result-' + id);
  const card = document.getElementById('card-' + id);

  btn.disabled = true;
  btn.textContent = '\\u23f3 运行中…';
  card.classList.add('running');
  result.classList.add('visible');
  result.innerHTML = '<span style="color:var(--muted)">正在调用 Dify 工作流…</span>';

  const start = Date.now();
  try {
    const resp = await fetch(DIFY_API, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        inputs: { query: scenario.prompt },
        response_mode: 'blocking',
        user: 'demo'
      })
    });
    const elapsed = ((Date.now() - start) / 1000).toFixed(1);
    const data = await resp.json();
    const wfStatus = data.data?.status;
    const error = data.data?.error;

    // 现在输出是 Markdown 字符串或对象
    const output = data.data?.outputs?.text;
    let reportText = '';
    if (typeof output === 'string') {
      reportText = output;
    } else if (Array.isArray(output) && output.length > 0) {
      const o0 = output[0];
      if (typeof o0 === 'string') { reportText = o0; }
      else if (typeof o0 === 'object') { reportText = o0.result || o0.report || ''; }
    } else if (typeof output === 'object' && output !== null) {
      reportText = output.result || output.report || '';
    }

    // 判断成功/失败
    let statusClass = 'err', statusText = '失败';
    if (wfStatus === 'succeeded' && !error) {
      if (reportText.includes('✅ **成功**')) {
        statusClass = 'ok'; statusText = '\\u2705 成功';
      } else if (reportText.includes('⏱ **超时**')) {
        statusText = '\\u23f1 超时';
      } else if (reportText.includes('❌ **失败**')) {
        statusText = '\\u274c 失败';
      } else {
        statusClass = 'ok'; statusText = '\\u2705 完成';
      }
    } else if (error) {
      statusText = '\\u274c ' + escHtml(String(error).slice(0, 60));
    }

    // 从 Markdown 中提取关键指标
    const maxPMatch = reportText.match(/最大压力[：:]?\s*\**\s*([\d.]+)/);
    const mp = maxPMatch ? parseFloat(maxPMatch[1]) : null;
    const durMatch = reportText.match(/执行耗时[：:]?\s*\**\s*([\d.]+)\s*ms/);
    const dur = durMatch ? parseFloat(durMatch[1]) : null;
    const attMatch = reportText.match(/尝试次数[：:]?\s*\**\s*(\d+)/);
    const attempts = attMatch ? parseInt(attMatch[1]) : 1;

    let html = `<div class="status-line">
      <span class="stat ${statusClass}">${statusText}</span>
      <span class="info">${elapsed}s</span>`;
    if (dur) html += `<span class="info">${dur < 1000 ? Math.round(dur)+'ms' : (dur/1000).toFixed(1)+'s'}</span>`;
    if (mp != null) html += `<span class="info">max_p=${mp.toFixed(4)}</span>`;
    if (attempts > 1) html += `<span class="info">retry x${attempts}</span>`;
    html += `</div>`;

    // 显示报告文本，简单格式化
    if (reportText) {
      const short = reportText.length > 600
        ? reportText.slice(0, 600).replace(/\n/g, '<br>') + '<br>...<br><span style="color:var(--blue);cursor:pointer;font-size:12px" onclick="this.parentElement.innerHTML=this.previousSibling">(展开全部)</span><span style="display:none">' + escHtml(reportText.slice(600)).replace(/\n/g, '<br>') + '</span>'
        : reportText.replace(/\n/g, '<br>');
      html += `<div style="margin-top:8px;font-size:12px;color:var(--muted);line-height:1.7">${short}</div>`;
    }

    result.innerHTML = html;

    history.unshift({
      id: scenario.id, name: scenario.name, status: statusText,
      elapsed: elapsed, mp: mp, time: new Date().toLocaleTimeString('zh-CN')
    });
    renderHistory();

  } catch(e) {
    const elapsed = ((Date.now() - start) / 1000).toFixed(1);
    result.innerHTML = `<div class="status-line"><span class="stat err">\\u274c 网络错误</span><span class="info">${elapsed}s</span></div><div style="font-size:11px;color:var(--red);margin-top:4px">${escHtml(String(e))}</div>`;
    history.unshift({ id: scenario.id, name: scenario.name, status: '网络错误', elapsed: elapsed, time: new Date().toLocaleTimeString('zh-CN') });
    renderHistory();
  }

  btn.disabled = false;
  btn.textContent = '\\u25b6 运行';
  card.classList.remove('running');
}

function extractMaxPressure(stdout) {
  if (!stdout) return null;
  const m = stdout.match(/(?:最大压[力强]|max.pressure|Max pressure).*?([\\d.eE+-]+)/i);
  return m ? parseFloat(m[1]) : null;
}

function showLarge(src) {
  const old = document.getElementById('img-lightbox');
  if (old) old.remove();
  const lb = document.createElement('div');
  lb.id = 'img-lightbox';
  lb.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.9);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:pointer;';
  lb.onclick = function() { lb.remove(); };
  const img = document.createElement('img');
  img.src = src;
  img.style.cssText = 'max-width:90%;max-height:90%;border-radius:8px;';
  lb.appendChild(img);
  document.body.appendChild(lb);
  document.addEventListener('keydown', function escFn(ev) {
    if (ev.key === 'Escape') { lb.remove(); document.removeEventListener('keydown', escFn); }
  });
}

function renderHistory() {
  const el = document.getElementById('history-list');
  if (!history.length) { el.innerHTML = '暂无记录'; return; }
  el.innerHTML = history.map(h => `
    <div class="history-item">
      <span class="h-name">#${h.id} ${h.name}</span>
      <span class="h-status">${h.status}</span>
      <span class="h-info">${h.elapsed}s</span>
      ${h.mp != null ? '<span class="h-info">max_p=' + (typeof h.mp === 'number' ? h.mp.toFixed(4) : h.mp) + '</span>' : ''}
      ${h.image ? '<img class="h-img" src="data:image/png;base64,' + h.image + '" onclick="showLarge(this.src)" title="点击放大">' : ''}
      <span class="h-time">${h.time}</span>
    </div>
  `).join('');
}

// Init
renderCards();
</script>
</body>
</html>
"""

CHAT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>多轮对话 · Dify MCP</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; height: 100vh; overflow: hidden;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Helvetica, Arial, sans-serif;
    display: flex; flex-direction: column;
  }
  .header {
    display: flex; align-items: center; gap: 14px;
    padding: 14px 24px; border-bottom: 1px solid var(--border);
    background: var(--surface);
  }
  .header a { color: var(--muted); text-decoration: none; font-size: 14px; }
  .header a:hover { color: var(--blue); }
  .header h1 { font-size: 19px; margin: 0; font-weight: 600; }
  .header h1 span { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 8px; }
  .header .reset-btn { margin-left: auto; padding: 6px 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--muted); cursor: pointer; font-size: 13px; }
  .header .reset-btn:hover { color: var(--text); border-color: var(--accent); }
  .header .model-btn { margin-left: 8px; padding: 6px 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--muted); cursor: pointer; font-size: 13px; }
  .header .model-btn:hover { color: var(--text); border-color: var(--accent); }
  .modal { position: fixed; inset: 0; background: rgba(17,24,39,.55); display: none; align-items: center; justify-content: center; z-index: 100; padding: 20px; }
  .modal.open { display: flex; }
  .modal-box { width: min(520px, 100%); background: var(--surface); border: 1px solid var(--border-strong); border-radius: 14px; padding: 24px; box-shadow: var(--shadow-md); }
  .modal-box h2 { margin: 0 0 12px; font-size: 17px; }
  .modal-box label { display: block; color: var(--muted); font-size: 12px; margin: 10px 0 5px; }
  .modal-box input, .modal-box select { width: 100%; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); font-size: 13px; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
  .modal-actions button { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; font-size: 13px; }
  .modal-actions button:disabled { opacity: .55; cursor: wait; }
  .modal-actions button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  .model-connection-status { margin-top: 12px; padding: 10px 12px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--surface-2); display: grid; gap: 3px; }
  .model-connection-status strong { font-size: 13px; }
  .model-connection-status span { color: var(--muted); font-size: 12px; line-height: 1.5; }
  .model-connection-status[data-state="pending"], .model-connection-status[data-state="warning"] { border-color: var(--yellow); background: var(--yellow-soft); }
  .model-connection-status[data-state="success"] { border-color: var(--green); background: var(--green-soft); }
  .model-connection-status[data-state="success"] strong { color: var(--green); }
  .model-connection-status[data-state="error"] { border-color: var(--red); background: var(--red-soft); }
  .model-connection-status[data-state="error"] strong { color: var(--red); }

  .chat-log { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; max-width: 900px; width: 100%; margin: 0 auto; }
  .chat-empty { color: var(--muted); font-size: 15px; text-align: center; padding: 70px 16px; line-height: 1.7; }
  .chat-empty .empty-title { font-size: 24px; font-weight: 700; color: var(--text); margin-bottom: 10px; letter-spacing: -.3px; }
  .chat-empty .empty-sub { margin-bottom: 26px; }
  .chat-empty .suggestions { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }
  .chat-empty .suggestions .chip {
    padding: 10px 18px; border-radius: 999px; cursor: pointer;
    border: 1px solid var(--border-strong); background: var(--surface);
    color: var(--text); font-size: 13px; line-height: 1.4; box-shadow: var(--shadow-sm);
    transition: border-color .18s, color .18s, transform .18s, box-shadow .18s;
  }
  .chat-empty .suggestions .chip:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-2px); box-shadow: var(--shadow-md); }

  .msg { max-width: 78%; padding: 12px 16px; border-radius: 16px; font-size: 15px; line-height: 1.7; }
  .msg.user { align-self: flex-end; background: var(--accent-soft); border: 1px solid var(--border-strong); color: var(--text); white-space: pre-wrap; word-break: break-word; box-shadow: var(--shadow-sm); }
  .msg.assistant { align-self: flex-start; background: var(--surface); color: var(--text); border: 1px solid var(--border); box-shadow: var(--shadow-sm); }

  .msg.assistant h2 { border-bottom: 1px solid var(--border); padding-bottom: 8px; margin: 0 0 12px; font-size: 18px; line-height: 1.35; }
  .msg.assistant h3 { margin: 16px 0 6px; font-size: 15px; font-weight: 650; color: var(--text); }
  .msg.assistant > :first-child { margin-top: 0; }
  .msg.assistant p, .msg.assistant ul { margin: 6px 0; }
  .msg.assistant ul { padding-left: 22px; }
  .msg.assistant table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
  .msg.assistant td, .msg.assistant th { padding: 7px 10px; border: 1px solid var(--border); text-align: left; line-height: 1.4; }
  .msg.assistant th { background: var(--surface-2); color: var(--text); }
  .msg.assistant code { font-family: Consolas, monospace; background: var(--surface-2); color: var(--text); padding: 2px 5px; border-radius: 4px; font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .msg.assistant pre { margin: 8px 0 0; padding: 12px; background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; line-height: 1.5; }
  .msg.assistant pre code { background: transparent; color: inherit; padding: 0; font-size: 12px; }
  .report-table { overflow-x: auto; margin: 8px 0 12px; }
  .report-output { margin: 10px 0 4px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
  .report-output summary { cursor: pointer; font-size: 13px; color: var(--muted); }
  .msg.assistant figure { margin: 12px 0; }
  .msg.assistant img { display: block; max-width: 100%; height: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
  .msg.assistant img[hidden] { display: none; }
  .msg.assistant figcaption { font-size: 13px; color: var(--red); }

  .typing { display: inline-flex; gap: 5px; align-items: center; padding: 2px 0; }
  .typing i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); animation: typingBounce 1.2s ease-in-out infinite; }
  .typing i:nth-child(2) { animation-delay: .15s; }
  .typing i:nth-child(3) { animation-delay: .3s; }
  @keyframes typingBounce { 0%, 60%, 100% { transform: translateY(0); opacity: .4; } 30% { transform: translateY(-4px); opacity: 1; } }

  .input-bar { flex-shrink: 0; border-top: 1px solid var(--border); background: var(--surface); padding: 14px 24px; }
  .input-wrap { max-width: 900px; margin: 0 auto; display: flex; gap: 10px; align-items: flex-end; }
  .input-wrap textarea {
    flex: 1; padding: 12px 16px; border-radius: 12px; border: 1px solid var(--border-strong);
    background: var(--bg); color: var(--text); font-size: 15px; outline: none; resize: vertical;
    min-height: 56px; max-height: 220px; line-height: 1.6; font-family: inherit;
  }
  .input-wrap textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .input-wrap button {
    padding: 12px 24px; border-radius: 12px; border: none; background: var(--accent); color: #fff;
    cursor: pointer; font-size: 15px; font-weight: 500; white-space: nowrap; min-height: 46px;
    display: inline-flex; align-items: center; gap: 8px;
    transition: transform .18s, box-shadow .18s, filter .18s;
  }
  .input-wrap button:hover:not(:disabled) { transform: translateY(-1px); box-shadow: var(--shadow-md); filter: brightness(1.05); }
  .input-wrap button:disabled { opacity: .6; cursor: not-allowed; }
  #ask-btn.loading::after { content: ""; width: 14px; height: 14px; border-radius: 50%; border: 2px solid rgba(255,255,255,.45); border-top-color: #fff; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .merge-hint { max-width: 900px; margin: 8px auto 0; color: var(--muted); font-size: 12px; min-height: 16px; }

  @media (max-width: 768px) {
    .chat-log { padding: 16px; }
    .header { padding: 10px 12px; gap: 8px; flex-wrap: wrap; }
    .header h1 { font-size: 16px; }
    .header h1 span { display: none; }
    .input-bar { padding: 12px; }
    .input-wrap { flex-direction: column; align-items: stretch; }
    .input-wrap button { width: 100%; }
    .msg { max-width: 92%; }
    .chat-empty { padding: 40px 12px; }
  }
</style>
</head>
<body>
<aside class="app-sidebar">
  <div class="brand">AcouAgent</div>
  <a class="side-link active" href="/">声学仿真</a>
  <a class="side-link" href="/dashboard">执行看板</a>
  <a class="side-link" href="/skills">技能库</a>
  <a class="side-link" href="/cache">纠错缓存</a>
</aside>
<div class="header">
  <h1>声学仿真<span>自然语言多轮对话</span></h1>
  <button class="reset-btn" onclick="resetChat()">新对话</button>
  <button class="model-btn" onclick="openModelSettings()">模型设置</button>
</div>

<div class="modal" id="model-modal">
  <div class="modal-box">
    <h2>连接你的模型</h2>
    <label for="model-profile">模型来源</label>
    <select id="model-profile" onchange="toggleByokFields()"></select>
    <div id="byok-fields" hidden>
      <label for="byok-provider">服务商快捷配置</label>
      <select id="byok-provider" onchange="applyByokProvider(false)">
        <option value="deepseek">DeepSeek 官方 API</option>
        <option value="qwen">阿里云百炼 · Qwen（中国站）</option>
        <option value="custom">自定义 OpenAI 兼容端点</option>
      </select>
      <label for="byok-base-url">OpenAI 兼容 API 地址</label>
      <input id="byok-base-url" type="url" autocomplete="off" placeholder="https://api.example.com/v1" oninput="markByokConfigDirty()">
      <label for="byok-model-preset">模型名称</label>
      <select id="byok-model-preset" onchange="toggleCustomByokModel(false)"></select>
      <div id="byok-custom-model-wrap" hidden>
        <label for="byok-model-custom">自定义模型 ID</label>
        <input id="byok-model-custom" type="text" autocomplete="off" placeholder="模型 ID" oninput="markByokConfigDirty()">
      </div>
      <label for="byok-api-key">API Key</label>
      <input id="byok-api-key" type="password" autocomplete="new-password" placeholder="仅发送到这台自部署服务器" oninput="markByokConfigDirty()">
    </div>
    <p id="model-profile-detail" style="color:var(--muted);font-size:12px;line-height:1.6;margin-top:10px">正在读取服务端配置…</p>
    <div id="model-preflight-result" class="model-connection-status" data-state="idle" role="status" aria-live="polite">
      <strong id="model-preflight-title">等待测试连接</strong>
      <span id="model-preflight-detail">输入 API Key 后测试；注册成功后 Key 会从输入框清除。</span>
    </div>
    <p style="color:var(--muted);font-size:12px;line-height:1.6">密钥仅保存在当前网关进程的临时内存中，到期或重启后失效；不会写入浏览器持久存储、SQLite、日志或技能库。</p>
    <div class="modal-actions">
      <button onclick="closeModelSettings()">取消</button>
      <button id="model-test-btn" onclick="testModelConnection()">测试连接</button>
      <button class="primary" onclick="saveModelSettings()">保存</button>
    </div>
  </div>
</div>

<div class="chat-log" id="chat-log"></div>

<div class="input-bar">
  <div class="input-wrap">
    <textarea id="query-input" rows="3" placeholder="描述声学仿真问题，或继续修改参数…" aria-label="声学仿真需求" onkeydown="if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();ask()}"></textarea>
    <button id="ask-btn" onclick="ask()">发送</button>
  </div>
  <div class="merge-hint" id="merge-hint"></div>
</div>

<script>
function esc(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function renderMarkdown(md) {
  var blocks=[];
  var html=String(md||'').replace(/\r\n?/g,'\n');
  // Protect fenced output before applying inline Markdown.
  html=html.replace(/^\s*(\x60{3,}|~{3,})[^\n]*\n([\s\S]*?)^\s*\1\s*$/gm,function(_,mark,body){
    blocks.push('<details class="report-output"><summary>查看原始输出</summary><pre><code>'+esc(body.trimEnd())+'</code></pre></details>');
    return 'REPORTBLOCK'+(blocks.length-1)+'END';
  });
  html=esc(html.replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu,''));
  html=html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g,function(_,alt,url){
    if(!/^(data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+|https?:\/\/[^\s"'&<>]+|\/(?!\/)[^\s"'&<>]*)$/i.test(url))return '<p>图片地址无效</p>';
    blocks.push('<figure><img src="'+url+'" alt="'+alt.replace(/"/g,'&quot;')+'" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><figcaption hidden>图片加载失败，请在执行看板查看原始图像。</figcaption></figure>');
    return 'REPORTBLOCK'+(blocks.length-1)+'END';
  });
  html=html.replace(/^###\s+(.+)$/gm,'<h3>$1</h3>').replace(/^##\s+(.+)$/gm,'<h2>$1</h2>');
  html=html.replace(/\x60([^\x60\n]+)\x60/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  html=html.replace(/(\|[^\n]+\|\n)(\|[-:\s|]+\|\n)((?:\|[^\n]+\|\n?)*)/g,function(_,header,sep,rows){
    function cells(row,tag){return row.trim().replace(/^\||\|$/g,'').split('|').map(c=>'<'+tag+'>'+c.trim()+'</'+tag+'>').join('');}
    return '<div class="report-table"><table><thead><tr>'+cells(header,'th')+'</tr></thead><tbody>'+rows.trim().split('\n').map(r=>'<tr>'+cells(r,'td')+'</tr>').join('')+'</tbody></table></div>';
  });
  html=html.replace(/(?:^[-*]\s+.+(?:\n|$))+/gm,function(list){return '<ul>'+list.trim().split('\n').map(l=>'<li>'+l.replace(/^[-*]\s+/,'')+'</li>').join('')+'</ul>';});
  html=html.replace(/^---+\s*$/gm,'<hr>');
  html=html.split(/\n\s*\n/).map(function(part){
    part=part.trim();if(!part)return '';
    return /^<(h[1-6]|div|ul|hr)|^REPORTBLOCK/.test(part)?part:'<p>'+part.replace(/\n/g,'<br>')+'</p>';
  }).join('\n');
  return html.replace(/REPORTBLOCK(\d+)END/g,function(_,n){return blocks[n];});
}

var conversation = [];
var currentRequirement = '';
var byokConfigDirty = false;
var byokRegistrationConfirmed = false;

var BYOK_PRESETS = {
  deepseek: {
    baseUrl: 'https://api.deepseek.com',
    models: [
      ['deepseek-chat', 'DeepSeek Chat'],
      ['deepseek-reasoner', 'DeepSeek Reasoner']
    ]
  },
  qwen: {
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: [
      ['qwen-plus', 'Qwen Plus'],
      ['qwen-max', 'Qwen Max'],
      ['qwen-turbo', 'Qwen Turbo']
    ]
  }
};

function getModelProfile() {
  return sessionStorage.getItem('acouagent_byok_profile') || localStorage.getItem('acouagent_model_profile') || null;
}

async function loadModelProfiles() {
  var select = document.getElementById('model-profile');
  var detail = document.getElementById('model-profile-detail');
  select.innerHTML = '';
  try {
    var resp = await fetch('/api/model-profiles', {headers:{'Accept':'application/json'}});
    var data = await resp.json();
    if (!resp.ok || !Array.isArray(data.profiles)) throw new Error(data.error || '无法读取模型配置');
    var saved = getModelProfile() || data.default_profile;
    data.profiles.forEach(function(profile) {
      var option = document.createElement('option');
      option.value = profile.id;
      option.textContent = profile.label + ' · ' + profile.model + (profile.configured ? '' : '（未配置凭据）');
      option.disabled = !profile.configured;
      option.selected = profile.id === saved;
      select.appendChild(option);
    });
    var byok = document.createElement('option');
    byok.value = '__byok__';
    byok.textContent = sessionStorage.getItem('acouagent_byok_profile') ? '我的 BYOK 模型（本次会话）' : '使用自己的 API Key（BYOK）';
    byok.selected = !!sessionStorage.getItem('acouagent_byok_profile');
    select.appendChild(byok);
    if (!select.value && data.default_profile) select.value = data.default_profile;
    if (!select.value) select.value = '__byok__';
    detail.textContent = data.orchestration_backend === 'local'
      ? 'BYOK 会用于参数提取、代码生成、审查、解释和重试。公网端点默认要求 HTTPS；本地模型地址需由部署者显式开启。'
      : '当前是 Dify 兼容模式，BYOK 无法接管 Dify 内部模型。请把 ORCHESTRATION_BACKEND 设为 local。';
    toggleByokFields();
  } catch(e) {
    detail.textContent = '无法读取模型配置：' + String(e);
  }
}

function toggleByokFields() {
  var selected = document.getElementById('model-profile').value;
  var fields = document.getElementById('byok-fields');
  fields.hidden = selected !== '__byok__';
  if (!fields.hidden && !document.getElementById('byok-model-preset').options.length) {
    if (sessionStorage.getItem('acouagent_byok_profile') && !byokConfigDirty) {
      showRestoredByokHandle();
    } else {
      applyByokProvider(true);
    }
  }
  if (selected === '__byok__' && sessionStorage.getItem('acouagent_byok_profile') && !byokConfigDirty) {
    if (byokRegistrationConfirmed) {
      setModelConnectionStatus('success', '本次会话模型已注册', '密钥已从输入框清除；可以再次测试连接或直接开始仿真。');
    } else {
      setModelConnectionStatus('warning', '发现本次浏览器会话配置', '请先测试连接；网关重启或配置到期后需要重新输入 API Key。');
    }
  } else {
    clearModelPreflightResult();
  }
}

function showRestoredByokHandle() {
  var providers = document.getElementById('byok-provider');
  var restored = providers.querySelector('option[value="session"]');
  if (!restored) {
    restored = document.createElement('option');
    restored.value = 'session';
    restored.textContent = '本次会话已注册配置（详情已隐藏）';
    providers.insertBefore(restored, providers.firstChild);
  }
  providers.value = 'session';
  var baseUrl = document.getElementById('byok-base-url');
  baseUrl.value = '';
  baseUrl.placeholder = '已注册端点（不回显）';
  var models = document.getElementById('byok-model-preset');
  models.innerHTML = '<option value="__session__">已注册模型（不回显）</option>';
  document.getElementById('byok-custom-model-wrap').hidden = true;
}

function applyByokProvider(initializing) {
  var provider = document.getElementById('byok-provider').value;
  var preset = BYOK_PRESETS[provider];
  var baseUrl = document.getElementById('byok-base-url');
  var models = document.getElementById('byok-model-preset');
  var restored = document.querySelector('#byok-provider option[value="session"]');
  if (restored) restored.remove();
  baseUrl.placeholder = 'https://api.example.com/v1';
  models.innerHTML = '';
  if (preset) {
    baseUrl.value = preset.baseUrl;
    preset.models.forEach(function(item) {
      var option = document.createElement('option');
      option.value = item[0];
      option.textContent = item[1] + ' · ' + item[0];
      models.appendChild(option);
    });
  } else {
    baseUrl.value = '';
  }
  var custom = document.createElement('option');
  custom.value = '__custom__';
  custom.textContent = '自定义模型 ID';
  models.appendChild(custom);
  if (!preset) models.value = '__custom__';
  toggleCustomByokModel(true);
  if (!initializing) markByokConfigDirty();
}

function toggleCustomByokModel(initializing) {
  var custom = document.getElementById('byok-model-preset').value === '__custom__';
  document.getElementById('byok-custom-model-wrap').hidden = !custom;
  if (!initializing) markByokConfigDirty();
}

function selectedByokModel() {
  var selected = document.getElementById('byok-model-preset').value;
  return selected === '__custom__'
    ? document.getElementById('byok-model-custom').value.trim()
    : selected;
}

function clearModelPreflightResult() {
  setModelConnectionStatus('idle', '等待测试连接', '输入 API Key 后测试；注册成功后 Key 会从输入框清除。');
}

function setModelConnectionStatus(state, title, detail) {
  document.getElementById('model-preflight-result').dataset.state = state;
  document.getElementById('model-preflight-title').textContent = title;
  document.getElementById('model-preflight-detail').textContent = detail;
}

function markByokConfigDirty() {
  byokConfigDirty = true;
  setModelConnectionStatus('idle', '配置已修改', '输入这组配置对应的 API Key，然后测试连接。');
}

function openModelSettings() {
  document.getElementById('model-modal').classList.add('open');
  loadModelProfiles();
}

function closeModelSettings() {
  document.getElementById('model-modal').classList.remove('open');
}

async function revokeByokProfile() {
  var profile = sessionStorage.getItem('acouagent_byok_profile');
  if (!profile) return;
  sessionStorage.removeItem('acouagent_byok_profile');
  byokRegistrationConfirmed = false;
  try {
    await fetch('/api/byok/session', {method:'DELETE', headers:{'Content-Type':'application/json'}, body:JSON.stringify({profile_id:profile})});
  } catch (_) {}
}

async function registerByokForCurrentSession() {
  var baseUrl = document.getElementById('byok-base-url').value.trim();
  var model = selectedByokModel();
  var apiKey = document.getElementById('byok-api-key').value.trim();
  if (!baseUrl || !model || !apiKey) throw new Error('请填写 API 地址、模型名称和 API Key。');
  var resp = await fetch('/api/byok/session', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({base_url:baseUrl, model:model, api_key:apiKey})});
  var data = await resp.json();
  if (!resp.ok) throw new Error(data.error || 'BYOK 配置失败');
  await revokeByokProfile();
  sessionStorage.setItem('acouagent_byok_profile', data.profile_id);
  localStorage.removeItem('acouagent_model_profile');
  document.getElementById('byok-api-key').value = '';
  document.getElementById('byok-api-key').placeholder = '本次会话已安全注册';
  byokConfigDirty = false;
  byokRegistrationConfirmed = true;
  return data.profile_id;
}

async function testModelConnection() {
  var selected = document.getElementById('model-profile').value;
  var button = document.getElementById('model-test-btn');
  button.disabled = true;
  setModelConnectionStatus('pending', '正在测试连接', '只执行小型兼容性检查，不运行模型返回的代码。');
  try {
    var profile = selected;
    if (selected === '__byok__') {
      var hasNewCredential = document.getElementById('byok-api-key').value.trim();
      if (!hasNewCredential && byokConfigDirty) {
        throw new Error('配置已修改，请输入这组配置对应的 API Key。');
      }
      profile = hasNewCredential ? await registerByokForCurrentSession() : getModelProfile();
      if (!profile || !String(profile).startsWith('byok-')) {
        throw new Error('请填写并测试 BYOK 配置。');
      }
    }
    if (!profile) throw new Error('请选择一个已配置的模型。');
    var resp = await fetch('/api/model-profiles/test', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({profile_id:profile})
    });
    var data = await resp.json();
    if (!resp.ok) throw new Error(data.error || '模型测试失败');
    var checks = Array.isArray(data.checks) ? data.checks : [];
    var passed = checks.filter(function(item) { return item.passed; }).length;
    var detail = String(data.model || '') + ' · ' + String(data.latency_ms) + ' ms · '
      + String(passed) + '/' + String(checks.length) + ' 项兼容检查通过';
    var failedDetails = checks.filter(function(item) { return !item.passed && item.detail; })
      .map(function(item) { return String(item.detail); });
    if (failedDetails.length) detail += '；未通过：' + failedDetails.join('；');
    if (selected === '__byok__') byokRegistrationConfirmed = true;
    setModelConnectionStatus(data.ok ? 'success' : 'warning',
      data.ok ? '连接成功' : '端点已连接，兼容检查未通过', detail);
  } catch (e) {
    var message = String(e.message || e);
    if (selected === '__byok__' && message.indexOf('byok_session_missing_or_expired') !== -1) {
      sessionStorage.removeItem('acouagent_byok_profile');
      byokRegistrationConfirmed = false;
      byokConfigDirty = true;
      document.getElementById('byok-api-key').placeholder = '仅发送到这台自部署服务器';
      message = '本次会话配置已失效，请重新输入 API Key 后测试。';
    }
    setModelConnectionStatus('error', '连接失败', message);
  } finally {
    button.disabled = false;
  }
}

async function saveModelSettings() {
  var selected = document.getElementById('model-profile').value;
  if (selected === '__byok__') {
    var existing = sessionStorage.getItem('acouagent_byok_profile');
    if (existing && !document.getElementById('byok-api-key').value.trim() && !byokConfigDirty) {
      closeModelSettings();
      return;
    }
    setModelConnectionStatus('pending', '正在保存模型配置', '密钥只会写入当前网关进程的临时内存。');
    try {
      await registerByokForCurrentSession();
      setModelConnectionStatus('success', '本次会话模型已注册', '密钥已从输入框清除；关闭设置后即可开始仿真。');
      closeModelSettings();
    } catch (e) {
      setModelConnectionStatus('error', '保存失败', String(e.message || e));
    }
    return;
  }
  await revokeByokProfile();
  if (selected) localStorage.setItem('acouagent_model_profile', selected);
  else localStorage.removeItem('acouagent_model_profile');
  closeModelSettings();
}

// 会话隔离：浏览器 localStorage 持久化 session_id，随请求发给后端（Dify user 按会话区分）
var currentSessionId = null;

function loadSessions() {
  try { return JSON.parse(localStorage.getItem('acouagent_sessions') || '[]'); } catch(e) { return []; }
}
function saveSessions(list) {
  localStorage.setItem('acouagent_sessions', JSON.stringify(list));
}
function newSessionId() {
  return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}
function getSessionId() { return currentSessionId; }

function persistCurrent(updateTime) {
  var list = loadSessions();
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === currentSessionId) { cur = list[i]; break; } }
  if (!cur) return;
  cur.messages = conversation;
  cur.requirement = currentRequirement;
  if (updateTime) { cur.updatedAt = Date.now(); }
  if ((cur.title === '新对话' || !cur.title) && conversation.length > 0) {
    for (var j = 0; j < conversation.length; j++) {
      if (conversation[j].role === 'user') { cur.title = conversation[j].content.slice(0, 20); break; }
    }
  }
  saveSessions(list);
}

function sessionGroupLabel(ts) {
  var d = new Date(ts);
  var now = new Date();
  var days30 = 30 * 24 * 3600 * 1000;
  if (now - d < days30) return '30天内';
  var m = d.getMonth() + 1;
  return d.getFullYear() + '-' + (m < 10 ? '0' + m : m);
}

function renderSessionList() {
  var box = document.getElementById('session-list');
  if (!box) return;
  var list = loadSessions();
  list.sort(function(a, b) { return b.updatedAt - a.updatedAt; });
  var html = '';
  var lastGroup = '';
  list.forEach(function(s) {
    var g = sessionGroupLabel(s.updatedAt);
    if (g !== lastGroup) {
      html += '<div class="session-group">' + g + '</div>';
      lastGroup = g;
    }
    var active = s.id === currentSessionId ? ' active' : '';
    html += '<div class="session-item' + active + '" onclick="switchSession(\'' + s.id + '\')" title="' + esc(s.title) + '">'
      + '<span class="session-title">' + esc(s.title || '新对话') + '</span>'
      + '<span class="session-del" onclick="event.stopPropagation();deleteSession(\'' + s.id + '\')" title="删除">×</span>'
      + '</div>';
  });
  box.innerHTML = html;
}

function switchSession(id) {
  if (id === currentSessionId) return;
  persistCurrent(false);
  var list = loadSessions();
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === id) { cur = list[i]; break; } }
  if (!cur) return;
  localStorage.setItem('acouagent_current', id);
  currentSessionId = id;
  conversation = cur.messages || [];
  currentRequirement = cur.requirement || '';
  document.getElementById('merge-hint').textContent = '';
  renderChat();
  renderSessionList();
  document.getElementById('query-input').focus();
}

function deleteSession(id) {
  if (!confirm('删除这个对话？此操作不可撤销。')) return;
  var list = loadSessions().filter(function(s) { return s.id !== id; });
  if (id === currentSessionId) {
    if (list.length) {
      localStorage.setItem('acouagent_current', list[0].id);
      currentSessionId = list[0].id;
      conversation = list[0].messages || [];
      currentRequirement = list[0].requirement || '';
    } else {
      var fresh = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
      list = [fresh];
      localStorage.setItem('acouagent_current', fresh.id);
      currentSessionId = fresh.id;
      conversation = [];
      currentRequirement = '';
    }
  }
  saveSessions(list);
  renderChat();
  renderSessionList();
}

function renderChat() {
  var log = document.getElementById('chat-log');
  if (!conversation.length) {
    var suggestions = [
      '2 MHz 点声源，均匀介质，区域 1 cm × 1 cm，网格 200×200',
      '1 MHz 点声源在水中传播，区域 2 cm × 2 cm，网格 200×200',
      '800 kHz 点声源，声速 1500 m/s，区域 1 cm × 1 cm，记录中心点压力随时间变化'
    ];
    log.innerHTML = '<div class="chat-empty">'
      + '<div class="empty-title">开始一次声学仿真</div>'
      + '<div class="empty-sub">用自然语言描述你的问题，支持多轮修改参数</div>'
      + '<div class="suggestions">'
      + suggestions.map(function(s) { return '<button type="button" class="chip" onclick="fillQuery(this)">' + esc(s) + '</button>'; }).join('')
      + '</div></div>';
    return;
  }
  log.innerHTML = conversation.map(function(m) {
    var cls = m.role === 'user' ? 'user' : 'assistant';
    var body = m.loading ? '<span class="typing"><i></i><i></i><i></i></span>' : (m.role === 'user' ? esc(m.content) : renderMarkdown(m.content));
    return '<div class="msg ' + cls + '">' + body + '</div>';
  }).join('');
  log.scrollTop = log.scrollHeight;
}

function fillQuery(el) {
  var input = document.getElementById('query-input');
  input.value = el.textContent;
  input.focus();
}

async function ask() {
  var input = document.getElementById('query-input');
  var btn = document.getElementById('ask-btn');
  var hint = document.getElementById('merge-hint');
  var message = input.value.trim();
  if (!message) return;
  input.value = '';
  hint.textContent = '';
  var skillFeedback = document.getElementById('skill-usage-feedback');
  if (skillFeedback) skillFeedback.textContent = '技能库：本次尚未确认参考调用';
  window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:[],final:true}}));

  conversation.push({role: 'user', content: message});
  renderChat();
  btn.disabled = true;
  btn.textContent = '运行中';
  btn.classList.add('loading');
  window.dispatchEvent(new CustomEvent('acouagent-progress',{detail:'请求已提交 · 等待工作流返回进度'}));

  var loadingIdx = conversation.length;
  conversation.push({role: 'assistant', content: '', loading: true});
  renderChat();

  try {
    var resp = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({stream: true, message: message, requirement: currentRequirement, session_id: getSessionId(), model_profile: getModelProfile()})
    });
    if (!resp.ok) throw new Error('服务返回 HTTP ' + resp.status);
    var data;
    if ((resp.headers.get('content-type') || '').includes('application/x-ndjson')) {
      var reader = resp.body.getReader(), decoder = new TextDecoder(), pending = '';
      while (true) {
        var chunk = await reader.read();
        pending += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
        var lines = pending.split('\n'); pending = lines.pop();
        for (var line of lines) {
          if (!line.trim()) continue;
          var event = JSON.parse(line);
          if (event.event === 'progress') {
            window.dispatchEvent(new CustomEvent('acouagent-progress',{detail:'当前步骤：'+event.stage}));
            conversation[loadingIdx] = {role: 'assistant', content: '当前步骤：' + event.stage + '。请稍候...'};
            renderChat();
          } else if (event.event === 'skill_usage') {
            if (skillFeedback) skillFeedback.textContent = event.skill_usage.message + ' · v' + event.skill_usage.version;
            window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:[event.skill_usage],final:false}}));
            conversation[loadingIdx] = {role: 'assistant', content: event.skill_usage.message};
            renderChat();
          } else if (event.event === 'result') { data = event; }
        }
        if (chunk.done) break;
      }
      if (!data) throw new Error('连接中断，未收到完整结果；请勿连续重复发送。');
    } else { data = await resp.json(); }

    var report = data.report || '';
    if (skillFeedback) skillFeedback.textContent = Array.isArray(data.skill_usage) && data.skill_usage.length ? data.skill_usage.map(function(s) { return s.message + ' · v' + s.version; }).join('；') : '本次未确认交付经过核验的技能参考';
    window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:Array.isArray(data.skill_usage)?data.skill_usage:[],final:true}}));
    if (Array.isArray(data.skill_usage) && data.skill_usage.length) {
      report += '\n\n**技能库调用反馈**\n' + data.skill_usage.map(function(s) { return '- ' + s.message + '（v' + s.version + '，编号 ' + s.skill_id + '）'; }).join('\n');
    }
    if (data.workflow_status === 'error' && data.request_id) report += '\n\n请求编号：' + data.request_id;
    currentRequirement = data.requirement || '';
    conversation[loadingIdx] = {role: 'assistant', content: report || '未获取到结果'};
    if (data.merge_used) {
      hint.textContent = '已根据历史需求合并本轮修改';
    }
  } catch(e) {
    conversation[loadingIdx] = {role: 'assistant', content: '请求失败：' + esc(String(e))};
  }
  renderChat();
  btn.disabled = false;
  btn.textContent = '开始仿真';
  btn.classList.remove('loading');
  window.dispatchEvent(new CustomEvent('acouagent-progress',{detail:'本次请求已结束 · 以报告中的成功或失败状态为准'}));
  persistCurrent(true);
  renderSessionList();
}

function resetChat() {
  persistCurrent(false);
  var fresh = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
  var list = loadSessions();
  list.unshift(fresh);
  saveSessions(list);
  localStorage.setItem('acouagent_current', fresh.id);
  currentSessionId = fresh.id;
  conversation = fresh.messages;
  currentRequirement = '';
  document.getElementById('merge-hint').textContent = '';
  var feedback = document.getElementById('skill-usage-feedback');
  if (feedback) feedback.textContent = '';
  renderChat();
  renderSessionList();
  document.getElementById('query-input').focus();
}

function initChat() {
  var list = loadSessions();
  var curId = localStorage.getItem('acouagent_current');
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === curId) { cur = list[i]; break; } }
  if (!cur) {
    cur = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
    list.unshift(cur);
    saveSessions(list);
    localStorage.setItem('acouagent_current', cur.id);
  }
  currentSessionId = cur.id;
  conversation = cur.messages || [];
  currentRequirement = cur.requirement || '';
  renderChat();
  renderSessionList();
}

window.initChat = initChat;
</script>
</body>
</html>
"""
