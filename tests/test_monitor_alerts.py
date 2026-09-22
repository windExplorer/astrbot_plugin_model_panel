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
