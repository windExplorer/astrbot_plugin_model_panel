"""存储层三通道语义自检：手动 / 定时 / 指令 的 NULL 语义与两处读取必须一致。

用法：
    python tests/test_storage_scopes.py

本机没装 aiosqlite，所以这里注入一个基于标准库 sqlite3 的**薄壳**（只实现
storage.py 真正用到的那几个方法）。壳刻意不做任何「方便测试」的语义放宽：
NULL 就是 NULL，SQL 原样交给 sqlite3 跑，所以它测的是真实表结构与真实列值。

覆盖的正是曾经分歧过的地方：
- ``enabled`` 为 NULL（跟随默认）时，get_detection_preferences 不能读成 False，
  否则一键检测会静默跳过面板上显示「已开启」的模型。
- set_detect_scope 定点改一列，不能顺手把同表另外两列抹回默认。
- set_detection_preferences 批量写手动通道时，不能清空 allow_scheduled / allow_command。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


import _aiosqlite_stub  # noqa: E402

_aiosqlite_stub.install()
import storage as S  # noqa: E402


# ------------------------------------------------------------------ 用例
async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="mp_scope_")
    st = S.Storage(db_path=str(Path(tmp) / "t.db"))
    await st.init()

    # 1) 全新 provider：三通道都没写过 → 手动/指令默认开，定时按计费推（unknown → 关）
    scopes = await st.get_detect_scopes()
    check("p_new" not in scopes, "未配置过的 provider 不在 scopes 表里")

    # 2) 只设定时通道：不能顺手把 manual 写成显式值
    await st.set_detect_scope("p_a", "scheduled", True)
    sc = (await st.get_detect_scopes())["p_a"]
    check(sc["scheduled"] is True and sc["explicit"]["scheduled"] is True, "定时通道已显式开启")
    check(sc["manual"] is True, "只设定时不会把 manual 挤成关闭")
    # manual 复用老表的 enabled 列，它 NOT NULL —— 所以 explicit 恒为真，
    # 「跟随默认」这个三态语义只对 scheduled / command 成立。前端据此决定
    # 只在定时列给「跟随默认」按钮，别在这里断言 manual 能回到未设置态。
    check(sc["explicit"]["manual"] is True, "manual 的 explicit 恒为真（列 NOT NULL，无第三态）")
    prefs = await st.get_detection_preferences()
    check(prefs.get("p_a") is True, "只设定时的 provider 在勾选偏好里也是开的")

    # 3) 手动通道传 None（前端「跟随默认」）→ 归一成开启，且不能抛 IntegrityError
    await st.set_detect_scope("p_a", "manual", False)
    check((await st.get_detection_preferences())["p_a"] is False, "显式关闭后勾选偏好为 False")
    await st.set_detect_scope("p_a", "manual", None)
    sc = (await st.get_detect_scopes())["p_a"]
    prefs = await st.get_detection_preferences()
    check(sc["manual"] is True, "manual 的「跟随默认」落到开启（不炸 NOT NULL）")
    check(prefs["p_a"] is True, "两处读取口径一致（scopes 与勾选偏好同为 True）")

    # 4) 批量写手动通道不能抹掉另外两列
    await st.set_detect_scope("p_b", "command", False)
    await st.set_detect_scope("p_b", "scheduled", True)
    await st.set_detection_preferences({"p_b": True})
    sc = (await st.get_detect_scopes())["p_b"]
    check(sc["manual"] is True, "批量勾选写回后 manual 生效")
    check(sc["command"] is False, "批量写 manual 没有连带清掉 command")
    check(sc["scheduled"] is True, "批量写 manual 没有连带清掉 scheduled")

    # 5) 定时通道的默认值由计费类型推导。
    #    p_c 只有档案行、没有勾选行 —— 正是「只标过计费再没碰过开关」的真实形态，
    #    它必须照样出现在结果里，否则免费模型永远排不进定时探测名单。
    async def scope_of(pid: str) -> dict:
        found = (await st.get_detect_scopes()).get(pid)
        check(found is not None, f"{pid} 出现在检测范围结果里（档案行不该被漏掉）")
        return found or {}

    await st.upsert_profile("p_c", {"billing_type": "free"})
    sc = await scope_of("p_c")
    check(sc.get("scheduled") is True and not sc.get("explicit", {}).get("scheduled"),
          "免费模型默认参与定时巡检")
    await st.upsert_profile("p_c", {"billing_type": "paid"})
    sc = await scope_of("p_c")
    check(sc.get("scheduled") is False, "改成付费后定时巡检自动回到不参与")
    await st.set_detect_scope("p_c", "scheduled", True)
    await st.upsert_profile("p_c", {"billing_type": "unknown"})
    sc = await scope_of("p_c")
    check(sc.get("scheduled") is True and sc.get("explicit", {}).get("scheduled"),
          "显式开过的定时通道不受计费改动影响")

    # 6) 状态机：last_error_code 存的是**错误码**（timeout / auth / unknown…），
    #    而 get_model_states 曾经对所有非 state 列一把梭 int() ——
    #    只要有一个模型进过 down，之后每次读都抛 `int('unknown')`；
    #    偏偏 save_model_state 必须先读再写，于是状态机从此完全写不进去，
    #    面板永远停在最后一次成功的结论上（「刚测出故障、面板还是绿的」的元凶）。
    await st.save_model_state("p_d", {
        "state": "down", "consecutive_fail": 3, "consecutive_ok": 0,
        "last_error_code": "unknown", "last_change_at": 1700000000,
        "muted_until": 0, "samples_total": 3,
    })
    row = (await st.get_model_states()).get("p_d") or {}
    check(row.get("state") == "down", "down 状态写得进去")
    check(row.get("last_error_code") == "unknown", "错误码原样读回（不被 int() 吃掉）")
    check(row.get("consecutive_fail") == 3, "数字列照旧是 int（3 而不是 '3'）")
    check(row.get("last_change_at") == 1700000000, "时间戳原样读回")
    # 再写一次：旧版连这一步都到不了（读的那一步先炸了）
    await st.save_model_state("p_d", {"state": "healthy", "consecutive_ok": 2})
    row = (await st.get_model_states()).get("p_d") or {}
    check(row.get("state") == "healthy", "有错误码的行仍能被后续写入更新")
    check(row.get("last_error_code") == "unknown" and row.get("muted_until") == 0,
          "没写的字段保持原值（整行 upsert 的读-改-写语义）")

    # 7) 分组「今日次数」的取数（v1.4.1）：llm_calls 按天按 provider 聚合。
    #    以前取 llm_usage 的 requests，那张表跳过流式分片 —— 数字与实时监测对不上，
    #    也没有成败可分。这里直接喂几行 llm_calls 验证聚合与过滤断言。
    today = time.strftime("%Y-%m-%d")
    rows = [
        {"ts": time.time(), "provider_id": "p_x", "provider_model": "m",
         "ok": True, "aborted": False, "streamed": True,
         "latency_ms": 800.0, "ttft_ms": 200.0, "error_code": "", "error_message": ""},
        {"ts": time.time(), "provider_id": "p_x", "provider_model": "m",
         "ok": False, "aborted": False, "streamed": False,
         "latency_ms": None, "ttft_ms": None, "error_code": "timeout",
         "error_message": "timed out"},
        {"ts": time.time(), "provider_id": "p_x", "provider_model": "m",
         "ok": False, "aborted": True, "streamed": False,
         "latency_ms": None, "ttft_ms": None, "error_code": "cancelled",
         "error_message": "取消"},
    ]
    for r in rows:
        await st.insert_call(r)
    day = await st.calls_day_by_provider()
    x = day.get("p_x") or {}
    check(x.get("total") == 3, "按天按 provider 的总次数（含流式与失败）")
    check(x.get("ok") == 1, "成功次数")
    check(x.get("fail") == 1, "失败次数（取消不算失败）")
    check(x.get("aborted") == 1, "被取消的单列")
    totals = await st.calls_totals()
    check(totals.get("today_total") == 3, "累计口径里今天的部分")
    check(totals.get("all_total") == 3, "累计总次数")
    check(totals.get("all_fail") == 1, "累计失败次数")
    yest = await st.calls_day_by_provider(day="2000-01-01")
    check("p_x" not in yest, "指定别的日子 → 查不到今天的行（day 过滤生效）")

    await st.close()
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
