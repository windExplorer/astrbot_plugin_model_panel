"""字体查找顺序与数据目录迁移的运行时自检。

用法：
    python tests/test_font_and_db_paths.py

两块都是「错了不会立刻看得见」的逻辑：

- 字体查找：Docker 用户往 ``data/fonts/`` 丢一个 ttf 就指望它命中，顺序排错或扩展名
  漏一个，表现是卡片静默降级成纯文本，用户只会说「图片没了」，不会想到字体。
- 数据目录迁移：搬错方向的后果是**用户数据消失**。所以这里必须真跑一遍文件操作，
  而不是只读代码。

card_render 只依赖 ``astrbot.api.logger``，桩掉就能真 import；main.py 依赖整个 astrbot
运行时，所以用 AST 把要测的函数原样抠出来 exec（和 test_picker_flow 同一套路）。
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


# ------------------------------------------------------------------ astrbot 桩
class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def _f(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    def __getattr__(self, _name):
        return self._f


def install_astrbot_stub(data_root: str):
    """造一个够用的 astrbot 包：logger + astrbot_path + star_tools。"""
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    path_mod = types.ModuleType("astrbot.core.utils.astrbot_path")
    star_mod = types.ModuleType("astrbot.core.star")
    tools_mod = types.ModuleType("astrbot.core.star.star_tools")

    log = _Log()
    api.logger = log
    path_mod.get_astrbot_data_path = lambda: data_root

    class StarTools:
        raise_dir = False

        @classmethod
        def get_data_dir(cls, plugin_name=None):
            if cls.raise_dir:
                raise RuntimeError("模拟核心助手不可用")
            # 真实的 StarTools.get_data_dir 会顺手把目录建出来，桩也得照做
            p = Path(data_root) / "plugin_data" / str(plugin_name or "unknown")
            p.mkdir(parents=True, exist_ok=True)
            return p

    tools_mod.StarTools = StarTools
    astrbot.api = api
    core.utils = utils
    core.star = star_mod
    utils.astrbot_path = path_mod
    star_mod.star_tools = tools_mod
    for name, mod in {
        "astrbot": astrbot, "astrbot.api": api, "astrbot.core": core,
        "astrbot.core.utils": utils, "astrbot.core.utils.astrbot_path": path_mod,
        "astrbot.core.star": star_mod, "astrbot.core.star.star_tools": tools_mod,
    }.items():
        sys.modules[name] = mod
    return log, StarTools


# ------------------------------------------------------------------ main.py 抠函数
WANT = ("_astrbot_data_root", "_plugin_data_dir", "_legacy_db_paths", "_migrate_legacy_db")


def load_main_ns(plugin_name: str = "astrbot_plugin_model_panel"):
    src = io.open(ROOT / "main.py", encoding="utf-8").read()
    tree = ast.parse(src)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in WANT]
    assert {n.name for n in nodes} == set(WANT), f"main.py 里缺这些函数：{set(WANT) - {n.name for n in nodes}}"
    seg = "\n\n".join(ast.get_source_segment(src, n) or "" for n in nodes)
    ns: dict = {
        "os": os, "shutil": shutil, "Optional": object,
        "logger": sys.modules["astrbot.api"].logger,
        "PLUGIN_NAME": plugin_name,
        "DB_FILE_NAME": "model_panel.db",
        "DEFAULT_DB_PATH": os.path.join("data", "model_panel.db"),
    }
    exec(compile(seg, "<paths>", "exec"), ns)  # noqa: S102
    return ns


# ------------------------------------------------------------------ 用例
def main() -> None:
    tmp = tempfile.mkdtemp(prefix="mp_font_")
    data_root = os.path.join(tmp, "data")
    fonts_dir = os.path.join(data_root, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)

    # 目录里放一堆：字体 / 非字体 / 子目录，排序与过滤都要对
    names = [
        "zz-unknown.ttf", "msyh.ttc", "NotoSansCJK-Regular.ttc",
        "LXGWWenKai-Medium.ttf", "ResourceHanRoundedCN-Medium.woff2",
        "readme.txt", "cover.png", "sub",
    ]
    for n in names:
        p = os.path.join(fonts_dir, n)
        os.makedirs(p, exist_ok=True) if n == "sub" else io.open(p, "wb").write(b"x")

    log, StarTools = install_astrbot_stub(data_root)
    import card_render as CR

    print("[投放目录与排序]")
    dirs = CR._data_font_dirs()
    check(len(dirs) == 2 and dirs[0].endswith(os.path.join("astrbot_plugin_model_panel", "fonts")),
          "插件专属 fonts 排在共享 data/fonts 之前")
    check(fonts_dir == dirs[1], "共享 data/fonts 被认出来了")
    ranked = [os.path.basename(p) for p in CR._fonts_in(fonts_dir)]
    check(ranked == ["LXGWWenKai-Medium.ttf", "ResourceHanRoundedCN-Medium.woff2",
                     "NotoSansCJK-Regular.ttc", "msyh.ttc", "zz-unknown.ttf"],
          f"目录内按名字优先级排序，非字体与子目录被过滤：{ranked}")
    check(CR._fonts_in(os.path.join(fonts_dir, "sub")) == [], "空目录不炸")
    check(CR._fonts_in(os.path.join(tmp, "没有这个目录")) == [], "目录不存在返回空而不是抛错")

    print("[查找顺序]")
    cands = CR._font_candidates("")
    check(cands[0] == os.path.join(fonts_dir, "LXGWWenKai-Medium.ttf"),
          "留空配置时，投放目录里优先级最高的字体排在最前")
    check(any(c.endswith("msyh.ttc") or "Windows" in c or "fonts" in c.lower() for c in cands[1:]),
          "投放目录之后仍有系统字体候选（没投放文件时不至于全空）")
    c2 = CR._font_candidates("/somewhere/my.ttf")
    check(c2[0] == "/somewhere/my.ttf", "配置指定的路径优先级最高")
    check(len(c2) > 1, "指定的路径读不到时仍会继续往下找")

    print("[目录名与 main 一致]")
    msrc = io.open(ROOT / "main.py", encoding="utf-8").read()
    check('PLUGIN_NAME = "astrbot_plugin_model_panel"' in msrc, "main.PLUGIN_NAME 字面量没动")
    check(CR._PLUGIN_DIR_NAME == "astrbot_plugin_model_panel", "card_render 的目录名与之一致")

    print("[数据目录解析]")
    ns = load_main_ns()
    d = ns["_plugin_data_dir"]()
    check(d == os.path.join(data_root, "plugin_data", "astrbot_plugin_model_panel"),
          f"走 StarTools 时拿插件专属持久位：{d}")
    check(os.path.isdir(d), "目录被建出来了（后面要往里写库）")
    StarTools.raise_dir = True
    d2 = ns["_plugin_data_dir"]()
    check(d2 == os.path.join(data_root, "plugin_data", "astrbot_plugin_model_panel"),
          "StarTools 不可用时退回手工拼同一个位置，而不是掉回 CWD 相对路径")
    StarTools.raise_dir = False

    print("[旧库迁移]")
    dst_dir = ns["_plugin_data_dir"]()
    dst = os.path.join(dst_dir, "model_panel.db")
    legacy = os.path.join(data_root, "model_panel.db")
    io.open(legacy, "wb").write(b"SQLITE-DATA")
    io.open(legacy + "-wal", "wb").write(b"WAL")
    moved = ns["_migrate_legacy_db"](dst)
    check(moved == legacy, "认得出 data/ 根下的旧库")
    check(os.path.isfile(dst) and not os.path.exists(legacy), "搬过来了，原地不留副本")
    check(os.path.isfile(dst + "-wal") and not os.path.exists(legacy + "-wal"),
          "WAL 边车一起搬：没 checkpoint 的事务不能丢")
    # 新位置已有库、老位置又冒出一个：必须不覆盖。这条不是上一条的重复 ——
    # 上一条搬完之后老路径已经空了，走不到「目标存在就收手」那个分支。
    keep = io.open(dst, "rb").read()
    io.open(legacy, "wb").write(b"OTHER")
    io.open(legacy + "-wal", "wb").write(b"OTHERWAL")
    check(ns["_migrate_legacy_db"](dst) is None, "目标已存在时不再搬（别把新库盖掉）")
    check(io.open(dst, "rb").read() == keep, "已有新库时旧库绝不覆盖")
    check(io.open(legacy, "rb").read() == b"OTHER", "不搬时旧库原地留着，等用户自己处置")

    print("[路径去重]")
    here = os.getcwd()
    os.chdir(tmp)
    try:
        paths = ns["_legacy_db_paths"](dst)
        check(len(paths) >= 1, "候选里至少有一个历史位置")
        check(all(os.path.normcase(os.path.realpath(p)) != os.path.normcase(os.path.realpath(dst))
                  for p in paths), "候选不含目标自身（否则会把新库搬走）")
        dup = [os.path.normcase(os.path.realpath(p)) for p in paths]
        check(len(dup) == len(set(dup)), "候选去重，同一个文件不被试两遍")
    finally:
        os.chdir(here)

    print("[迁移失败要能看见]")
    bad_dir = os.path.join(tmp, "bad")
    os.makedirs(bad_dir, exist_ok=True)
    bad_src = os.path.join(bad_dir, "model_panel.db")
    io.open(bad_src, "wb").write(b"x")
    ns2 = load_main_ns()
    ns2["_legacy_db_paths"] = lambda d: [bad_src]
    log.lines.clear()
    check(ns2["_migrate_legacy_db"](os.path.join(bad_src, "不可能", "子层", "x.db")) is None,
          "搬不动时返回 None，不抛异常打断启动")
    check(any("迁移旧库失败" in line for line in log.lines), "搬不动会留一条 warning 日志")

    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
    print("\n失败 %d 项" % len(_failures))
    for f in _failures:
        print("  - " + f)
    sys.exit(1 if _failures else 0)
