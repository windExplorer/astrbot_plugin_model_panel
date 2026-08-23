"""SQLite 存储层：保存模型检测历史与统计，使用 WAL 模式以支持并发读写。

依赖：aiosqlite。表结构：

- test_sessions：每次"一键检测" / "单独检测" 一次会话的元数据
    id              INTEGER PRIMARY KEY
    session_uuid    TEXT    会话 UUID（前端可用做关联）
    trigger         TEXT    'all' / 'single' / 'sse'
    started_at      INTEGER 起始时间戳（秒）
    finished_at     INTEGER 结束时间戳（秒），未完成为 NULL
    total           INTEGER 模型总数
    ok_count        INTEGER 成功数
    fail_count      INTEGER 失败数
    skip_count      INTEGER 跳过数

- model_test_results：单次检测条目
    id              INTEGER PRIMARY KEY
    session_id      INTEGER 外键 → test_sessions.id
    provider_id     TEXT
    provider_name   TEXT
    provider_model  TEXT
    ok              INTEGER 0/1
    latency_ms      REAL    延迟毫秒；失败时为 NULL
    error_code      TEXT    归一化错误码（timeout/connect/refused/auth/unknown/...）
    error_message   TEXT    原始错误短文本（≤200 字符）
    retry_count     INTEGER 实际重试次数（0 表示一次就过）
    checked_at      INTEGER 时间戳（秒）

索引：
- idx_results_checked_at：按时间倒序查询历史
- idx_results_provider：按 provider_id 过滤
- idx_results_session：按 session_id 过滤
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Optional

try:
    import aiosqlite  # 第三方依赖
except ImportError as e:  # pragma: no cover - 由 AstrBot 启动时保证已安装
    raise ImportError(
        "astrbot_plugin_model_panel 需要 aiosqlite，请先在 AstrBot 环境中安装：pip install aiosqlite"
    ) from e


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS test_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uuid    TEXT    NOT NULL UNIQUE,
    trigger         TEXT    NOT NULL,
    started_at      INTEGER NOT NULL,
    finished_at     INTEGER,
    total           INTEGER NOT NULL DEFAULT 0,
    ok_count        INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    skip_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS model_test_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES test_sessions(id) ON DELETE CASCADE,
    provider_id     TEXT    NOT NULL,
    provider_name   TEXT    NOT NULL DEFAULT '',
    provider_model  TEXT    NOT NULL DEFAULT '',
    ok              INTEGER NOT NULL,
    latency_ms      REAL,
    error_code      TEXT    NOT NULL DEFAULT '',
    error_message   TEXT    NOT NULL DEFAULT '',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    checked_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_results_checked_at ON model_test_results(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_results_provider   ON model_test_results(provider_id);
CREATE INDEX IF NOT EXISTS idx_results_session    ON model_test_results(session_id);

-- 用户对每个 provider 是否勾选参与一键检测。
-- 默认全部勾选（enabled 默认 1）；用户取消勾选后写入 0；
-- 不存在的行视为 enabled=1。
CREATE TABLE IF NOT EXISTS detection_preferences (
    provider_id     TEXT PRIMARY KEY,
    enabled         INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
"""


class Storage:
    """异步 SQLite 存储（WAL 模式）。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._initialized = False

    # ---------- 生命周期 ----------
    async def init(self) -> None:
        if self._initialized:
            return
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        async with self._lock:
            if self._initialized:
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode = WAL")
                await db.execute("PRAGMA synchronous = NORMAL")
                await db.execute("PRAGMA foreign_keys = ON")
                await db.executescript(SCHEMA_SQL)
                await db.commit()
            self._initialized = True

    async def close(self) -> None:
        # aiosqlite 没有常驻连接，无需显式 close。
        self._initialized = False

    # ---------- 会话 ----------
    async def create_session(self, trigger: str, total: int) -> dict:
        await self.init()
        session_uuid = uuid.uuid4().hex
        started_at = int(time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    "INSERT INTO test_sessions (session_uuid, trigger, started_at, total) VALUES (?, ?, ?, ?)",
                    (session_uuid, trigger, started_at, total),
                )
                await db.commit()
                session_id = cur.lastrowid
        return {
            "id": session_id,
            "session_uuid": session_uuid,
            "trigger": trigger,
            "started_at": started_at,
            "total": total,
            "ok_count": 0,
            "fail_count": 0,
            "skip_count": 0,
        }

    async def finish_session(self, session_id: int, ok_count: int, fail_count: int, skip_count: int) -> None:
        await self.init()
        finished_at = int(time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE test_sessions SET finished_at=?, ok_count=?, fail_count=?, skip_count=? WHERE id=?",
                    (finished_at, ok_count, fail_count, skip_count, session_id),
                )
                await db.commit()

    # ---------- 结果 ----------
    async def insert_result(self, session_id: int, item: dict) -> int:
        await self.init()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    """INSERT INTO model_test_results
                       (session_id, provider_id, provider_name, provider_model,
                        ok, latency_ms, error_code, error_message, retry_count, checked_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        str(item.get("id") or ""),
                        str(item.get("name") or ""),
                        str(item.get("model") or ""),
                        1 if item.get("ok") else 0,
                        item.get("latency_ms"),
                        str(item.get("error_code") or ""),
                        str(item.get("error") or ""),
                        int(item.get("retry_count") or 0),
                        int(item.get("checked_at") or time.time()),
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0

    # ---------- 查询 ----------
    async def latest_per_provider(self, keep_top: int = 1) -> dict[str, dict]:
        """返回每个 provider_id 的最新一条结果，键为 provider_id。"""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM (
                      SELECT r.*,
                             ROW_NUMBER() OVER (PARTITION BY provider_id ORDER BY checked_at DESC, id DESC) AS rn
                      FROM model_test_results r
                   ) WHERE rn <= ?
                   ORDER BY provider_id""",
                (keep_top,),
            )
            rows = await cur.fetchall()
        out: dict[str, dict] = {}
        for row in rows:
            out[row["provider_id"]] = _row_to_result_dict(row)
        return out

    async def latest_session(self) -> Optional[dict]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM test_sessions ORDER BY started_at DESC, id DESC LIMIT 1"""
            )
            row = await cur.fetchone()
        return _row_to_session_dict(row) if row else None

    async def session_stats(self) -> dict:
        """整体统计：累计会话数 / 最近一次会话的存活率 / 累计失败项。"""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM test_sessions WHERE finished_at IS NOT NULL"
            )
            row = await cur.fetchone()
            sessions_total = int(row["c"] or 0)

            cur = await db.execute(
                """SELECT ok_count, fail_count, skip_count, total
                   FROM test_sessions WHERE finished_at IS NOT NULL
                   ORDER BY finished_at DESC, id DESC LIMIT 1"""
            )
            last = await cur.fetchone()
            cur = await db.execute(
                """SELECT
                       SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS fail,
                       COUNT(*) AS total
                   FROM model_test_results"""
            )
            all_row = await cur.fetchone()
        latest = None
        if last:
            total = max(int(last["total"] or 0), 1)
            ok = int(last["ok_count"] or 0)
            latest = {
                "ok_count": ok,
                "fail_count": int(last["fail_count"] or 0),
                "skip_count": int(last["skip_count"] or 0),
                "total": int(last["total"] or 0),
                "alive_rate": round(ok * 100.0 / total, 1),
            }
        return {
            "sessions_total": sessions_total,
            "results_total": int(all_row["total"] or 0),
            "results_ok": int(all_row["ok"] or 0),
            "results_fail": int(all_row["fail"] or 0),
            "latest_session": latest,
        }

    async def cleanup_older_than(self, days: int) -> int:
        """清理 days 天前的已完成会话。返回受影响行数。"""
        if days <= 0:
            return 0
        await self.init()
        cutoff = int(time.time()) - days * 86400
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    """DELETE FROM test_sessions WHERE finished_at IS NOT NULL AND finished_at < ?""",
                    (cutoff,),
                )
                await db.commit()
                return cur.rowcount or 0

    # ---------- 检测勾选偏好 ----------
    async def get_detection_preferences(self) -> dict[str, bool]:
        """返回 {provider_id: enabled}。缺失的 provider 视为 True（默认勾选）。"""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT provider_id, enabled FROM detection_preferences")
            rows = await cur.fetchall()
        return {row["provider_id"]: bool(row["enabled"]) for row in rows}

    async def set_detection_preference(self, provider_id: str, enabled: bool) -> None:
        await self.init()
        now = int(time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT INTO detection_preferences (provider_id, enabled, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(provider_id) DO UPDATE SET enabled=?, updated_at=?""",
                    (provider_id, 1 if enabled else 0, now, 1 if enabled else 0, now),
                )
                await db.commit()

    async def set_detection_preferences(self, prefs: dict[str, bool]) -> None:
        """批量写入；先清空再插入，确保与前端传来的集合一致（缺失项回到默认 True）。"""
        await self.init()
        now = int(time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                # 删除所有 preference，由调用方传入完整集合（缺失即默认勾选）
                await db.execute("DELETE FROM detection_preferences")
                rows = [
                    (pid, 1 if enabled else 0, now)
                    for pid, enabled in prefs.items()
                ]
                if rows:
                    await db.executemany(
                        "INSERT INTO detection_preferences (provider_id, enabled, updated_at) VALUES (?, ?, ?)",
                        rows,
                    )
                await db.commit()


# ---------- helpers ----------
def _row_to_result_dict(row: Any) -> dict:
    return {
        "id": row["provider_id"],
        "name": row["provider_name"] or "",
        "model": row["provider_model"] or "",
        "ok": bool(row["ok"]),
        "latency_ms": row["latency_ms"],
        "error_code": row["error_code"] or "",
        "error": row["error_message"] or "",
        "retry_count": int(row["retry_count"] or 0),
        "checked_at": int(row["checked_at"] or 0),
        "session_id": int(row["session_id"] or 0),
    }


def _row_to_session_dict(row: Any) -> dict:
    total = max(int(row["total"] or 0), 1)
    return {
        "id": int(row["id"]),
        "session_uuid": row["session_uuid"],
        "trigger": row["trigger"],
        "started_at": int(row["started_at"] or 0),
        "finished_at": int(row["finished_at"] or 0) if row["finished_at"] else None,
        "total": int(row["total"] or 0),
        "ok_count": int(row["ok_count"] or 0),
        "fail_count": int(row["fail_count"] or 0),
        "skip_count": int(row["skip_count"] or 0),
        "alive_rate": round(int(row["ok_count"] or 0) * 100.0 / total, 1),
    }
