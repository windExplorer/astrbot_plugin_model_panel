"""调用台账（_merge_ledger）三路合并的自检。

用法：
    uv run --no-project --python 3.12 python tests/test_merge_ledger.py

为什么值得单独测：台账决定「实时监测的最后一次调用」和「模型状态卡的次数」。
v1.3.22 之前它只读两路（核心 provider_stats + 探测），而 AstrBot 后台的
「提供商测试」、WebChat 聊天界面、其它插件直调 provider 这些调用**只存在于
本插件埋的 llm_calls 里** —— 于是它们既不进「最后一次调用」、也不进次数统计。

三路来源有一条不能违反的规则：**llm_calls 与 provider_stats 描述的是同一批调用，
不能相加**（agent 一轮对话两边各记一条，加起来就是双倍）。有 llm_calls 时次数全部
改由它出，provider_stats 只贡献它独有的 token；没有时才回落 provider_stats。
"""

from __future__ import annotations

import ast
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- 最小 astrbot 桩（stats_reader 依赖 astrbot.api.logger） ---
if "astrbot" not in sys.modules:
    import logging
    import types

    _astrbot = types.ModuleType("astrbot")
    _api = types.ModuleType("astrbot.api")
    _api.logger = logging.getLogger("test")  # type: ignore[attr-defined]
    _astrbot.api = _api  # type: ignore[attr-defined]
    sys.modules["astrbot"] = _astrbot
    sys.modules["astrbot.api"] = _api

from stats_reader import CallRecord, percentile as _percentile  # noqa: E402

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


def eq(actual, expected, label: str) -> None:
    ok = actual == expected
    check(ok, label if ok else f"{label}（期望 {expected!r}，实得 {actual!r}）")


def load_merge_ledger():
    """从 main.py 抠出 ``_merge_ledger`` / ``_probe_source`` 原样执行。

    main.py 依赖整个 astrbot 运行时（quart / astrbot.core…），本机 import 不了；
    但这两个函数只用到彼此和 ``percentile``（stats_reader，真 import），抠出来就能跑。
    与 test_card_rows.py 的做法一致：**原样 exec，不抄一份**，抄的代码测不出真问题。
    """
    src = io.open(ROOT / "main.py", encoding="utf-8").read()
    tree = ast.parse(src)
    wanted = {"_merge_ledger", "_probe_source"}
    segs = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in wanted]
    got = {n.name for n in segs}
    assert got == wanted, f"main.py 里找不到这些定义：{sorted(wanted - got)}"
    mod = ast.Module(body=segs, type_ignores=[])
    ns = {"percentile": _percentile}
    exec(compile(mod, "main.py", "exec"), ns)  # noqa: S102 - 测试专用
    return ns["_merge_ledger"]


_merge_ledger = load_merge_ledger()


def live(pid: str, *, ts: float, status: str = "completed",
         latency: float = 1000.0, tokens: int = 100) -> CallRecord:
    """构造一行核心 provider_stats（token 用量只有它有）。"""
    return CallRecord(
        id=int(ts), provider_id=pid, provider_model="m", status=status,
        started_at=ts, latency_ms=latency, ttft_ms=None,
        token_input=tokens, token_cached=0, token_output=0,
    )


def call(pid: str, *, ts: float, ok: bool = True, latency: float = 812.0,
         code: str = "") -> dict:
    """构造一行 llm_calls（本插件埋的，含 AstrBot 侧的调用）。"""
    return {"ts": int(ts), "provider_id": pid, "provider_model": "m",
            "ok": 1 if ok else 0, "aborted": 0, "latency_ms": latency,
            "ttft_ms": None, "error_code": code}


def probe(pid: str, *, ts: float, ok: bool = True, trigger: str = "command") -> dict:
    """构造一行探测记录（model_test_results）。"""
    return {"provider_id": pid, "checked_at": int(ts), "ok": ok,
            "latency_ms": 300.0, "ttft_ms": None, "error_code": "",
            "trigger": trigger}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    now = time.time()

    print("[没有 llm_calls 时：回落到核心 provider_stats（老行为）]")
    m = _merge_ledger([live("p1", ts=now - 100, status="error")], [], [])
    eq(m["p1"]["window"]["total"], 1, "次数来自 provider_stats")
    eq(m["p1"]["window"]["fail"], 1, "失败照常计入")
    eq(m["p1"]["last"]["source"], "chat", "最近一次来自 provider_stats")
    eq(m["p1"]["last"]["ok"], False, "失败的那次就是最近一次")

    print("[有 llm_calls 时：次数与最近一次都由它决定]")
    m = _merge_ledger(
        [live("p1", ts=now - 100, status="error", tokens=100)],
        [],
        [call("p1", ts=now - 10, ok=True, latency=812.0)],
    )
    eq(m["p1"]["window"]["total"], 1, "★次数来自 llm_calls（不再与 provider_stats 相加——同一次调用加两遍就是双倍）")
    eq(m["p1"]["window"]["ok"], 1, "成功次数")
    eq(m["p1"]["window"]["fail"], 0, "失败次数（llm_calls 说这次成功了）")
    eq(m["p1"]["last"]["ts"], int(now - 10), "最近一次取 llm_calls 的记录")
    eq(m["p1"]["last"]["latency_ms"], 812.0, "延迟也是本次的（不是核心表里那次旧的）")
    eq(m["p1"]["window"]["tokens"], 100, "★token 仍然来自 provider_stats（llm_calls 不记 token）")

    print("[AstrBot 侧的调用（只进 llm_calls）也能被看到]")
    m = _merge_ledger(
        [live("p1", ts=now - 100)],
        [],
        [call("p2", ts=now - 5, ok=True), call("p2", ts=now - 3, ok=False, code="timeout")],
    )
    check("p2" in m, "后台「提供商测试」的模型出现在台账里")
    eq(m["p2"]["last"]["ok"], False, "最近一次是失败的那次")
    eq(m["p2"]["last"]["error_code"], "timeout", "失败原因跟着记录走")
    eq(m["p2"]["window"]["fail"], 1, "失败次数")
    eq(m["p2"]["window"]["tokens"], 0, "它没有 provider_stats 行 → token 为 0（不编数）")

    print("[探测与 llm_calls 各自独立：最近一次取更新的]")
    m = _merge_ledger(
        [],
        [probe("p3", ts=now - 10, ok=True, trigger="command")],
        [call("p3", ts=now - 100, ok=False, code="timeout")],
    )
    eq(m["p3"]["last"]["source"], "command", "探测更新 → 最近一次是探测（带来源标签）")
    eq(m["p3"]["last"]["ok"], True, "探测成功")
    eq(m["p3"]["window"]["total"], 2, "次数 = 探测 1 + llm_calls 1（两路不同的记录，该加）")
    eq(m["p3"]["window"]["fail"], 1, "llm_calls 里那次失败照算")

    print("[多次调用：延迟分位数来自 llm_calls 的原始行]")
    m = _merge_ledger(
        [],
        [],
        [call("p4", ts=now - i, ok=True, latency=100.0 * i) for i in range(1, 5)],
    )
    eq(m["p4"]["window"]["latency_samples"], 4, "4 个延迟样本")
    eq(m["p4"]["window"]["avg_latency_ms"], 250.0, "平均延迟")
    eq(m["p4"]["window"]["p95_latency_ms"], 400.0, "P95 延迟（最近秩法取最大）")

    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for f in _failures:
            print("  - " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
