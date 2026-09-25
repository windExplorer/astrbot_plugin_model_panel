"""告警规则引擎：只做判定，不碰数据库也不发消息。

为什么单独一个模块并且保持纯函数：这套规则的输入（一批调用记录 + 上一轮状态）和
输出（每个模型的新状态 + 要不要告警）完全可以离线断言。一旦掺进 IO，
「连续失败几次才告警」「冷却」「恢复通知」这些就只能靠在线上跑一遍来验，代价高得多。

三条判定纪律：

- **连续**失败才算故障，不是窗口内失败率——一次抖动就告警会让人很快开始忽略告警。
- ``aborted``（用户主动打断）不改变任何计数器，它既不是成功也不是故障。
- 恢复必须**连续成功**若干次才算，避免「一次成功 → 发恢复 → 立刻又失败」的抖动通知。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

# 告警类型
KIND_FAIL = "fail_burst"
KIND_RECOVER = "recovered"
KIND_FREE_EXPIRING = "free_expiring"

STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_DOWN = "down"
STATE_UNKNOWN = "unknown"

# 从 down 恢复需要连续成功次数。设 2 而不是 1：单次成功就宣布恢复会被抖动骗。
RECOVER_OK = 2
# 未达故障阈值但确有失败时标降级，让「在变差」和「坏了」在界面上可区分
DEGRADED_FAIL_RATE = 0.10

# 告警文案里绝不能出现的凭据形态。
# 顺序有讲究：通用的 "key=value" 规则必须放最后，否则 "Authorization: Bearer xxx"
# 会先被它吃掉 "Bearer" 这个词，导致真正的令牌值反而逃过脱敏。
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}"),
    re.compile(r"://[^:/\s]+:[^@/\s]+@"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?key|secret[_-]?key|authorization|token|password)\b\s*[=:]\s*[^\s,;&]+"),
)


@dataclass
class MonitorConfig:
    """巡检、告警与定时探测参数。全部有安全默认值，配置缺失时不会炸。"""

    enabled: bool = True
    notify_enabled: bool = True
    interval_sec: int = 180
    fail_threshold: int = 3
    cooldown_sec: int = 1800
    alert_retention_days: int = 30
    free_alert_days: int = 3
    # 定时探测会真的打模型、真的花钱，所以默认关闭，要用户显式打开。
    probe_enabled: bool = False
    probe_interval_min: int = 30
    probe_daily_budget: int = 500
    probe_concurrency: int = 3

    @classmethod
    def from_config(cls, cfg: Any) -> "MonitorConfig":
        """从 AstrBotConfig（dict 子类）容错读取。任何一项坏掉都退回默认值。"""
        out = cls()
        get = getattr(cfg, "get", None)
        if not callable(get):
            return out
        try:
            out.enabled = bool(get("monitor_enabled", True))
            out.notify_enabled = bool(get("alert_notify_enabled", True))
            out.interval_sec = max(30, int(get("monitor_interval_sec") or out.interval_sec))
            out.fail_threshold = max(1, int(get("alert_fail_threshold") or out.fail_threshold))
            out.cooldown_sec = max(60, int(get("alert_cooldown_min") or 0) * 60) or out.cooldown_sec
            out.alert_retention_days = max(0, int(get("alert_retention_days") or out.alert_retention_days))
            out.free_alert_days = max(0, int(get("free_expiry_alert_days") or 0))
            out.probe_enabled = bool(get("probe_enabled", False))
            out.probe_interval_min = max(5, int(get("probe_interval_min") or out.probe_interval_min))
            raw_budget = get("probe_daily_budget")
            # 0 = 不限，沿用本仓库 history_retention_days 的既有约定
            out.probe_daily_budget = max(0, int(raw_budget)) if raw_budget is not None else out.probe_daily_budget
            out.probe_concurrency = max(1, min(8, int(get("probe_concurrency") or out.probe_concurrency)))
        except (TypeError, ValueError):
            return cls()
        return out


@dataclass
class Decision:
    """一个模型这一轮的判定结果。

    ``detail`` 里与「为什么坏」有关的键（告警卡片直接读它们，改名前先看 main._alert_cause）：

    - ``error_codes``：本批调用里各错误码的出现次数 ``{code: n}``，用于「超时 ×2 / 鉴权失败 ×1」
      这种分布描述。核心 provider_stats 只有 status、没有原因，所以这一项可能为空。
    - ``last_error_code`` / ``last_error_message``：本批里**最近一次失败**的错误码与原始短文本
      （已脱敏、截断）。取的是这一批的真实记录，不是上一轮存下来的码 ——
      第一轮就告警时，上一轮状态里的码往往是空的。
    """

    provider_id: str
    state: str
    consecutive_fail: int = 0
    consecutive_ok: int = 0
    samples: int = 0
    fails: int = 0
    action: str = ""  # "" / alert / recover
    kind: str = ""
    reason: str = ""
    suppressed: str = ""  # "" / muted / cooldown / notify_off
    last_error_code: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def should_notify(self) -> bool:
        return bool(self.action) and not self.suppressed


def redact(text: Any, limit: int = 160) -> str:
    """抹掉可能混进告警文案的凭据。

    探测的 error_message 只截断到 80 字符，但**截断不保证不含 api_key**——
    异常字符串完全可能把请求头或带凭据的 URL 回显出来，而告警是发到聊天软件里的。
    """
    out = str(text or "")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("***", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:limit]


def _default_state() -> dict[str, Any]:
    return {
        "state": STATE_UNKNOWN, "consecutive_fail": 0, "consecutive_ok": 0,
        "last_error_code": "", "muted_until": 0, "samples_total": 0, "last_change_at": 0,
    }


def evaluate(
    records: Iterable[Any],
    states: dict[str, dict[str, Any]],
    cfg: MonitorConfig,
    now: int,
    cooldown_active: Optional[Callable[[str, str], bool]] = None,
) -> list[Decision]:
    """吃一批新增的真实调用记录，吐每个被触及模型的判定。

    Args:
        records: ``stats_reader.CallRecord`` 序列。
        states: 上一轮的 ``model_state``，键为 provider_id。
        cfg: 阈值与冷却配置。
        now: 当前秒级时间戳。
        cooldown_active: ``(provider_id, kind) -> bool``，由调用方接数据库实现；
            不传表示不做冷却判定（测试或首次启动时用）。
    """
    def _field(r: Any, name: str, default: Any = None) -> Any:
        """同时吃核心的 CallRecord 和本插件 llm_calls 的 dict 行。

        两个数据源字段名不一样（``started_at`` vs ``ts``），而状态机只有一份实现这点
        不能破 —— 否则「连续失败几次算故障」在对话路和逐次路上会给出不同结论。
        """
        if isinstance(r, dict):
            if name == "started_at":
                return r.get("ts", default)
            return r.get(name, default)
        return getattr(r, name, default)

    grouped: dict[str, list[Any]] = {}
    for r in records:
        pid = str(_field(r, "provider_id", "") or "") or "(unknown)"
        grouped.setdefault(pid, []).append(r)

    out: list[Decision] = []
    for pid, rows in grouped.items():
        prev = dict(_default_state())
        prev.update(states.get(pid) or {})
        fail_streak = int(prev.get("consecutive_fail") or 0)
        ok_streak = int(prev.get("consecutive_ok") or 0)
        fails = oks = aborted = 0
        # 「为什么坏」要跟着判定一起出来，不能等告警时再去猜：这批记录里就有错误码与原文，
        # 而告警卡片的读者第一句话永远是「它是怎么坏的」。
        err_codes: dict[str, int] = {}
        last_err_code = last_err_msg = ""
        for r in sorted(rows, key=lambda x: _field(x, "started_at", 0) or 0):
            if _field(r, "aborted", False):
                aborted += 1
                continue
            if _field(r, "ok", False):
                oks += 1
                fail_streak = 0
                ok_streak += 1
            else:
                fails += 1
                ok_streak = 0
                fail_streak += 1
                # 按时间序推进，循环结束时留下的就是最近一次失败的原因。
                # 核心 provider_stats 路径这两个字段恒为空（表里只记 status），
                # 那就保持空串，由调用方在卡片上写「原因未知」而不是编一个。
                code = str(_field(r, "error_code", "") or "")
                msg = str(_field(r, "error_message", "") or "")
                if code:
                    err_codes[code] = err_codes.get(code, 0) + 1
                elif msg:
                    err_codes["unknown"] = err_codes.get("unknown", 0) + 1
                if code or msg:
                    last_err_code, last_err_msg = code, msg

        counted = oks + fails
        rate = (fails / counted) if counted else 0.0
        before = str(prev.get("state") or STATE_UNKNOWN)
        if fail_streak >= cfg.fail_threshold:
            state = STATE_DOWN
        elif before == STATE_DOWN and ok_streak < RECOVER_OK:
            # 还没攒够连续成功，先别急着宣布恢复
            state = STATE_DOWN
        elif rate > DEGRADED_FAIL_RATE:
            state = STATE_DEGRADED
        elif counted:
            state = STATE_HEALTHY
        else:
            state = before or STATE_UNKNOWN

        d = Decision(
            provider_id=pid,
            state=state,
            consecutive_fail=fail_streak,
            consecutive_ok=ok_streak,
            samples=counted,
            fails=fails,
            # 优先用**本批**记录里的码；本批没有（核心表不记原因）才回落到上一轮存下来的
            last_error_code="" if state != STATE_DOWN else (last_err_code or str(prev.get("last_error_code") or "")),
            detail={
                "aborted": aborted, "ok": oks, "fail": fails,
                "fail_rate": round(rate, 4), "before": before,
                "error_codes": err_codes,
                "last_error_code": last_err_code,
                # 原文可能把请求头/带凭据的 URL 回显出来，而告警是发到聊天软件里的 —— 必须先脱敏
                "last_error_message": redact(last_err_msg, 90),
            },
        )
        if state == STATE_DOWN and before != STATE_DOWN:
            d.action, d.kind = "alert", KIND_FAIL
            d.reason = f"连续失败 {fail_streak} 次"
        elif before == STATE_DOWN and state != STATE_DOWN:
            d.action, d.kind = "recover", KIND_RECOVER
            d.reason = f"已连续成功 {ok_streak} 次"

        if d.action:
            if int(prev.get("muted_until") or 0) > now:
                d.suppressed = "muted"
            elif not cfg.notify_enabled:
                d.suppressed = "notify_off"
            elif cooldown_active and cooldown_active(pid, d.kind):
                # 恢复通知也要冷却：模型在阈值附近抖动时，down→恢复→down 会来回发，
                # 只给故障告警设冷却的话，恢复这一路就成了刷屏的缺口。
                d.suppressed = "cooldown"
        out.append(d)
    return out


def free_expiry_decisions(
    profiles: dict[str, dict[str, Any]],
    cfg: MonitorConfig,
    now: int,
    cooldown_active: Optional[Callable[[str, str], bool]] = None,
    names: Optional[dict[str, str]] = None,
    last_sent: Optional[dict[str, str]] = None,
) -> list[Decision]:
    """限时免费临期提醒。

    已过期不再提醒——到期后到底转付费还是继续免费得人判断，天天催只会变成噪音。

    ``last_sent`` 是「上次**发出去**的那条提醒的文案」（按 provider_id 索引，来自 alert_events）。
    文案里带着剩余天数，所以拿它一比就等于「同一个剩余天数只发一次」：
    剩 3 天、2 天、1 天、今天各一条，之后整天待在窗口里也不再重复刷屏。
    为什么不复用 ``cooldown_active``：那是故障告警的冷却（默认 30 分钟），
    故障要按周期催、临期提醒不用 —— 混用会变成「三天里每半小时一条、共 144 条」，
    而配置项的说明写的恰恰是「提醒一次」。改了 ``free_until`` 会让天数变化，自然重新提醒。
    """
    if cfg.free_alert_days <= 0:
        return []
    out: list[Decision] = []
    for pid, prof in (profiles or {}).items():
        if str(prof.get("billing_type") or "") != "temp_free":
            continue
        try:
            until = int(prof.get("free_until") or 0)
        except (TypeError, ValueError):
            continue
        if until <= 0:
            continue
        days_left = int((until - now) // 86400)
        if not (0 <= days_left <= cfg.free_alert_days):
            continue
        reason = "限时免费今天到期" if days_left <= 0 else f"限时免费 {days_left} 天后到期"
        d = Decision(
            provider_id=pid,
            state=STATE_HEALTHY,
            action="alert",
            kind=KIND_FREE_EXPIRING,
            reason=reason,
            detail={"days_left": days_left, "name": str((names or {}).get(pid) or pid)},
        )
        if not cfg.notify_enabled:
            d.suppressed = "notify_off"
        elif last_sent and str(last_sent.get(pid) or "") == reason:
            d.suppressed = "unchanged"
        elif cooldown_active and cooldown_active(pid, KIND_FREE_EXPIRING):
            d.suppressed = "cooldown"
        out.append(d)
    return out
