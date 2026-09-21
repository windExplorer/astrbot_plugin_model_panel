"""AstrBot 核心 provider_stats 表的只读适配层。

为什么读这张表而不是自己埋点：AstrBot 4.28.1 在每轮内部 agent 对话结束时已经写了一行
（``start_time`` / ``end_time`` / ``time_to_first_token`` / ``status``），自己埋点等于重复造轮子。
而 ``on_llm_response`` 钩子在模型报错时**不会触发**（runner 合成 role="err" 后直接 return），
所以「失败」这一半只能从这张表拿，没有第二条路。

已知盲区（读侧必须知道，否则统计会骗人，详见 docs/模型监测与配置规划.md 第 3.6 节）：

- 插件内部调用（``context.llm_generate`` / ``req_llm``）与第三方 agent 不进这张表；
- 粒度是「一轮对话」，``end_time - start_time`` **含工具执行与多步 agent 循环**，不是模型纯响应时间；
- 只有流式路径会设 ``time_to_first_token``，非流式恒为 0 —— 这里把 0 一律当「未测到」而非「很快」；
- 表**没有任何保留期清理**，行数只增不减，所以查询一律带 LIMIT 并按主键游标增量读。

刻意不 import 核心的 ``ProviderStat`` ORM 模型：只用裸 SQL + 表名/列名，核心挪动模型定义也不会打断这里。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from astrbot.api import logger

# created_at 由 SQLAlchemy 的 SQLite DATETIME 处理器写成 "YYYY-MM-DD HH:MM:SS.ffffff"（UTC、无偏移后缀），
# 该格式定长且字典序与时间序一致，所以直接做字符串比较即可，不要塞 datetime 对象给 DBAPI
# （默认适配器用的是 'T' 分隔符，和这个格式不一致，会静默查出 0 行）。
_TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

# 单轮对话的状态取值，来自核心 _record_internal_agent_stats
STATUS_OK = "completed"
STATUS_ERROR = "error"
STATUS_ABORTED = "aborted"

_COLS = """
    id, provider_id, provider_model, status,
    start_time, end_time, time_to_first_token,
    token_input_other, token_input_cached, token_output
"""

# 表是否存在（老版本 AstrBot 没有这张表）
SQL_PING = "SELECT 1 FROM provider_stats LIMIT 1"

# 按时间窗取原始行；created_at 是 UTC 字符串，:since 由 _to_utc_str 生成
SQL_WINDOW = f"SELECT {_COLS} FROM provider_stats WHERE created_at >= :since ORDER BY id DESC LIMIT :limit"

# 按主键游标增量读，用于定时聚合（created_at 无索引，窗口查询已经要全表扫，增量走游标更省）
SQL_SINCE = f"SELECT {_COLS} FROM provider_stats WHERE id > :last_id ORDER BY id ASC LIMIT :limit"


# 首轮巡检用：把游标直接放到表尾，避免把陈年历史当新数据评估后立刻告警
SQL_LATEST = "SELECT COALESCE(MAX(id), 0) FROM provider_stats"


def _to_utc_str(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(_TS_FORMAT)


@dataclass
class CallRecord:
    """一行 provider_stats，已做单位归一（延迟统一成毫秒）。"""

    id: int
    provider_id: str
    provider_model: str
    status: str
    started_at: float
    latency_ms: Optional[float]
    ttft_ms: Optional[float]
    token_input: int
    token_cached: int
    token_output: int

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def aborted(self) -> bool:
        """用户主动打断，既不算成功也不算故障。"""
        return self.status == STATUS_ABORTED

    @property
    def failed(self) -> bool:
        return self.status == STATUS_ERROR


def _row_to_record(row: Any) -> CallRecord:
    """把一行结果转成 CallRecord。

    取列一律按**列名**而非下标，避免核心调整 SELECT 顺序时静默错位。
    sqlite Row / dict / SQLAlchemy Row 都能用 ``row[name]``，SQLAlchemy Row 需要 _mapping。
    """
    data = getattr(row, "_mapping", None)
    if data is None:
        data = row

    def num(key: str, default: float = 0.0) -> float:
        try:
            v = data[key]
        except (KeyError, IndexError, TypeError):
            return default
        return float(v) if v is not None else default

    def text_of(key: str) -> str:
        try:
            v = data[key]
        except (KeyError, IndexError, TypeError):
            return ""
        return str(v or "")

    start = num("start_time")
    end = num("end_time")
    # 核心用的是 time.time() 不是单调时钟，系统校时会产生 end < start；
    # 和核心自己的聚合一样，这种行直接丢弃不计时。
    latency = round((end - start) * 1000.0, 1) if end > start > 0 else None
    ttft = num("time_to_first_token")
    return CallRecord(
        id=int(num("id")),
        provider_id=text_of("provider_id"),
        provider_model=text_of("provider_model"),
        status=text_of("status") or STATUS_OK,
        started_at=start,
        latency_ms=latency,
        # 0 秒 = 非流式路径没测，不是「快到一个数都没有」
        ttft_ms=round(ttft * 1000.0, 1) if ttft > 0 else None,
        token_input=int(num("token_input_other")),
        token_cached=int(num("token_input_cached")),
        token_output=int(num("token_output")),
    )


class LiveStatsReader:
    """provider_stats 只读访问器。

    Args:
        get_db: 无参可调用，返回 AstrBot 的 BaseDatabase（即 ``context.get_db()``）。
            用可调用而不是直接传库对象，是为了让插件热重载后仍能拿到当前实例。
    """

    # 表读不到时只告警一次，避免每条消息刷日志
    _missing_logged = False

    def __init__(self, get_db: Callable[[], Any]):
        self._get_db = get_db
        self.available = True

    async def _query(self, sql: str, params: dict) -> list[CallRecord] | None:
        """执行一条只读查询。返回 None 表示这次读取不可用（调用方据此降级）。"""
        if not self.available:
            return None
        try:
            from sqlalchemy import text

            db = self._get_db()
            if db is None:
                raise RuntimeError("context.get_db() 返回 None")
            async with db.get_db() as session:
                result = await session.execute(text(sql), params)
                return [_row_to_record(r) for r in result.fetchall()]
        except Exception as e:
            # 表不存在多半是老版本 AstrBot；其它异常也不能影响对话主流程
            if not LiveStatsReader._missing_logged:
                LiveStatsReader._missing_logged = True
                logger.warning(f"[ModelPanel] 读取 provider_stats 失败，实时监测降级为不可用: {e}")
            self.available = False
            return None

    async def probe(self) -> bool:
        """探测这张表能不能读。只用来决定面板要不要显示监测页。"""
        try:
            from sqlalchemy import text

            db = self._get_db()
            async with db.get_db() as session:
                await session.execute(text(SQL_PING))
            self.available = True
            return True
        except Exception:
            self.available = False
            return False

    async def fetch_window(self, days: float = 7.0, limit: int = 20000) -> tuple[list[CallRecord], bool]:
        """取最近 N 天的原始调用行。

        Returns:
            (记录列表, 是否被 limit 截断)。截断时调用方应把结果标成「样本不完整」。
        """
        rows = await self._query(
            SQL_WINDOW,
            {"since": _to_utc_str(time.time() - days * 86400.0), "limit": int(limit)},
        )
        if rows is None:
            return [], False
        rows.sort(key=lambda r: r.started_at)
        return rows, len(rows) >= limit

    async def fetch_since(self, last_id: int, limit: int = 2000) -> tuple[list[CallRecord], int]:
        """按主键游标增量读，供定时聚合调用。

        Returns:
            (记录列表, 新的游标位置)。没有新行或读不到时原样返回 last_id。
        """
        rows = await self._query(SQL_SINCE, {"last_id": int(last_id), "limit": int(limit)})
        if not rows:
            return [], last_id
        return rows, rows[-1].id

    async def latest_id(self) -> Optional[int]:
        """当前表尾主键。首轮巡检用它把游标直接放到最后，跳过陈年历史——
        否则装上插件的第一秒就会因为「上周那三次失败」发一条莫名其妙的告警。

        返回 ``None`` 表示读不到（表不存在或库不可用）。
        """
        try:
            from sqlalchemy import text

            db = self._get_db()
            async with db.get_db() as session:
                result = await session.execute(text(SQL_LATEST))
                row = result.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            self.available = False
            return None


def percentile(values: list[float], pct: float) -> Optional[float]:
    """最近秩法取分位数。空列表返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    idx = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def summarize(records: Iterable[CallRecord]) -> dict[str, dict[str, Any]]:
    """按 provider_id 聚合成实时健康指标。

    口径要点：

    - ``total`` 含 aborted，但 ``fail_rate`` 的分母是 ``counted = total - aborted``，
      用户主动打断不是模型故障；
    - ``ttft_*`` 只统计真正测到的样本（``ttft_samples``），非流式模型的 0 不参与平均，
      否则平均首字延迟会被一堆 0 拉穿；
    - ``error_code`` 实时数据里拿不到（核心只记 status 不记原因），留空，由探测层补。
    """
    buckets: dict[str, dict[str, Any]] = {}
    for r in records:
        pid = r.provider_id or "(unknown)"
        b = buckets.setdefault(
            pid,
            {
                "provider_id": pid,
                "provider_model": "",
                "total": 0,
                "ok": 0,
                "fail": 0,
                "aborted": 0,
                "_lat": [],
                "_ttft": [],
                "_tok": 0,
                "last_ts": 0,
                "last_status": "",
            },
        )
        if r.provider_model:
            b["provider_model"] = r.provider_model
        b["total"] += 1
        if r.aborted:
            b["aborted"] += 1
        elif r.ok:
            b["ok"] += 1
        else:
            b["fail"] += 1
        if r.latency_ms is not None:
            b["_lat"].append(r.latency_ms)
        if r.ttft_ms is not None:
            b["_ttft"].append(r.ttft_ms)
        b["_tok"] += r.token_input + r.token_cached + r.token_output
        if r.started_at >= b["last_ts"]:
            b["last_ts"] = r.started_at
            b["last_status"] = r.status

    out: dict[str, dict[str, Any]] = {}
    for pid, b in buckets.items():
        counted = b["total"] - b["aborted"]
        lat, ttft = b.pop("_lat"), b["_ttft"]
        b["counted"] = counted
        b["tokens"] = b.pop("_tok")
        b["fail_rate"] = round(b["fail"] / counted, 4) if counted else 0.0
        b["latency_samples"] = len(lat)
        b["avg_latency_ms"] = round(sum(lat) / len(lat), 1) if lat else None
        b["p95_latency_ms"] = percentile(lat, 95)
        b["ttft_samples"] = len(ttft)
        b["avg_ttft_ms"] = round(sum(ttft) / len(ttft), 1) if ttft else None
        b["p95_ttft_ms"] = percentile(ttft, 95)
        del b["_ttft"]
        out[pid] = b
    return out
