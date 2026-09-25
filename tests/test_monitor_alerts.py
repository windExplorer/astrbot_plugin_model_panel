"""告警状态机自检：两种数据源必须给出同一个结论。

用法：
    python tests/test_monitor_alerts.py

为什么专门测这个：巡检的输入源在 v1.3.9 从核心 provider_stats 换成了本插件的
llm_calls（逐次调用），两者字段名不一样（``started_at`` vs ``ts``、对象 vs dict）。
如果状态机只认一种，另一种会静默当成「全部失败」或「无样本」，
表现为「告警突然不发了」或「装上一会儿所有模型一起报故障」，都很难查。

顺带把这次换源要解决的那个真实场景钉成断言：
主模型连挂 4 次、每次都被备用救回来 —— 核心表里它是 0 次失败，逐次表里才是 4 次。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("astrbot", type(sys)("astrbot"))
_api = type(sys)("astrbot.api")


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


_api.logger = _Logger()
sys.modules["astrbot.api"] = _api

import monitor as M  # noqa: E402
from stats_reader import CallRecord  # noqa: E402

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


def eq(actual, expected, label: str) -> None:
    ok = actual == expected
    check(ok, label if ok else f"{label}（期望 {expected!r}，实得 {actual!r}）")


def cfg(**over):
    base = {"enabled": True, "notify_enabled": True, "fail_threshold": 3, "cooldown_sec": 1800}
    base.update(over)
    return M.MonitorConfig(**base)


def call_rows(pid, ok_seq, start=1000):
    """llm_calls 形状：dict + ts 字段。``ok_seq`` 里的 None 表示调用被取消。"""
    return [
        {"id": i + 1, "provider_id": pid, "provider_model": "m", "ts": start + i,
         "ok": 0 if ok is None else (1 if ok else 0),
         "aborted": 1 if ok is None else 0,
         "error_code": "" if ok else ("cancelled" if ok is None else "timeout")}
        for i, ok in enumerate(ok_seq)
    ]


def record_rows(pid, ok_seq, start=1000):
    """核心 provider_stats 形状：CallRecord 对象 + started_at 字段。"""
    return [
        CallRecord(id=i + 1, provider_id=pid, provider_model="m",
                   status="completed" if ok else ("aborted" if ok is None else "error"),
                   started_at=float(start + i), latency_ms=900.0, ttft_ms=200.0,
                   token_input=1, token_cached=0, token_output=1)
        for i, ok in enumerate(ok_seq)
    ]


def kinds(decisions):
    """该发的告警种类（被冷却/静音压掉的不算）。"""
    return sorted(d.kind for d in decisions if d.should_notify)


def main() -> int:
    now = 2_000_000
    c = cfg()

    print("[两种数据源结论一致]")
    seq = [False, False, False, False]
    d_calls = M.evaluate(call_rows("p1", seq), {}, c, now)
    d_core = M.evaluate(record_rows("p1", seq), {}, c, now)
    check(any(d.kind == M.KIND_FAIL and d.should_notify for d in d_calls),
          "逐次表（dict + ts）：连续失败 4 次触发故障告警")
    eq([d.kind for d in d_core], [d.kind for d in d_calls],
       "核心表（对象 + started_at）结论与逐次表一致")
    eq(max((d.consecutive_fail for d in d_calls), default=0), 4, "连续失败次数按逐次调用累计")

    print("[被备用救回来的失败]")
    # 核心表里这一轮的 provider_id 是备用模型，主模型一行都没有；
    # 逐次表里主模型有 4 行失败 —— 这正是换源要解决的问题。
    rescued = call_rows("主模型", [False] * 4) + record_rows("备用模型", [True] * 4)
    decisions = M.evaluate(rescued, {}, c, now)
    by_pid = {d.provider_id: d for d in decisions}
    check(by_pid.get("主模型") is not None and by_pid["主模型"].kind == M.KIND_FAIL,
          "主模型连挂 4 次即使每轮都被备用救回，仍然报故障")
    check(by_pid.get("备用模型") is None or not by_pid["备用模型"].kind,
          "备用模型自己没失败过，不该被连带报错")

    print("[打断与恢复]")
    eq(kinds(M.evaluate(call_rows("p2", [None, None, None]), {}, c, now)), [],
       "全是 aborted 的中性事件不产生任何告警")
    eq(kinds(M.evaluate(call_rows("p3", [False, True, False, False, False]), {}, c, now)),
       [M.KIND_FAIL], "夹一次成功仍凑得出连续失败（成功只清零当时的串）")
    prev = {"p4": {"state": "down", "consecutive_fail": 4, "consecutive_ok": 0}}
    eq(kinds(M.evaluate(call_rows("p4", [True, True]), prev, c, now)), [M.KIND_RECOVER],
       "连续成功 2 次才算恢复")
    eq(kinds(M.evaluate(call_rows("p5", [True]), dict(prev, p5=dict(prev["p4"])), c, now)), [],
       "只成功 1 次不宣布恢复，避免抖一下就说好了")

    print("[冷却]")
    calls = call_rows("p6", [False, False, False])
    hot = M.evaluate(calls, {}, c, now, cooldown_active=lambda pid, kind: True)
    eq(kinds(hot), [], "冷却期内不重复推送")
    check(any(d.kind == M.KIND_FAIL and d.suppressed == "cooldown" for d in hot),
          "被冷却压掉时 state 仍然更新，只是不发通知")
    eq(kinds(M.evaluate(calls, {}, c, now, cooldown_active=lambda pid, kind: False)),
       [M.KIND_FAIL], "冷却结束后同批失败仍可再报")

    print("[阈值与样本数]")
    eq(kinds(M.evaluate(call_rows("p7", [False, False]), {}, c, now)), [],
       "未达连败阈值不告警")

    print("[失败原因跟着判定一起出来]")
    # 告警卡片要显示「为什么坏」，所以判定里就必须带上原因，不能等发卡片时再回头查
    rows = call_rows("p8", [False, False, False])
    for r in rows:
        r["error_message"] = "HTTP 502 Bad Gateway from upstream"
    d = M.evaluate(rows, {}, c, now)[0]
    eq(d.detail.get("error_codes"), {"timeout": 3}, "错误码分布按出现次数统计")
    eq(d.detail.get("last_error_code"), "timeout", "最近一次失败的错误码")
    eq(d.detail.get("last_error_message"), "HTTP 502 Bad Gateway from upstream",
       "最近一次失败的原始短文本")
    eq(d.last_error_code, "timeout", "down 状态下 last_error_code 用本批的码（不用上一轮的）")

    print("[核心表没有原因时不编造]")
    d2 = M.evaluate(record_rows("p9", [False] * 3), {}, c, now)[0]
    eq(d2.detail.get("error_codes"), {}, "核心表没有 error 字段 → 不产生错误码")
    eq(d2.detail.get("last_error_message"), "", "拿不到原文就留空，卡片写「原因未知」而不是猜一个")
    eq(d2.last_error_code, "", "本批没有码、上一轮也没有 → 保持空")

    print("[错误原文里的凭据必须抹掉]")
    # 告警是发到聊天软件里的，异常串完全可能把请求头/带凭据的 URL 回显出来
    leak = [{**r, "error_message": "api_key=sk-abcdef123456 rejected"} for r in call_rows("p10", [False] * 3)]
    d3 = M.evaluate(leak, {}, c, now)[0]
    check("sk-abcdef123456" not in d3.detail.get("last_error_message", ""),
          "错误原文里的 api_key 被脱敏")
    check("api_key=sk" not in d3.detail.get("last_error_message", ""),
          "连 key=value 这种形态也一起抹掉")

    print("[限时免费到期提醒]")
    DAY = 86400
    c2 = cfg(free_alert_days=3)
    now2 = 2_000_000
    names2 = {"pf1": "Vendor/free-model"}
    # 多给 3 小时余量：只前进 30 分钟时 days_left 必须还是 2，否则测的就不是去重了
    prof = {"pf1": {"billing_type": "temp_free", "free_until": now2 + 2 * DAY + 3 * 3600}}

    first = M.free_expiry_decisions(prof, c2, now2, None, names2)
    eq([d.reason for d in first], ["限时免费 2 天后到期"], "进入窗口时提醒一次")
    check(first[0].should_notify, "首轮没被任何条件压住")

    # 这是本次要修的正题：故障告警冷却只有 30 分钟，临期提醒不能跟着每半小时催一遍
    last = {"pf1": first[0].reason}
    again = M.free_expiry_decisions(prof, c2, now2 + 1800, lambda pid, kind: False, names2, last)
    eq([d.suppressed for d in again], ["unchanged"], "半小时后再巡检：同一个剩余天数不再重发")
    check(not any(d.should_notify for d in again), "去重后这一轮一条都不发")
    check(len(again) == 1, "仍然产出判定（面板要显示临期状态），只是不投递")

    eq([d.reason for d in M.free_expiry_decisions(prof, c2, now2 + DAY, None, names2, last)],
       ["限时免费 1 天后到期"], "天数变了要重新提醒")
    check(M.free_expiry_decisions(prof, c2, now2 + DAY, None, names2, last)[0].should_notify,
          "新天数照常投递")

    today = {"pf1": {"billing_type": "temp_free", "free_until": now2 + 3600}}
    eq([d.reason for d in M.free_expiry_decisions(today, c2, now2, None, names2)],
       ["限时免费今天到期"], "最后一天说人话，不是「0 天后到期」")
    check(M.free_expiry_decisions(today, c2, now2, None, names2)[0].suppressed == "",
          "今天到期这条不会被去重误伤（文案和昨天不同）")

    expired = {"pf1": {"billing_type": "temp_free", "free_until": now2 - 60}}
    check(M.free_expiry_decisions(expired, c2, now2, None, names2) == [], "已过期不再催")
    far = {"pf1": {"billing_type": "temp_free", "free_until": now2 + 9 * DAY}}
    check(M.free_expiry_decisions(far, c2, now2, None, names2) == [], "窗口外不提醒")
    paid = {"pf1": {"billing_type": "paid", "free_until": now2 + 2 * DAY}}
    check(M.free_expiry_decisions(paid, c2, now2, None, names2) == [], "非限时免费类型不参与")
    check(M.free_expiry_decisions(prof, cfg(free_alert_days=0), now2, None, names2) == [],
          "配置设 0 = 彻底关掉提醒")

    off = M.free_expiry_decisions(prof, cfg(free_alert_days=3, notify_enabled=False), now2, None, names2)
    eq([d.suppressed for d in off], ["notify_off"], "全局静音优先于去重判定")
    cool = M.free_expiry_decisions(prof, c2, now2, lambda pid, kind: True, names2,
                                   {"pf1": "限时免费 3 天后到期"})
    eq([d.suppressed for d in cool], ["cooldown"], "文案不同但仍在冷却里时，冷却兜住")

    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for f in _failures:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
