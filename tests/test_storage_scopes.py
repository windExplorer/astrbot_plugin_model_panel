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
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


# ------------------------------------------------------------------ aiosqlite 薄壳
class _Cursor:
    def __init__(self, cur: sqlite3.Cursor):
        self._cur = cur

    def __await__(self):
        return iter(lambda: self._cur)  # type: ignore[misc]

    def __iter__(self):
        return iter([self._cur])

    def __getattr__(self, name):
        return getattr(self._cur, name)

    async def fetchall(self):
        return self._cur.fetchall()

    async def fetchone(self):
        return self._cur.fetchone()


class _Conn:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path)

    # storage.py 是「先进上下文、后设 row_factory」，所以必须在赋值那一刻就透给 sqlite3，
    # 不能等到 __aenter__ 才读 —— 那时还没设上，取出来的行就是普通 tuple。
    @property
    def row_factory(self):
        return self._db.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._db.row_factory = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._db.close()
        return False

    async def execute(self, sql, params=()):
        return _Cursor(self._db.execute(sql, params))

    async def executemany(self, sql, seq):
        return _Cursor(self._db.executemany(sql, seq))

    async def executescript(self, sql):
        return _Cursor(self._db.executescript(sql))

    async def commit(self):
        self._db.commit()

    async def close(self):
        self._db.close()


def install_fake_aiosqlite() -> None:
    mod = types.ModuleType("aiosqlite")
    mod.Row = sqlite3.Row
    mod.connect = lambda path, **kw: _Conn(path)
    sys.modules["aiosqlite"] = mod


install_fake_aiosqlite()
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
