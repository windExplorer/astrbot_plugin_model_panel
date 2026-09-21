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

- detection_preferences：三通道检测范围（手动 / 定时 / 指令），NULL 表示跟随计费类型推默认
- model_profile：模型档案（计费类型、限时免费到期、单价、来源渠道、角色、流式能力、探测模式）
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import date, datetime, timedelta
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

-- 用户对每个 provider 是否勾选参与检测。三个通道各自独立，回答的是三个不同问题：
--   enabled          WebUI 一键/分组检测（我手点的）
--   allow_scheduled  定时巡检（系统自己花额度，NULL 表示跟随计费类型推默认）
--   allow_command    指令检测（谁能点、能点哪些）
-- NULL 是「未显式设置」，由 billing_type 推导；0/1 是用户显式覆盖。
-- 之所以不做成 NOT NULL DEFAULT，是因为用户改了计费类型后，
-- 固化下来的默认值不会跟着变，会出现「付费模型仍在被定时烧钱」。
CREATE TABLE IF NOT EXISTS detection_preferences (
    provider_id     TEXT PRIMARY KEY,
    enabled         INTEGER NOT NULL DEFAULT 1,
    allow_scheduled INTEGER,
    allow_command   INTEGER,
    updated_at      INTEGER NOT NULL
);

-- 模型档案：一行一个 provider。AstrBot 里 1 个 provider 实例恰好对应 1 个模型
-- （openai_source.py 在 init 时 set_model(config["model"])，model 是单数），
-- 所以「给同一供应商的不同模型分别定价」不存在——不同模型本来就是不同 provider。
-- 核心没有任何计费字段（models.dev 带 cost 但解析时被丢弃），只能自己存。
CREATE TABLE IF NOT EXISTS model_profile (
    provider_id        TEXT PRIMARY KEY,
    billing_type       TEXT    NOT NULL DEFAULT 'unknown',
    free_until         INTEGER,               -- 限时免费到期日，可空，仅展示
    currency           TEXT    NOT NULL DEFAULT '',
    price_input_per_m  REAL,                  -- 单价一律「每百万 token」
    price_output_per_m REAL,
    price_cached_per_m REAL,                  -- 缓存命中价，DeepSeek 类折扣很大
    channel_kind       TEXT    NOT NULL DEFAULT 'unknown',
    role               TEXT    NOT NULL DEFAULT 'unknown',
    supports_streaming TEXT    NOT NULL DEFAULT 'unknown',  -- true/false/unknown
    probe_mode         TEXT    NOT NULL DEFAULT 'non_stream',
    note               TEXT    NOT NULL DEFAULT '',
    model_name         TEXT    NOT NULL DEFAULT '',  -- 快照，provider 删除后仍可回显
    updated_at         INTEGER NOT NULL DEFAULT 0
);

-- LLM 调用用量统计：由 on_llm_response 钩子写入，每行一次调用。
-- day 为本地日期（YYYY-MM-DD），便于按天聚合。
CREATE TABLE IF NOT EXISTS llm_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    day            TEXT    NOT NULL,
    provider_id    TEXT    NOT NULL DEFAULT '',
    model          TEXT    NOT NULL DEFAULT '',
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    cached_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_usage_day   ON llm_usage(day);
CREATE INDEX IF NOT EXISTS idx_usage_model ON llm_usage(model);
"""


# ---------------- 档案枚举与取值约束 ----------------
# paid_overage 单独一类：它平时表现为「免费」，额度用完当天才变红，是最容易漏的预算炸弹。
BILLING_TYPES = ("unknown", "free", "temp_free", "trial", "paid_overage", "paid", "subscription")
# 来源渠道决定告警口径（中转站 5xx 是常态，阈值应比官方宽松）与成本语义。
CHANNEL_KINDS = ("unknown", "official", "aggregator", "reseller", "self_hosted")
# primary 有特权：它是正在服务用户的那个，标红时和观察模型标红完全不是一回事。
ROLES = ("unknown", "primary", "backup", "fallback", "dedicated", "watch", "retired")
STREAM_FLAGS = ("unknown", "true", "false")
PROBE_MODES = ("non_stream", "stream", "both")

# 定时巡检会持续产生真实调用，默认只对「单次不产生费用」的计费类型开放。
SCHEDULED_DEFAULT_ON = ("free", "temp_free", "subscription")

# 三通道 → 列名。manual 沿用既有的 enabled，保持老前端语义不变。
DETECT_CHANNELS = ("manual", "scheduled", "command")
_CHANNEL_COLUMN = {"manual": "enabled", "scheduled": "allow_scheduled", "command": "allow_command"}

_ENUM_FIELDS = {
    "billing_type": BILLING_TYPES,
    "channel_kind": CHANNEL_KINDS,
    "role": ROLES,
    "supports_streaming": STREAM_FLAGS,
    "probe_mode": PROBE_MODES,
}
_INT_FIELDS = ("free_until",)
_FLOAT_FIELDS = ("price_input_per_m", "price_output_per_m", "price_cached_per_m")
_TEXT_FIELDS = ("currency", "note", "model_name")
PROFILE_FIELDS = tuple(_ENUM_FIELDS) + _INT_FIELDS + _FLOAT_FIELDS + _TEXT_FIELDS


def scheduled_default_for(billing_type: str) -> bool:
    """按计费类型推导「是否参与定时巡检」的默认值。"""
    return billing_type in SCHEDULED_DEFAULT_ON


def _coerce_profile(field: str, value: Any) -> Any:
    """把前端传来的档案值收敛成合法类型。非法枚举值退回 unknown，不抛异常。"""
    if field in _ENUM_FIELDS:
        raw = str(value or "").strip()
        return raw if raw in _ENUM_FIELDS[field] else "unknown"
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if field in _INT_FIELDS:
            # 前端日期选择器给的是 "YYYY-MM-DD"，按本地零点存成 epoch；直接给数字则当 epoch
            if isinstance(value, str) and "-" in value:
                day = date.fromisoformat(value.strip()[:10])
                return int(datetime(day.year, day.month, day.day).timestamp())
            return int(float(value))
        if field in _FLOAT_FIELDS:
            price = float(value)
            # 负单价没有意义；0 是合法值（免费额度内、订阅内含）
            return price if price >= 0 else 0.0
        if field == "currency":
            return str(value).strip().upper()[:8]
        return str(value).strip()[:200]
    except (TypeError, ValueError):
        return None



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
                await self._migrate(db)
                await db.commit()
            self._initialized = True

    @staticmethod
    async def _migrate(db: "aiosqlite.Connection") -> None:
        """老库补列。CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，
        漏了这一步的话老用户一进档案页就 "no such column" 白屏。"""
        cur = await db.execute("PRAGMA table_info(detection_preferences)")
        existing = {row[1] for row in await cur.fetchall()}
        if not existing:
            return
        for col in ("allow_scheduled", "allow_command"):
            if col not in existing:
                # 列名来自上面的固定字面量，不是用户输入
                await db.execute(f"ALTER TABLE detection_preferences ADD COLUMN {col} INTEGER")

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

    # ---------- LLM 用量统计 ----------
    async def record_usage(
        self,
        model: str,
        provider_id: str = "",
        input_tokens: int = 0,
        cached_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """记录一次 LLM 调用的 token 用量。"""
        await self.init()
        now = int(time.time())
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        total = (
            int(input_tokens or 0) + int(cached_tokens or 0) + int(output_tokens or 0)
        )
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT INTO llm_usage
                       (ts, day, provider_id, model,
                        input_tokens, cached_tokens, output_tokens, total_tokens)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        now,
                        day,
                        str(provider_id or ""),
                        str(model or ""),
                        int(input_tokens or 0),
                        int(cached_tokens or 0),
                        int(output_tokens or 0),
                        total,
                    ),
                )
                await db.commit()

    async def usage_stats(self, days: int = 7, top_models: int = 5) -> dict:
        """用量统计：今日 / 累计 / 近 N 天趋势 / 今日模型 TopN。"""
        await self.init()
        days = max(1, int(days or 7))
        today = time.strftime("%Y-%m-%d", time.localtime())
        since = (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT COALESCE(SUM(total_tokens), 0) AS total,
                          COALESCE(SUM(input_tokens), 0) AS inp,
                          COALESCE(SUM(cached_tokens), 0) AS cached,
                          COALESCE(SUM(output_tokens), 0) AS outp,
                          COUNT(*) AS reqs
                   FROM llm_usage WHERE day = ?""",
                (today,),
            )
            t = await cur.fetchone()
            cur = await db.execute(
                """SELECT COALESCE(SUM(total_tokens), 0) AS total, COUNT(*) AS reqs
                   FROM llm_usage"""
            )
            a = await cur.fetchone()
            cur = await db.execute(
                """SELECT day,
                          COALESCE(SUM(total_tokens), 0) AS total,
                          COUNT(*) AS reqs
                   FROM llm_usage
                   WHERE day >= ?
                   GROUP BY day ORDER BY day ASC""",
                (since,),
            )
            day_rows = await cur.fetchall()
            cur = await db.execute(
                """SELECT model, provider_id,
                          COALESCE(SUM(total_tokens), 0) AS total,
                          COUNT(*) AS reqs
                   FROM llm_usage WHERE day = ?
                   GROUP BY model, provider_id
                   ORDER BY total DESC LIMIT ?""",
                (today, int(top_models or 5)),
            )
            model_rows = await cur.fetchall()
        by_day = {r["day"]: r for r in day_rows}
        series: list[dict] = []
        for i in range(days - 1, -1, -1):
            d = (date.fromisoformat(today) - timedelta(days=i)).isoformat()
            r = by_day.get(d)
            series.append(
                {
                    "day": d,
                    "total": int(r["total"] or 0) if r else 0,
                    "requests": int(r["reqs"] or 0) if r else 0,
                }
            )
        return {
            "today": {
                "total": int(t["total"] or 0),
                "input": int(t["inp"] or 0),
                "cached": int(t["cached"] or 0),
                "output": int(t["outp"] or 0),
                "requests": int(t["reqs"] or 0),
            },
            "total": {
                "total": int(a["total"] or 0),
                "requests": int(a["reqs"] or 0),
            },
            "by_day": series,
            "by_model": [
                {
                    "model": r["model"] or "",
                    "provider_id": r["provider_id"] or "",
                    "total": int(r["total"] or 0),
                    "requests": int(r["reqs"] or 0),
                }
                for r in model_rows
            ],
        }

    async def cleanup_usage_older_than(self, days: int) -> int:
        """清理 days 天前的用量记录。返回受影响行数。"""
        if days <= 0:
            return 0
        await self.init()
        cutoff = (date.today() - timedelta(days=int(days))).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    "DELETE FROM llm_usage WHERE day < ?", (cutoff,)
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
        """批量写入手动通道的勾选。

        改成逐条 upsert，不再「先 DELETE 再插入」：原来的清空会把同一张表里
        ``allow_scheduled`` / ``allow_command`` 两个新通道一起抹掉，
        用户在档案页配好的定时名单会被前端一次普通的勾选保存悄悄清空。
        """
        await self.init()
        now = int(time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executemany(
                    """INSERT INTO detection_preferences
                       (provider_id, enabled, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(provider_id)
                       DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
                    [(pid, 1 if enabled else 0, now) for pid, enabled in prefs.items()],
                )
                await db.commit()

    # ---------- 三通道检测范围 ----------
    async def get_detect_scopes(self) -> dict[str, dict[str, Any]]:
        """返回 {provider_id: {manual, scheduled, command, billing_type, explicit}}。

        库里存 NULL 表示「未显式设置」，由 billing_type 推导：
        manual / command 默认开放，scheduled 只对不产生单次费用的计费类型开放。
        所以新装用户什么都没标时，**定时名单是空的** —— 这是刻意的省钱默认，
        而不是漏配；档案页可以按供应商批量勾选。
        """
        await self.init()
        sql = """SELECT d.provider_id AS pid, d.enabled AS manual,
                        d.allow_scheduled AS scheduled, d.allow_command AS command,
                        m.billing_type AS billing
                 FROM detection_preferences d
                 LEFT JOIN model_profile m ON m.provider_id = d.provider_id"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql)
            rows = await cur.fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            billing = str(row["billing"] or "unknown")
            explicit = {
                "manual": row["manual"] is not None,
                "scheduled": row["scheduled"] is not None,
                "command": row["command"] is not None,
            }
            out[row["pid"]] = {
                "billing_type": billing,
                "explicit": explicit,
                "manual": bool(row["manual"]) if explicit["manual"] else True,
                "scheduled": bool(row["scheduled"]) if explicit["scheduled"] else scheduled_default_for(billing),
                "command": bool(row["command"]) if explicit["command"] else True,
            }
        return out

    async def set_detect_scope(
        self, provider_id: str, channel: str, enabled: Optional[bool]
    ) -> bool:
        """设置某个 provider 在某通道的参与情况。``enabled=None`` 表示恢复跟随默认。"""
        if channel not in _CHANNEL_COLUMN:
            return False
        await self.init()
        col = _CHANNEL_COLUMN[channel]
        value = None if enabled is None else (1 if enabled else 0)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                # 先保证行存在（新 provider 的 manual 默认勾选），再定点改那一列
                await db.execute(
                    """INSERT INTO detection_preferences (provider_id, enabled, updated_at)
                       VALUES (?, 1, ?)
                       ON CONFLICT(provider_id) DO UPDATE SET updated_at=excluded.updated_at""",
                    (provider_id, int(time.time())),
                )
                await db.execute(
                    f"UPDATE detection_preferences SET {col} = ? WHERE provider_id = ?",
                    (value, provider_id),
                )
                await db.commit()
        return True

    # ---------- 模型档案 ----------
    async def get_all_profiles(self) -> dict[str, dict[str, Any]]:
        await self.init()
        cols = ", ".join(PROFILE_FIELDS)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(f"SELECT provider_id, {cols}, updated_at FROM model_profile")
            rows = await cur.fetchall()
        return {
            r["provider_id"]: {
                "provider_id": r["provider_id"],
                **{f: r[f] for f in PROFILE_FIELDS},
                "updated_at": int(r["updated_at"] or 0),
            }
            for r in rows
        }

    async def upsert_profile(self, provider_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """写入/更新档案，返回落库后的完整档案。非法枚举退回 unknown，不抛异常。"""
        await self.init()
        clean = {f: _coerce_profile(f, patch.get(f)) for f in PROFILE_FIELDS if f in patch}
        now = int(time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT * FROM model_profile WHERE provider_id = ?", (provider_id,)
                )
                row = await cur.fetchone()
                merged = {f: row[f] for f in PROFILE_FIELDS} if row else {}
                merged.update(clean)
                # 枚举列 NOT NULL，缺值时补 unknown；数值/文本列允许 NULL
                for f in _ENUM_FIELDS:
                    if merged.get(f) is None:
                        merged[f] = "unknown"
                cols = list(merged)
                placeholders = ", ".join("?" for _ in cols)
                updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
                await db.execute(
                    f"""INSERT INTO model_profile (provider_id, {", ".join(cols)}, updated_at)
                        VALUES (?, {placeholders}, ?)
                        ON CONFLICT(provider_id) DO UPDATE SET {updates}, updated_at=excluded.updated_at""",
                    [provider_id] + [merged[c] for c in cols] + [now],
                )
                await db.commit()
        cur_row = await self.get_all_profiles()
        return cur_row.get(provider_id, {"provider_id": provider_id, **merged})

    async def reap_orphans(self, live_ids: set[str]) -> dict[str, int]:
        """清掉 provider 已不存在的档案/范围行。

        AstrBot 的 delete_provider 不做任何级联清理，用户改名/删除后这边会留孤儿；
        改名更狠——旧 id 的行会永远显示一个不存在的模型。
        """
        await self.init()
        removed = {"model_profile": 0, "detection_preferences": 0}
        if not live_ids:
            # 一个 provider 都没有时不做全表删，交给上层确认，避免误清空
            return removed
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                marks = ", ".join("?" for _ in live_ids)
                params = tuple(live_ids)
                for table in removed:
                    cur = await db.execute(
                        f"DELETE FROM {table} WHERE provider_id NOT IN ({marks})", params
                    )
                    removed[table] = cur.rowcount or 0
                await db.commit()
        return removed



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
