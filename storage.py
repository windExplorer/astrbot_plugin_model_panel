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
    ttft_ms         REAL    流式探测的首字毫秒；非流式或失败时为 NULL
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


# model_state 里哪几列是**文本**（其余都是整数）。见 get_model_states 的注释。
_STATE_TEXT_COLS = ("state", "last_error_code")


def _as_int(value: Any, default: int = 0) -> int:
    """尽量转 int；转不了就给默认值。

    只用在「读自己写的库」这种地方（面板是观测工具，一列坏值不该让它整个打不开）。
    注意别拿它做语义纠偏：``''`` 与 ``None`` 都算「没有值」，一律落到 ``default``。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    ttft_ms         REAL,
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
    currency           TEXT    NOT NULL DEFAULT '',  -- 留空 = 跟随该供应商的分组币种
    price_input_per_m  REAL,                  -- 单价一律「每百万 token」
    price_output_per_m REAL,
    price_cached_per_m REAL,                  -- 缓存命中价，DeepSeek 类折扣很大
    price_per_call     REAL,                  -- 按次计费的单次单价
    rate_multiplier    REAL,                  -- 倍率；NULL = 跟随分组倍率
    channel_kind       TEXT    NOT NULL DEFAULT 'unknown',
    role               TEXT    NOT NULL DEFAULT 'unknown',
    supports_streaming TEXT    NOT NULL DEFAULT 'unknown',  -- true/false/unknown
    probe_mode         TEXT    NOT NULL DEFAULT 'non_stream',
    note               TEXT    NOT NULL DEFAULT '',
    model_name         TEXT    NOT NULL DEFAULT '',  -- 快照，provider 删除后仍可回显
    updated_at         INTEGER NOT NULL DEFAULT 0
);

-- 供应商（分组）级档案。倍率与每日次数限制天然是「一家一个值」，
-- 逐个模型填十遍既累又容易填得不一致，所以放在这一层；模型侧只留可选覆盖。
-- 键是供应商名（_provider_display 的 name，即 provider_source_id），不是 provider_id。
CREATE TABLE IF NOT EXISTS vendor_profile (
    name              TEXT PRIMARY KEY,
    currency          TEXT    NOT NULL DEFAULT '',
    rate_multiplier   REAL,                   -- 分组倍率；NULL = 未设置（按 1.0 计）
    daily_call_limit  INTEGER,                -- 每日次数限制；NULL = 不限
    note              TEXT    NOT NULL DEFAULT '',
    updated_at        INTEGER NOT NULL DEFAULT 0
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

-- 逐次 LLM 调用记录，由本插件自己包装 provider.text_chat / text_chat_stream 写入。
-- 为什么不能只用核心的 provider_stats：那是一张「每轮一条」的表，
-- 主模型失败被备用模型救回来时，行的 provider_id 归属于**最后跑成功的那个**，
-- 失败的那次尝试在核心里根本没有记录 —— 于是日志里失败一堆、面板却显示 100% 成功。
-- 这张表按「每次尝试」记，失败的、被换掉的都在这里，才是模型真实的可靠性。
CREATE TABLE IF NOT EXISTS llm_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    day            TEXT    NOT NULL,
    provider_id    TEXT    NOT NULL DEFAULT '',
    provider_model TEXT    NOT NULL DEFAULT '',
    ok             INTEGER NOT NULL DEFAULT 0,
    aborted        INTEGER NOT NULL DEFAULT 0,
    streamed       INTEGER NOT NULL DEFAULT 0,
    latency_ms     REAL,
    ttft_ms        REAL,
    error_code     TEXT    NOT NULL DEFAULT '',
    error_message  TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_calls_ts        ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_calls_provider  ON llm_calls(provider_id, ts);
CREATE INDEX IF NOT EXISTS idx_calls_day       ON llm_calls(day);

-- 少量全局状态（目前只有 provider_stats 的增量读取游标）。
-- 核心那张表只增不清，靠游标增量读才不会每次巡检都全表扫。
CREATE TABLE IF NOT EXISTS panel_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

-- 每个模型当前的健康状态，是告警状态机的宿主。
-- 必须有显式状态行，否则「连续失败几次才告警」「冷却多久不再重复发」
-- 「已告警过还是刚恢复」这些都没地方记，只能每次现算，既慢又容易重复刷屏。
CREATE TABLE IF NOT EXISTS model_state (
    provider_id      TEXT PRIMARY KEY,
    state            TEXT NOT NULL DEFAULT 'unknown',
    consecutive_fail INTEGER NOT NULL DEFAULT 0,
    consecutive_ok   INTEGER NOT NULL DEFAULT 0,
    last_error_code  TEXT NOT NULL DEFAULT '',
    last_change_at   INTEGER NOT NULL DEFAULT 0,
    muted_until      INTEGER NOT NULL DEFAULT 0,
    samples_total    INTEGER NOT NULL DEFAULT 0,
    updated_at       INTEGER NOT NULL
);

-- 告警事件：可追溯 + 冷却判定的依据
CREATE TABLE IF NOT EXISTS alert_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'open',
    detail          TEXT NOT NULL DEFAULT '',
    count           INTEGER NOT NULL DEFAULT 1,
    opened_at       INTEGER NOT NULL,
    notified_at     INTEGER,
    resolved_at     INTEGER,
    cooldown_until  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_alert_provider ON alert_events(provider_id, kind, state);
"""


# ---------------- 档案枚举与取值约束 ----------------
# paid_overage 单独一类：它平时表现为「免费」，额度用完当天才变红，是最容易漏的预算炸弹。
# per_request 是另一套计价单位（按次而非按 token），有的站点对部分模型就是这么收的。
BILLING_TYPES = ("unknown", "free", "temp_free", "trial", "paid_overage", "paid",
                 "subscription", "per_request")
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
_FLOAT_FIELDS = ("price_input_per_m", "price_output_per_m", "price_cached_per_m",
                 "price_per_call", "rate_multiplier")
_TEXT_FIELDS = ("currency", "note", "model_name")
PROFILE_FIELDS = tuple(_ENUM_FIELDS) + _INT_FIELDS + _FLOAT_FIELDS + _TEXT_FIELDS

# 分组（供应商）级档案：倍率与每日次数限制天然是一家一个值。
VENDOR_INT_FIELDS = ("daily_call_limit",)
VENDOR_FLOAT_FIELDS = ("rate_multiplier",)
VENDOR_TEXT_FIELDS = ("currency", "note")
VENDOR_FIELDS = VENDOR_INT_FIELDS + VENDOR_FLOAT_FIELDS + VENDOR_TEXT_FIELDS

# 只有这几列接受 "YYYY-MM-DD" 字符串（前端日期选择器给的），其余整型列不接受，
# 否则「每日限额」填了个带连字符的怪值会被当成日期悄悄存成时间戳。
_DATE_FIELDS = ("free_until",)

_SPECS = {
    "profile": {"enums": _ENUM_FIELDS, "ints": _INT_FIELDS, "floats": _FLOAT_FIELDS,
                "texts": _TEXT_FIELDS},
    "vendor": {"enums": {}, "ints": VENDOR_INT_FIELDS, "floats": VENDOR_FLOAT_FIELDS,
               "texts": VENDOR_TEXT_FIELDS},
}


def scheduled_default_for(billing_type: str) -> bool:
    """按计费类型推导「是否参与定时巡检」的默认值。"""
    return billing_type in SCHEDULED_DEFAULT_ON


def _coerce(kind: str, field: str, value: Any) -> Any:
    """把前端传来的档案值收敛成合法类型。非法枚举值退回 unknown，不抛异常。"""
    spec = _SPECS[kind]
    if field in spec["enums"]:
        raw = str(value or "").strip()
        return raw if raw in spec["enums"][field] else "unknown"
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if field in spec["ints"]:
            if field in _DATE_FIELDS and isinstance(value, str) and "-" in value:
                day = date.fromisoformat(value.strip()[:10])
                return int(datetime(day.year, day.month, day.day).timestamp())
            n = int(float(value))
            return n if n > 0 else None      # 限额/日期填 0 或负数等于「没填」，落成 NULL
        if field in spec["floats"]:
            price = float(value)
            # 负单价/负倍率没有意义；0 是合法值（免费额度内、订阅内含、倍率 0 = 不计费）
            return price if price >= 0 else 0.0
        if field == "currency":
            return str(value).strip().upper()[:8]
        return str(value).strip()[:200]
    except (TypeError, ValueError):
        return None


def _coerce_profile(field: str, value: Any) -> Any:
    return _coerce("profile", field, value)


def _coerce_vendor(field: str, value: Any) -> Any:
    return _coerce("vendor", field, value)


def resolve_pricing(prof: dict, vendor: Optional[dict]) -> dict:
    """把模型档案与分组档案合成「这个模型实际按什么计价」。

    倍率的优先级是 模型覆盖 → 分组 → 1.0；币种是 模型 → 分组 → 空。
    模型级倍率留 NULL 就永远跟着分组走，改一处十来个模型都跟着变 —— 这正是
    「倍率一般是整个分组都是这个倍率」想要的效果。
    """
    vendor = vendor or {}
    own = prof.get("rate_multiplier")
    group = vendor.get("rate_multiplier")
    if own is not None:
        mult, from_group = float(own), False
    elif group is not None:
        mult, from_group = float(group), True
    else:
        mult, from_group = 1.0, True
    currency = str(prof.get("currency") or "").strip() or str(vendor.get("currency") or "").strip()
    return {
        "multiplier": mult,
        "multiplier_from_group": from_group,
        "currency": currency,
        "daily_call_limit": vendor.get("daily_call_limit"),
    }


def estimate_cost(billing_type: str, prices: dict, usage: dict, multiplier: float = 1.0) -> Optional[float]:
    """按档案单价估算一段用量花掉多少钱；单价没填就返回 None（估不出来不等于 0）。

    token 口径按 AstrBot 的 usage 拆法来：``input_other`` 与 ``input_cached`` 是**互斥**的
    两部分（total = input + cached + output），所以缓存部分单独按缓存价计，
    不能再混进 input 里重复收费。缓存价没填时退回输入价，而不是当 0 ——
    当 0 会把成本系统性低估，而 DeepSeek 这类缓存命中价只是便宜、不是免费。
    """
    if str(billing_type or "") == "free":
        return 0.0
    try:
        mult = float(multiplier)
    except (TypeError, ValueError):
        mult = 1.0
    if mult < 0:
        mult = 1.0
    reqs = max(0, int(usage.get("requests") or 0))
    if str(billing_type or "") == "per_request":
        per_call = prices.get("price_per_call")
        if per_call is None:
            return None
        return round(float(per_call) * reqs * mult, 6)
    p_in, p_out = prices.get("price_input_per_m"), prices.get("price_output_per_m")
    if p_in is None and p_out is None:
        return None
    p_in = float(p_in or 0.0)
    p_out = float(p_out or 0.0)
    p_cached = prices.get("price_cached_per_m")
    p_cached = p_in if p_cached is None else float(p_cached)
    inp = max(0, int(usage.get("input") or 0))
    cached = max(0, int(usage.get("cached") or 0))
    outp = max(0, int(usage.get("output") or 0))
    raw = (inp * p_in + cached * p_cached + outp * p_out) / 1_000_000.0
    return round(raw * mult, 6)



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
        if existing:
            for col in ("allow_scheduled", "allow_command"):
                if col not in existing:
                    # 列名来自上面的固定字面量，不是用户输入
                    await db.execute(f"ALTER TABLE detection_preferences ADD COLUMN {col} INTEGER")
        cur = await db.execute("PRAGMA table_info(model_test_results)")
        cols = {row[1] for row in await cur.fetchall()}
        if cols and "ttft_ms" not in cols:
            await db.execute("ALTER TABLE model_test_results ADD COLUMN ttft_ms REAL")
        cur = await db.execute("PRAGMA table_info(model_profile)")
        cols = {row[1] for row in await cur.fetchall()}
        # 按次单价与模型级倍率覆盖是 v1.3.8 加的；漏了的话档案页一读就是 "no such column"
        for col, decl in (("price_per_call", "REAL"), ("rate_multiplier", "REAL")):
            if cols and col not in cols:
                await db.execute(f"ALTER TABLE model_profile ADD COLUMN {col} {decl}")

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
                        ok, latency_ms, ttft_ms, error_code, error_message, retry_count, checked_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        str(item.get("id") or ""),
                        str(item.get("name") or ""),
                        str(item.get("model") or ""),
                        1 if item.get("ok") else 0,
                        item.get("latency_ms"),
                        item.get("ttft_ms"),
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

    async def probe_window(self, days: float = 7.0, limit: int = 5000) -> list[dict[str, Any]]:
        """取最近 N 天的**探测**明细，带上会话来源。

        实时监测要的是「每一次 LLM 调用」，而调用有两个互不相通的记录处：
        真实对话在核心 provider_stats，手动/指令/定时探测在我们自己的
        model_test_results。少读一边就会把「刚手动测过且失败」的模型显示成正常。
        trigger 列就是区分三种探测入口的唯一依据。
        """
        await self.init()
        since = int(time.time() - max(0.0, float(days)) * 86400)
        sql = """SELECT r.provider_id, r.provider_model, r.ok, r.latency_ms, r.ttft_ms,
                        r.error_code, r.error_message, r.checked_at, s.trigger
                 FROM model_test_results r
                 JOIN test_sessions s ON s.id = r.session_id
                 WHERE r.checked_at >= ?
                 ORDER BY r.checked_at DESC, r.id DESC
                 LIMIT ?"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, (since, int(limit)))
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

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
        """返回 {provider_id: enabled}。缺失的 provider 视为 True（默认勾选）。

        ``enabled`` 列是 NOT NULL，所以这里不需要处理第三态：manual 的「跟随默认」
        在写入侧就被归成了 1（见 ``set_detect_scope``）。
        """
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

        两张表要并集遍历，不能从 ``detection_preferences`` 单侧 LEFT JOIN：
        只标过计费、没碰过通道开关的 provider 在前者里没有行，那样会整个从结果里消失，
        于是「免费模型默认参与定时巡检」这条推导对它根本不生效 —— 定时探测名单
        （``_maybe_probe``）与面板显示读的都是这个函数，结果就是免费模型永远排不进巡检。
        """
        await self.init()
        sql = """SELECT x.pid AS pid, d.enabled AS manual,
                        d.allow_scheduled AS scheduled, d.allow_command AS command,
                        m.billing_type AS billing
                 FROM (SELECT provider_id AS pid FROM detection_preferences
                       UNION
                       SELECT provider_id AS pid FROM model_profile) x
                 LEFT JOIN detection_preferences d ON d.provider_id = x.pid
                 LEFT JOIN model_profile m ON m.provider_id = x.pid"""
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
        # manual 复用老表的 enabled 列，它是 NOT NULL DEFAULT 1 —— 「跟随默认」在这列上
        # 落不回 NULL，直接写会抛 IntegrityError（合并页的「跟随默认」一点就 500）。
        # 而 manual 的默认本来就只有「开」，不随计费变，所以把 None 归一成 1 即可，
        # 语义与 get_detect_scopes 里 explicit=False → manual=True 的推导结果一致。
        if col == "enabled" and value is None:
            value = 1
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

    # ---------- 供应商（分组）档案 ----------
    async def get_all_vendor_profiles(self) -> dict[str, dict[str, Any]]:
        await self.init()
        cols = ", ".join(VENDOR_FIELDS)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(f"SELECT name, {cols}, updated_at FROM vendor_profile")
            rows = await cur.fetchall()
        return {
            r["name"]: {
                "name": r["name"],
                **{f: r[f] for f in VENDOR_FIELDS},
                "updated_at": int(r["updated_at"] or 0),
            }
            for r in rows
        }

    async def upsert_vendor_profile(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        """写入/更新某个供应商分组的档案。name 是面板上的分组名（供应商源 ID）。"""
        name = str(name or "").strip()[:120]
        if not name:
            return {}
        await self.init()
        clean = {f: _coerce_vendor(f, patch.get(f)) for f in VENDOR_FIELDS if f in patch}
        now = int(time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM vendor_profile WHERE name = ?", (name,))
                row = await cur.fetchone()
                merged = {f: row[f] for f in VENDOR_FIELDS} if row else {}
                merged.update(clean)
                if not merged:
                    # 补丁里一个合法字段都没有：不写空行，原样返回现有档案
                    return (await self.get_all_vendor_profiles()).get(name) or {"name": name}
                cols = list(merged)
                placeholders = ", ".join("?" for _ in cols)
                updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
                await db.execute(
                    f"""INSERT INTO vendor_profile (name, {", ".join(cols)}, updated_at)
                        VALUES (?, {placeholders}, ?)
                        ON CONFLICT(name) DO UPDATE SET {updates}, updated_at=excluded.updated_at""",
                    [name] + [merged[c] for c in cols] + [now],
                )
                await db.commit()
        return (await self.get_all_vendor_profiles()).get(name) or {"name": name, **merged}

    # ---------- 用量聚合（给花费估算用） ----------
    async def usage_aggregate(self, days: int = 1) -> dict[str, dict[str, int]]:
        """按 provider_id 聚合最近 N 天的 token 用量与调用次数。

        这里只取原始量：单价与倍率在档案表里，合成规则在 ``resolve_pricing``，
        存储层掺进去的话两处口径会互相打脸。
        """
        await self.init()
        days = max(1, int(days or 1))
        today = date.fromisoformat(time.strftime("%Y-%m-%d", time.localtime()))
        since = (today - timedelta(days=days - 1)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT provider_id,
                          COALESCE(SUM(input_tokens), 0)  AS inp,
                          COALESCE(SUM(cached_tokens), 0) AS cached,
                          COALESCE(SUM(output_tokens), 0) AS outp,
                          COUNT(*)                        AS reqs
                   FROM llm_usage WHERE day >= ?
                   GROUP BY provider_id""",
                (since,),
            )
            rows = await cur.fetchall()
        return {
            str(r["provider_id"] or ""): {
                "input": int(r["inp"] or 0),
                "cached": int(r["cached"] or 0),
                "output": int(r["outp"] or 0),
                "requests": int(r["reqs"] or 0),
            }
            for r in rows
        }

    # ---------- 逐次 LLM 调用（本插件自埋点） ----------
    async def insert_call(self, row: dict) -> None:
        """写一行逐次调用记录。由 call_recorder 的回调调用。"""
        await self.init()
        ts = int(row.get("ts") or time.time())
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT INTO llm_calls
                       (ts, day, provider_id, provider_model, ok, aborted, streamed,
                        latency_ms, ttft_ms, error_code, error_message)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, time.strftime("%Y-%m-%d", time.localtime(ts)),
                     str(row.get("provider_id") or ""), str(row.get("provider_model") or ""),
                     1 if row.get("ok") else 0, 1 if row.get("aborted") else 0,
                     1 if row.get("streamed") else 0,
                     row.get("latency_ms"), row.get("ttft_ms"),
                     str(row.get("error_code") or ""), str(row.get("error_message") or "")[:200]),
                )
                await db.commit()

    async def calls_since(self, last_id: int, limit: int = 2000) -> tuple[list[dict], int]:
        """按主键游标增量读逐次调用，供告警状态机消费。"""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT id, provider_id, provider_model, ok, aborted, ts,
                          latency_ms, ttft_ms, error_code, error_message
                   FROM llm_calls WHERE id > ? ORDER BY id ASC LIMIT ?""",
                (int(last_id), int(limit)),
            )
            rows = await cur.fetchall()
        if not rows:
            return [], int(last_id)
        out = [dict(r) for r in rows]
        return out, int(out[-1]["id"])

    async def calls_stats(self, since_ts: int, until_ts: Optional[int] = None) -> dict[str, dict]:
        """按 provider 聚合一段时间的逐次调用：成败、延迟、首字、最近一次。

        ``aborted``（调用被取消）既不算成功也不算故障，和核心表那边的处理保持一致。
        """
        await self.init()
        sql = """SELECT provider_id,
                        COUNT(*) AS total,
                        SUM(CASE WHEN aborted = 1 THEN 1 ELSE 0 END) AS aborted,
                        SUM(CASE WHEN aborted = 0 AND ok = 0 THEN 1 ELSE 0 END) AS fail,
                        SUM(CASE WHEN aborted = 0 AND ok = 1 THEN 1 ELSE 0 END) AS ok,
                        AVG(CASE WHEN ok = 1 AND aborted = 0 THEN latency_ms END) AS avg_lat,
                        AVG(CASE WHEN ok = 1 AND aborted = 0 AND ttft_ms IS NOT NULL
                                 THEN ttft_ms END) AS avg_ttft
                 FROM llm_calls WHERE ts >= ?"""
        params: list = [int(since_ts)]
        if until_ts:
            sql += " AND ts <= ?"
            params.append(int(until_ts))
        sql += " GROUP BY provider_id"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            agg_rows = await cur.fetchall()
            cur = await db.execute(
                """SELECT c.provider_id, c.ts, c.ok, c.aborted, c.latency_ms, c.ttft_ms,
                          c.error_code, c.error_message
                   FROM llm_calls c
                   JOIN (SELECT provider_id, MAX(id) AS mid FROM llm_calls
                         WHERE ts >= ? GROUP BY provider_id) t ON t.mid = c.id""",
                (int(since_ts),),
            )
            last_rows = {r["provider_id"]: r for r in await cur.fetchall()}
        out: dict[str, dict] = {}
        for r in agg_rows:
            pid = str(r["provider_id"] or "")
            total = int(r["total"] or 0)
            aborted = int(r["aborted"] or 0)
            fail = int(r["fail"] or 0)
            ok = int(r["ok"] or 0)
            counted = total - aborted
            last = last_rows.get(pid)
            out[pid] = {
                "total": total,
                "ok": ok,
                "fail": fail,
                "aborted": aborted,
                "counted": counted,
                "fail_rate": round(fail / counted, 4) if counted else 0.0,
                "avg_latency_ms": round(float(r["avg_lat"]), 1) if r["avg_lat"] is not None else None,
                "avg_ttft_ms": round(float(r["avg_ttft"]), 1) if r["avg_ttft"] is not None else None,
                "last": None if last is None else {
                    "ts": int(last["ts"] or 0),
                    "ok": bool(last["ok"]) and not bool(last["aborted"]),
                    "aborted": bool(last["aborted"]),
                    "latency_ms": last["latency_ms"],
                    "ttft_ms": last["ttft_ms"],
                    "error_code": str(last["error_code"] or ""),
                },
            }
        return out

    async def calls_errors_since(self, since_ts: int, limit: int = 20) -> list[dict]:
        """最近的失败调用明细，给「今天到底哪儿错了」看原因用。"""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT provider_id, provider_model, ts, latency_ms, error_code, error_message
                   FROM llm_calls WHERE ok = 0 AND aborted = 0 AND ts >= ?
                   ORDER BY id DESC LIMIT ?""",
                (int(since_ts), int(limit)),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def calls_count_all(self) -> int:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM llm_calls")
            row = await cur.fetchone()
        return int(row[0] or 0)

    async def calls_latest_id(self) -> int:
        """当前表尾主键。首轮巡检用它把游标放到最后，跳过装上插件之前的陈年失败。"""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COALESCE(MAX(id), 0) FROM llm_calls")
            row = await cur.fetchone()
        return int(row[0] or 0)

    async def cleanup_calls_older_than(self, days: int) -> int:
        """清理 days 天前的逐次调用记录。返回受影响行数。"""
        if days <= 0:
            return 0
        await self.init()
        cutoff = int(time.time()) - int(days) * 86400
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute("DELETE FROM llm_calls WHERE ts < ?", (cutoff,))
                await db.commit()
                return cur.rowcount or 0

    # ---------- 全局小状态（provider_stats 游标等） ----------
    async def get_state_value(self, key: str, default: str = "") -> str:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT value FROM panel_state WHERE key = ?", (key,))
            row = await cur.fetchone()
        return str(row[0]) if row and row[0] is not None else default

    async def set_state_value(self, key: str, value: Any) -> None:
        await self.init()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT INTO panel_state (key, value, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (key, str(value), int(time.time())),
                )
                await db.commit()

    # ---------- 模型状态机 ----------
    async def get_model_states(self) -> dict[str, dict[str, Any]]:
        await self.init()
        cols = ("state", "consecutive_fail", "consecutive_ok", "last_error_code",
                "last_change_at", "muted_until", "samples_total")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT provider_id, {', '.join(cols)} FROM model_state")
            rows = await cur.fetchall()
        out = {}
        for r in rows:
            d = {"provider_id": r["provider_id"]}
            for c in cols:
                # 列分两类：state / last_error_code 是**文本**（后者存的是
                # timeout / auth / unknown 这种码），其余才是整数。
                #
                # 这里曾经一把梭 ``int()``，于是只要有一个模型进过 down（写入了非空错误码），
                # 之后**每一次**读都会抛 `invalid literal for int() with base 10: 'unknown'`；
                # 而 save_model_state 必须先读再写 —— 状态机从此再也写不进去，
                # 面板永远停在最后一次成功的结论上：表现就是「刚测出故障、面板还是绿的」，
                # 而报错只出现在调用方（v1.3.18 由 /切换模型检测 把它顶到了用户面前）。
                d[c] = str(r[c] or "") if c in _STATE_TEXT_COLS else _as_int(r[c])
            out[r["provider_id"]] = d
        return out

    async def save_model_state(self, provider_id: str, patch: dict[str, Any]) -> None:
        """整行 upsert。巡检是单任务的，所以读-改-写之间不会有并发丢更新。"""
        await self.init()
        fields = ("state", "consecutive_fail", "consecutive_ok", "last_error_code",
                  "last_change_at", "muted_until", "samples_total")
        cur_state = (await self.get_model_states()).get(provider_id) or {
            "state": "unknown", "consecutive_fail": 0, "consecutive_ok": 0,
            "last_error_code": "", "last_change_at": 0, "muted_until": 0, "samples_total": 0,
        }
        merged = dict(cur_state)
        merged.update({k: v for k, v in patch.items() if k in fields})
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    f"""INSERT INTO model_state (provider_id, {', '.join(fields)}, updated_at)
                        VALUES ({', '.join('?' for _ in range(len(fields) + 2))})
                        ON CONFLICT(provider_id) DO UPDATE SET
                          {', '.join(f'{f}=excluded.{f}' for f in fields)}, updated_at=excluded.updated_at""",
                    [provider_id] + [merged[f] for f in fields] + [int(time.time())],
                )
                await db.commit()

    async def set_muted(self, provider_id: str, until_ts: int) -> None:
        """只改静默截止时间，不动状态机其它字段。"""
        await self.init()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT INTO model_state (provider_id, muted_until, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(provider_id) DO UPDATE SET muted_until=excluded.muted_until,
                         updated_at=excluded.updated_at""",
                    (provider_id, int(until_ts), int(time.time())),
                )
                await db.commit()

    # ---------- 告警事件 ----------
    async def open_alert(
        self, provider_id: str, kind: str, detail: str, now: int, cooldown_until: int = 0
    ) -> int:
        await self.init()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    """INSERT INTO alert_events (provider_id, kind, state, detail, count, opened_at, cooldown_until)
                       VALUES (?, ?, 'open', ?, 1, ?, ?)""",
                    (provider_id, kind, detail[:300], now, cooldown_until),
                )
                await db.commit()
                return int(cur.lastrowid or 0)

    async def mark_alert_notified(self, alert_id: int, now: int) -> None:
        await self.init()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE alert_events SET state='notified', notified_at=? WHERE id=?",
                    (now, alert_id),
                )
                await db.commit()

    async def pending_alerts(self, max_age_sec: int = 86400, limit: int = 100) -> list[dict[str, Any]]:
        """已创建但从未送达的告警事件，供下一轮重试。

        只认 ``notified_at IS NULL``：投递失败时不写 notified_at，冷却因此不会启动，
        这些事件就必须被重试，否则「管理员还没配好」的第一条告警会被静默吞掉。
        超过 ``max_age_sec`` 的旧事件放弃，避免长期无接收人时无限重试。
        """
        await self.init()
        cutoff = int(time.time()) - max(60, int(max_age_sec))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM alert_events
                   WHERE state='open' AND notified_at IS NULL AND opened_at >= ?
                   ORDER BY opened_at ASC LIMIT ?""",
                (cutoff, int(limit)),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def resolve_alerts(self, provider_id: str, kind: str, now: int) -> int:
        """把该模型该类型的未结告警关掉。恢复通知靠它，否则事件会一直挂着。"""
        await self.init()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    """UPDATE alert_events SET state='resolved', resolved_at=?
                       WHERE provider_id=? AND kind=? AND state IN ('open','notified')""",
                    (now, provider_id, kind),
                )
                await db.commit()
                return cur.rowcount or 0

    async def last_notified_at(self, provider_id: str, kind: str) -> int:
        """该模型该类型最近一次真正发出去告警的时间，冷却判定用。"""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """SELECT MAX(notified_at) FROM alert_events
                   WHERE provider_id=? AND kind=? AND notified_at IS NOT NULL""",
                (provider_id, kind),
            )
            row = await cur.fetchone()
        return int(row[0] or 0) if row else 0

    async def last_notified_map(self) -> dict[tuple[str, str], int]:
        """``{(provider_id, kind): 最近一次发出告警的秒级时间戳}``。

        一次查全而不是逐个问：巡检每轮都要判冷却，N 个模型就是 N 次查询。
        已 resolved 的事件也要算进来——冷却防的是刷屏，不是只防未结的。
        """
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """SELECT provider_id, kind, MAX(notified_at) FROM alert_events
                   WHERE notified_at IS NOT NULL GROUP BY provider_id, kind"""
            )
            rows = await cur.fetchall()
        return {(str(r[0]), str(r[1])): int(r[2] or 0) for r in rows}

    async def open_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM alert_events WHERE state IN ('open','notified')
                   ORDER BY opened_at DESC LIMIT ?""",
                (int(limit),),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def cleanup_alerts(self, days: int) -> int:
        """只清已结的告警事件，未结的必须留着，否则冷却判定会失忆导致重复刷屏。"""
        if days <= 0:
            return 0
        await self.init()
        cutoff = int(time.time()) - days * 86400
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cur = await db.execute(
                    "DELETE FROM alert_events WHERE state='resolved' AND resolved_at < ?", (cutoff,)
                )
                await db.commit()
                return cur.rowcount or 0

    async def reap_orphans(self, live_ids: set[str]) -> dict[str, int]:
        """清掉 provider 已不存在的档案/范围行。

        AstrBot 的 delete_provider 不做任何级联清理，用户改名/删除后这边会留孤儿；
        改名更狠——旧 id 的行会永远显示一个不存在的模型。
        """
        await self.init()
        tables = ("model_profile", "detection_preferences", "model_state", "alert_events")
        removed = {t: 0 for t in tables}
        if not live_ids:
            # 一个 provider 都没有时不做全表删，交给上层确认，避免误清空
            return removed
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                marks = ", ".join("?" for _ in live_ids)
                params = tuple(live_ids)
                for table in tables:
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
        "ttft_ms": row["ttft_ms"] if "ttft_ms" in row.keys() else None,
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
