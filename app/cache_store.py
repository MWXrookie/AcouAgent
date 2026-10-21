"""纠错经验缓存（SQLite）：错误签名提取、命中查询、置信度更新与统计。

数据文件：/app/data/error_cache.db（与执行历史同卷持久化）。
"""

import re
import sqlite3 as _sql
from datetime import datetime, timezone
from pathlib import Path as _Path

from app.dashboard import DB_PATH

_CACHE_DB = _Path(DB_PATH).parent / "error_cache.db"


def _cache_db() -> _sql.Connection:
    """Get or create the error cache database."""
    db = _sql.connect(str(_CACHE_DB))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS error_fix_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_signature TEXT NOT NULL UNIQUE,
            fix_hint TEXT NOT NULL,
            hit_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            consecutive_fails INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.0,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    db.commit()
    return db


def _extract_error_signature(stderr: str, exit_code: int | None, stdout: str) -> str | None:
    """解析 stderr 提取错误签名： (错误类型, 对象名, 属性/问题)"""
    if not stderr and exit_code == 0 and stdout:
        # 检测全零输出
        if re.search(r'(?:最大压力|max.pressure|Max pressure).*[:=]\s*0[\.\s]', stdout):
            return "ZeroOutput|max_pressure|all_zeros"
        return None
    if not stderr:
        return None

    # 提取错误类型
    err_type = "UnknownError"
    m = re.search(r'(\w+Error|\w+Exception|\w+Warning)', stderr)
    if m:
        err_type = m.group(1)

    # 提取对象名
    obj_name = "unknown"
    m = re.search(r"'([^']+)' object has no attribute '(\w+)'", stderr)
    if m:
        obj_name = m.group(1).split(".")[-1]
        attr = m.group(2)
        return f"{err_type}|{obj_name}|{attr}"

    # has no attribute
    m = re.search(r"has no attribute '(\w+)'", stderr)
    if m:
        # Extract class name from "X object has no attribute"
        m2 = re.search(r"'([^']+)' object", stderr)
        obj = m2.group(1).split(".")[-1] if m2 else "object"
        return f"{err_type}|{obj}|{m.group(1)}"

    # broadcast_shapes
    if "broadcast_shapes" in stderr:
        return f"BroadcastError|broadcast_shapes|shape_mismatch"

    # positional/keyword argument errors
    m = re.search(r'(__init__\(\)|__call__\(\))\s+takes\s+(\d+)', stderr)
    if m:
        return f"TypeError|{m.group(1)}|arg_count"

    # signals must be / positions must be
    m = re.search(r'(signals must be|positions must be)', stderr)
    if m:
        return f"TypeError|Sources|{m.group(1)}"

    # np.save error (disk full)
    if "OSError" in stderr and "written" in stderr:
        return "OSError|np.save|disk_full"

    # Generic fallback
    key = stderr.split("\n")[-3] if len(stderr.split("\n")) > 2 else stderr[-100:]
    key = re.sub(r'File ".*?"', '', key).strip()[:80]
    return f"{err_type}|general|{key}" if key else None


def _query_cache(error_signature: str) -> dict | None:
    """查询缓存，返回置信度 >= 0.7 的条目."""
    if not error_signature:
        return None
    db = _cache_db()
    row = db.execute(
        "SELECT * FROM error_fix_cache WHERE error_signature = ? AND confidence >= 0.7",
        (error_signature,),
    ).fetchone()
    db.close()
    if row:
        return {"signature": row[1], "fix_hint": row[2], "confidence": row[6]}
    return None


def _update_cache(error_signature: str, fix_hint: str, was_successful: bool) -> None:
    """更新缓存：记录命中、成功/失败，管理质疑和退役."""
    if not error_signature or not fix_hint:
        return
    db = _cache_db()
    now = datetime.now(timezone.utc).isoformat()
    existing = db.execute(
        "SELECT * FROM error_fix_cache WHERE error_signature = ?",
        (error_signature,),
    ).fetchone()

    if existing:
        hit_count = existing[3] + 1
        if was_successful:
            success_count = existing[4] + 1
            consecutive_fails = 0
        else:
            success_count = existing[4]
            consecutive_fails = existing[5] + 1

        confidence = success_count / hit_count if hit_count > 0 else 0.0

        # 连续失败 >= 3: 标记质疑 (confidence 减半)
        if consecutive_fails >= 3:
            confidence = confidence * 0.5

        # 连续失败 >= 5: 删除
        if consecutive_fails >= 5:
            db.execute("DELETE FROM error_fix_cache WHERE error_signature = ?", (error_signature,))
            db.commit()
            db.close()
            return

        db.execute(
            """UPDATE error_fix_cache
               SET fix_hint = ?, hit_count = ?, success_count = ?,
                   consecutive_fails = ?, confidence = ?, updated_at = ?
               WHERE error_signature = ?""",
            (fix_hint, hit_count, success_count, consecutive_fails, confidence, now, error_signature),
        )
    else:
        confidence = 1.0 if was_successful else 0.0
        db.execute(
            """INSERT INTO error_fix_cache (error_signature, fix_hint, hit_count, success_count, consecutive_fails, confidence, created_at, updated_at)
               VALUES (?, ?, 1, ?, ?, ?, ?, ?)""",
            (error_signature, fix_hint, 1 if was_successful else 0, 0 if was_successful else 1, confidence, now, now),
        )
    db.commit()
    db.close()


def get_cache_stats() -> dict:
    """返回缓存统计数据."""
    db = _cache_db()
    rows = db.execute("SELECT * FROM error_fix_cache").fetchall()
    db.close()

    total = len(rows)
    total_hits = sum((row[3] or 0) for row in rows)
    total_success = sum((row[4] or 0) for row in rows)
    hit_rate = round(total_success / total_hits * 100, 1) if total_hits > 0 else 0.0
    confidences = [float(row[6] or 0.0) for row in rows if row[6] is not None]
    hits = [int(row[3] or 0) for row in rows]

    def _row_dict(row) -> dict:
        return {
            "signature": row[1],
            "hint": row[2],
            "hint_excerpt": (row[2] or "")[:160],
            "hits": row[3],
            "success_count": row[4],
            "consecutive_fails": row[5],
            "confidence": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }

    sorted_rows = sorted(rows, key=lambda row: ((row[3] or 0), (row[6] or 0.0)), reverse=True)
    recent_rows = sorted(rows, key=lambda row: row[8] or "", reverse=True)

    confidence_bands = {"0-50": 0, "50-70": 0, "70-90": 0, "90-100": 0}
    failure_streak_buckets = {"0": 0, "1-2": 0, "3-4": 0, "5+": 0}
    error_families: dict[str, int] = {}
    healthy_count = 0
    questioned_count = 0
    stale_count = 0

    for row in rows:
        confidence = float(row[6] or 0.0)
        streak = int(row[5] or 0)
        prefix = (row[1] or "general").split("|")[0]
        error_families[prefix] = error_families.get(prefix, 0) + 1

        if confidence < 0.5:
            confidence_bands["0-50"] += 1
        elif confidence < 0.7:
            confidence_bands["50-70"] += 1
        elif confidence < 0.9:
            confidence_bands["70-90"] += 1
        else:
            confidence_bands["90-100"] += 1

        if streak == 0:
            failure_streak_buckets["0"] += 1
        elif streak < 3:
            failure_streak_buckets["1-2"] += 1
        elif streak < 5:
            failure_streak_buckets["3-4"] += 1
        else:
            failure_streak_buckets["5+"] += 1

        if confidence >= 0.7 and streak < 3:
            healthy_count += 1
        elif streak >= 5:
            stale_count += 1
        elif streak >= 3:
            questioned_count += 1

    avg_hits_per_entry = round(total_hits / total, 2) if total > 0 else 0
    avg_confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    max_hits = max(hits) if hits else 0

    return {
        "total_entries": total,
        "total_hits": total_hits,
        "total_successes": total_success,
        "overall_hit_rate": hit_rate,
        "avg_hits_per_entry": avg_hits_per_entry,
        "avg_confidence": avg_confidence,
        "max_hits": max_hits,
        "healthy_count": healthy_count,
        "questioned_count": questioned_count,
        "stale_count": stale_count,
        "confidence_bands": confidence_bands,
        "failure_streak_buckets": failure_streak_buckets,
        "error_families": error_families,
        "entries": [_row_dict(row) for row in sorted_rows[:500]],
        "entries_truncated": total > 500,
        "top_entries": [_row_dict(row) for row in sorted_rows[:5]],
        "recent_entries": [_row_dict(row) for row in recent_rows[:5]],
    }
