"""计费内核自检：按次单价、分组倍率、预估花费、老库补列。

用法：
    python tests/test_billing.py

覆盖的都是「算错了不会报错、只会静默给出一个像模像样的错数字」那一类，
所以每条断言都盯具体金额而不是「有没有返回」。

要点：
- token 计费按 AstrBot 的 usage 拆法：input_other 与 input_cached **互斥**，
  缓存部分单独按缓存价计，不能混进 input 重复收费。
- 缓存价没填时退回输入价，而不是当 0（当 0 会系统性低估成本）。
- 单价没填要返回 None，不能返回 0.0 —— 「没填」和「免费」是两件事。
- 倍率优先级：模型覆盖 → 分组 → 1.0。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


def eq(actual, expected, label: str) -> None:
    ok = actual == expected
    check(ok, label if ok else f"{label}（期望 {expected!r}，实得 {actual!r}）")


import _aiosqlite_stub  # noqa: E402

_aiosqlite_stub.install()
import storage as S  # noqa: E402

USAGE = {"input": 1_000_000, "cached": 0, "output": 500_000, "requests": 10}


def cost_tests() -> None:
    """纯函数：estimate_cost。"""
    # 100 万输入 + 50 万输出，输入 2 元/百万、输出 8 元/百万 → 2 + 4 = 6
    eq(S.estimate_cost("paid", {"price_input_per_m": 2, "price_output_per_m": 8}, USAGE),
       6.0, "按量：输入×单价 + 输出×单价")
    # 倍率 1.5 → 9
    eq(S.estimate_cost("paid", {"price_input_per_m": 2, "price_output_per_m": 8}, USAGE, 1.5),
       9.0, "按量：倍率乘在总价上")
    # 缓存互斥：80 万 input + 20 万 cached，缓存价 0.5 → 1.6 + 0.1 + 4
    cached_usage = {"input": 800_000, "cached": 200_000, "output": 500_000, "requests": 3}
    eq(S.estimate_cost("paid", {"price_input_per_m": 2, "price_output_per_m": 8,
                                "price_cached_per_m": 0.5}, cached_usage),
       5.7, "按量：缓存 token 单独按缓存价计，不混进 input")
    # 缓存价没填 → 退回输入价，而不是当 0
    eq(S.estimate_cost("paid", {"price_input_per_m": 2, "price_output_per_m": 8}, cached_usage),
       6.0, "按量：缓存价缺省时按输入价计（不能当免费）")
    # 按次：10 次 × 0.002 × 倍率 2 = 0.04
    eq(S.estimate_cost("per_request", {"price_per_call": 0.002}, USAGE, 2.0),
       0.04, "按次：请求数 × 单次单价 × 倍率")
    eq(S.estimate_cost("per_request", {}, USAGE), None, "按次但没填单价 → None 而不是 0")
    eq(S.estimate_cost("paid", {}, USAGE), None, "按量但一个单价都没填 → None（没填≠免费）")
    eq(S.estimate_cost("free", {"price_input_per_m": 2}, USAGE), 0.0, "免费类恒为 0")
    eq(S.estimate_cost("paid", {"price_input_per_m": 2, "price_output_per_m": 8}, USAGE, 0.0),
       0.0, "倍率 0 是合法值（不计费），不能被当成「没填」退回 1.0")
    eq(S.estimate_cost("paid", {"price_input_per_m": 2, "price_output_per_m": 8}, USAGE, -3),
       6.0, "负倍率按 1.0 处理（基础价 6.0），绝不返回负数金额")
    # unknown 但填了价 → 尊重用户填的价照样估
    eq(S.estimate_cost("unknown", {"price_input_per_m": 2, "price_output_per_m": 8}, USAGE),
       6.0, "未标注计费类型但填了单价时照样估算")


def pricing_tests() -> None:
    """纯函数：resolve_pricing 的三级优先。"""
    r = S.resolve_pricing({"rate_multiplier": 3.0}, {"rate_multiplier": 1.5, "currency": "CNY"})
    eq(r["multiplier"], 3.0, "模型级倍率覆盖优先于分组")
    eq(r["multiplier_from_group"], False, "覆盖时标记为「非跟随分组」")
    r = S.resolve_pricing({"rate_multiplier": None}, {"rate_multiplier": 1.5, "currency": "CNY"})
    eq(r["multiplier"], 1.5, "模型没填倍率时跟随分组")
    eq(r["currency"], "CNY", "模型没填币种时跟随分组币种")
    r = S.resolve_pricing({"rate_multiplier": None, "currency": "USD"}, {"currency": "CNY"})
    eq(r["currency"], "USD", "模型级币种覆盖分组币种")
    r = S.resolve_pricing({"rate_multiplier": None}, None)
    eq(r["multiplier"], 1.0, "模型和分组都没填 → 倍率 1.0")
    eq(r["daily_call_limit"], None, "没有分组档案时每日限额为 None")
    eq(S.resolve_pricing({}, {"daily_call_limit": 500})["daily_call_limit"], 500,
       "每日限额从分组档案取")


async def dao_tests(tmp: str) -> None:
    st = S.Storage(db_path=str(Path(tmp) / "t.db"))
    await st.init()

    check("per_request" in S.BILLING_TYPES, "BILLING_TYPES 含按次计费")
    prof = await st.upsert_profile("p1", {"billing_type": "per_request",
                                          "price_per_call": 0.002, "rate_multiplier": 2.5})
    eq(prof.get("billing_type"), "per_request", "档案可写入按次计费类型")
    eq(prof.get("price_per_call"), 0.002, "按次单价落库")
    eq(prof.get("rate_multiplier"), 2.5, "模型级倍率落库")
    # 非法枚举退回 unknown，不抛异常
    eq((await st.upsert_profile("p1", {"billing_type": "bogus"})).get("billing_type"),
       "unknown", "非法计费类型退回 unknown")
    eq((await st.get_all_profiles())["p1"]["billing_type"], "unknown",
       "非法值确实覆盖了旧值（不是静默忽略）")

    # 分组档案
    v = await st.upsert_vendor_profile("硅基流动", {"rate_multiplier": 1.2,
                                                  "daily_call_limit": 500, "currency": "cny"})
    eq(v.get("rate_multiplier"), 1.2, "分组倍率落库")
    eq(v.get("daily_call_limit"), 500, "分组每日限额落库")
    eq(v.get("currency"), "CNY", "分组币种统一大写")
    v2 = await st.upsert_vendor_profile("硅基流动", {"note": "晚高峰易 429"})
    eq(v2.get("rate_multiplier"), 1.2, "补丁只改 note，不该清掉已有倍率")
    eq(v2.get("note"), "晚高峰易 429", "note 写入成功")
    eq((await st.upsert_vendor_profile("硅基流动", {"daily_call_limit": 0})).get("daily_call_limit"),
       None, "限额填 0 视为「不限」，落成 NULL 而不是 0")
    eq((await st.upsert_vendor_profile("硅基流动", {"rate_multiplier": -1})).get("rate_multiplier"),
       0.0, "负倍率夹到 0，不写负数")
    eq(await st.upsert_vendor_profile("  ", {"note": "x"}), {}, "空分组名不建档")

    # 用量聚合
    await st.record_usage("m1", "p1", input_tokens=1000, cached_tokens=200, output_tokens=300)
    await st.record_usage("m1", "p1", input_tokens=500, cached_tokens=0, output_tokens=200)
    await st.record_usage("m2", "p2", input_tokens=100, cached_tokens=0, output_tokens=100)
    agg = await st.usage_aggregate(days=1)
    eq(agg["p1"]["input"], 1500, "聚合：两次调用的非缓存输入相加")
    eq(agg["p1"]["cached"], 200, "聚合：缓存 token 单独一项")
    eq(agg["p1"]["requests"], 2, "聚合：调用次数按行数计")
    check("p2" in agg and agg["p2"]["requests"] == 1, "聚合：第二个 provider 独立成行")
    await st.close()


async def migration_tests(tmp: str) -> None:
    """老库（没有新列的 model_profile）必须被 _migrate 补上列。"""
    path = str(Path(tmp) / "old.db")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE model_profile (
            provider_id TEXT PRIMARY KEY,
            billing_type TEXT NOT NULL DEFAULT 'unknown',
            free_until INTEGER,
            currency TEXT NOT NULL DEFAULT '',
            price_input_per_m REAL,
            price_output_per_m REAL,
            price_cached_per_m REAL,
            channel_kind TEXT NOT NULL DEFAULT 'unknown',
            role TEXT NOT NULL DEFAULT 'unknown',
            supports_streaming TEXT NOT NULL DEFAULT 'unknown',
            probe_mode TEXT NOT NULL DEFAULT 'non_stream',
            note TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO model_profile (provider_id, billing_type, updated_at)
            VALUES ('p_old', 'paid', 1);
    """)
    db.commit()
    db.close()

    st = S.Storage(db_path=path)
    await st.init()
    cols = {f for f in S.PROFILE_FIELDS}
    prof = (await st.get_all_profiles()).get("p_old") or {}
    check(all(c in prof for c in cols), "老库补列后所有 PROFILE_FIELDS 都读得到")
    eq((await st.upsert_profile("p_old", {"price_per_call": 0.01})).get("price_per_call"),
       0.01, "老库可以直接写新列")
    eq(prof.get("billing_type"), "paid", "迁移不能动已有值")
    await st.close()


def enum_label_tests() -> None:
    """后端枚举 ↔ 前端 i18n 标签必须一一对应。

    加一个计费类型只改后端的话，前端下拉会显示成 `monitor.billing.per_request` 这种裸键，
    而且不会报错 —— 正是那种「构建成功、界面上是坏的」的漏。
    """
    import json
    locales = json.loads((ROOT / "webui-src" / "src" / "locales" / "zh.json").read_text(encoding="utf-8"))
    pairs = [(list(S.BILLING_TYPES), locales["monitor"]["billing"], "monitor.billing"),
             (list(S.ROLES), locales["monitor"]["role"], "monitor.role"),
             (list(S.CHANNEL_KINDS), locales["monitor"]["channel"], "monitor.channel"),
             (list(S.STREAM_FLAGS), locales["monitor"]["stream"], "monitor.stream"),
             (list(S.PROBE_MODES), locales["profile"]["probeMode"], "profile.probeMode")]
    for values, labels, ns in pairs:
        missing = [v for v in values if v not in labels]
        extra = [k for k in labels if k not in values]
        check(not missing, f"{ns}：后端枚举都有中文标签（缺 {missing}）" if missing
              else f"{ns}：后端 {len(values)} 个枚举值都有中文标签")
        check(not extra, f"{ns}：没有多余的孤儿标签（多 {extra}）" if extra
              else f"{ns}：没有多余的孤儿标签")


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="mp_billing_")
    print("[estimate_cost]")
    cost_tests()
    print("[resolve_pricing]")
    pricing_tests()
    print("[枚举与标签]")
    enum_label_tests()
    print("[档案 DAO]")
    await dao_tests(tmp)
    print("[老库迁移]")
    await migration_tests(tmp)
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
    sys.exit(asyncio.run(main()))
