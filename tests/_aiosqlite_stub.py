"""aiosqlite 薄壳：让存储层测试在本机（没装 aiosqlite）也能跑真 SQL。

只实现 storage.py 真正用到的那几个方法，**不做任何「方便测试」的语义放宽**：
NULL 就是 NULL，SQL 原样交给标准库 sqlite3 执行。所以它测的是真实表结构与真实列值，
而不是一个被 mock 过的理想世界。

用法（测试文件里）：
    import _aiosqlite_stub
    _aiosqlite_stub.install()
    import storage as S
"""

from __future__ import annotations

import sqlite3
import sys
import types


class _Cursor:
    def __init__(self, cur: sqlite3.Cursor):
        self._cur = cur

    def __await__(self):
        # `await db.execute(...)` 拿到的就是这个游标本身
        return iter([self])

    def __getattr__(self, name):
        return getattr(self._cur, name)

    async def fetchall(self):
        return self._cur.fetchall()

    async def fetchone(self):
        return self._cur.fetchone()


class _Conn:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path)

    # storage.py 是「先进上下文、后设 row_factory」，所以赋值那一刻就要透给 sqlite3，
    # 等到 __aenter__ 再读的话还没设上，取出来的行会是普通 tuple（按下标而不是列名）。
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


def install() -> None:
    mod = types.ModuleType("aiosqlite")
    mod.Row = sqlite3.Row
    mod.connect = lambda path, **kw: _Conn(path)
    sys.modules["aiosqlite"] = mod
