"""静态自检：只解析源码文本，不需要 AstrBot 运行环境，也不给外部 API 打桩。

用法：
    python tests/test_static_guards.py

覆盖的都是「编译通过、运行不报错、但功能静默失效」那一类：

1. 指令注册——装饰器被编辑吃掉时方法还在、语法也对，只是 AstrBot 扫不到它。
   用户侧表现成「发了指令 bot 毫无反应」，日志里一个字都没有。
   （v1.3.4 人工枚举指令写 README 时，才发现 /模型静音 从未注册。）
2. astrbot.* 导入——打桩 sys.modules 的测试对「这个名字到底存不存在」是无感的，
   桩全绿不等于真机能导入。（v1.3.1 装不上：MessageChain 并不在
   astrbot.api.message_components 里。）
   这一项要本地有 _Refs/AstrBot 源码，找不到就跳过而非失败。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = ROOT / "main.py"

# 指令清单：方法名 → (指令名, 期望权限)。权限 None 表示不声明 permission_type，即全员。
EXPECTED_COMMANDS = {
    "cmd_companion_affinity": ("陪伴分数", None),
    # v1.3.10 起正名为「模型检测」；旧名「检测模型」必须留在 alias 里，否则老用户突然失灵
    "cmd_model_probe": ("模型检测", "ADMIN"),
    "cmd_model_mute": ("模型静音", "ADMIN"),
    "cmd_model_status": ("模型状态", "ADMIN"),
    "cmd_model_stats": ("模型统计", "ADMIN"),
    "cmd_switch_model": ("切换系统模型", "ADMIN"),
}

# 指令别名：方法名 → 必须出现的别名（改名的指令要留住旧名）
EXPECTED_ALIASES = {
    "cmd_model_probe": "检测模型",
}

# 非 command 的钩子型 handler：靠 custom_filter 匹配，必须带 filter 装饰器否则永不触发
EXPECTED_FILTERED = {
    "on_number_pick": "custom_filter",
}

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


def dec_text(dec: ast.expr) -> str:
    return ast.unparse(dec)


def find_plugin_class(tree: ast.Module) -> ast.ClassDef | None:
    """带 @register 的那个 Star 子类。"""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(dec_text(d).split("(")[0].strip() == "register" for d in node.decorator_list):
            return node
    return None


def check_command_registration(tree: ast.Module) -> None:
    cls = find_plugin_class(tree)
    if cls is None:
        check(False, "找到带 @register 的插件类")
        return
    check(True, f"找到带 @register 的插件类 {cls.name}")

    methods: dict[str, ast.FunctionDef] = {
        n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    registered: dict[str, tuple[str | None, str | None, int, int]] = {}

    for name, meth in methods.items():
        cmd_at = perm_at = -1
        cmd_text = perm_text = None
        for idx, dec in enumerate(meth.decorator_list):
            text = dec_text(dec)
            if text.startswith("astr_filter.command(") or text.startswith("command("):
                cmd_text, cmd_at = text, idx
            if "permission_type(" in text:
                perm_text, perm_at = text, idx
        if cmd_text is None:
            if name.startswith("cmd_"):
                check(False, f"{name} 缺 @command，会静默不注册")
            continue
        registered[name] = (cmd_text, perm_text, cmd_at, perm_at)

    for meth_name, (want_cmd, want_perm) in EXPECTED_COMMANDS.items():
        if meth_name not in registered:
            check(False, f"/{want_cmd} 已注册（{meth_name}）")
            continue
        cmd_text, perm_text, cmd_at, perm_at = registered[meth_name]
        check(want_cmd in cmd_text, f"{meth_name} 指令名为「{want_cmd}」")
        # AstrBot 的 call_handler 两种都支持（协程 / 异步生成器），所以这里只要求 async。
        # v1.3.16 起指令回执改成**直发**（见下面的「不许用 plain_result」），自然就不再是生成器了。
        check(meth_name in methods and isinstance(methods[meth_name], ast.AsyncFunctionDef),
              f"{meth_name} 是 async 函数")
        if want_perm:
            check(perm_text is not None and want_perm in perm_text, f"{meth_name} 权限为 {want_perm}")
            # AstrBot 由外向内套装饰器：permission_type 必须写在 command 之上才生效
            check(perm_at >= 0 and perm_at < cmd_at, f"{meth_name} permission_type 位于 command 之上")
        else:
            check(perm_text is None, f"{meth_name} 未声明 permission_type（全员可用）")

    # 有人新增了 cmd_* 却没登记进清单时，上面的循环不会报任何东西 —— 清单本身也会腐烂。
    unlisted = sorted(set(m for m in methods if m.startswith("cmd_")) - set(EXPECTED_COMMANDS))
    check(not unlisted, f"清单覆盖了所有 cmd_* 方法（未登记：{unlisted}）" if unlisted
          else "清单覆盖了所有 cmd_* 方法")

    # 改名必须留别名：@command 装饰器被改成新名字时，老用户手里的旧指令会静默失效
    for meth_name, want_alias in EXPECTED_ALIASES.items():
        meth = methods.get(meth_name)
        text = ""
        if meth is not None:
            for dec in meth.decorator_list:
                t = dec_text(dec)
                if t.startswith("astr_filter.command(") or t.startswith("command("):
                    text = t
        check(want_alias in text, f"{meth_name} 保留了旧指令名「{want_alias}」作为 alias")

    # custom_filter 型 handler：没有 filter 装饰器就永远不会被触发，
    # 而且症状是「回数字没反应」，和装饰器被吃掉那一类完全一样。
    for meth_name, want_dec in EXPECTED_FILTERED.items():
        meth = methods.get(meth_name)
        if meth is None:
            check(False, f"{meth_name} 存在")
            continue
        decs = [dec_text(d) for d in meth.decorator_list]
        check(any(want_dec + "(" in d for d in decs),
              f"{meth_name} 带 @{want_dec}(...)，否则永不触发")
        check(isinstance(meth, ast.AsyncFunctionDef), f"{meth_name} 是 async 函数")

    # 回执必须**直发**：``yield event.plain_result(...)`` 会进 AstrBot 的结果链，
    # 而 ``ResultDecorateStage`` 会给「只含 Plain / Image 的结果链」最前面插一个
    # ``At(发送者)``（``platform_settings.reply_with_mention``）—— 卡片就是一张 Image，
    # 于是每张卡片都自带一个 @ 用户。这类回归编译过、测试过、只有群里看得见，必须静态拦住。
    bad_reply = [
        f"{n.value.id}.{n.attr}" for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and n.attr in ("plain_result", "chain_result")
        and isinstance(n.value, ast.Name)
    ]
    check(not bad_reply,
          f"回执一律直发（event.send），不用 {'、'.join(sorted(set(bad_reply))) or 'plain_result/chain_result'}"
          if not bad_reply else f"回执一律直发，但出现了 {sorted(set(bad_reply))}（会被 @ 发送者）")

    # 有直发就得有人真的用：全部改成直发后，回执发不出去是「指令毫无反应」的翻版
    check(any(isinstance(n, ast.Attribute) and n.attr in ("_reply", "_reply_chain")
              for cls_node in [find_plugin_class(tree)] if cls_node
              for n in ast.walk(cls_node)),
          "回执走 plugin._reply / _reply_chain（直发封装）")

    # @register 误挂到顶层函数上，会让插件类压根没注册——同样静默
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check(not any(dec_text(d).split("(")[0].strip() == "register" for d in node.decorator_list),
                  f"顶层函数 {node.name} 上没有误挂 @register")


# ------------------------------------------------------------------ 导入检查

def core_source_root() -> Path | None:
    for cand in (ROOT.parent / "_Refs" / "AstrBot", ROOT.parent.parent / "_Refs" / "AstrBot"):
        if (cand / "astrbot" / "__init__.py").exists():
            return cand
    return None


def module_file(src_root: Path, dotted: str, level: int, importer: Path) -> Path | None:
    """把 `import a.b.c` 解析成磁盘上的 .py / __init__.py。level>0 走相对导入。"""
    if level > 0:
        base = importer.parent
        for _ in range(level - 1):
            base = base.parent
        parts = dotted.split(".") if dotted else []
        cand = base.joinpath(*parts)
    else:
        parts = dotted.split(".")
        if parts[0] != "astrbot":
            return None
        cand = src_root.joinpath(*parts)
    if cand.with_suffix(".py").exists():
        return cand.with_suffix(".py")
    if (cand / "__init__.py").exists():
        return cand / "__init__.py"
    return None


def own_names(path: Path) -> set[str]:
    """模块自己对外可见的名字：顶层定义、赋值，以及显式 re-export。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names if a.name != "*")
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def star_names(src_root: Path, path: Path, seen: set[Path]) -> set[str]:
    """跟着 `from x import *` 往下走，把门面模块真正转出的名字收齐。"""
    if path in seen:
        return set()
    seen.add(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not any(a.name == "*" for a in node.names):
            continue
        target = module_file(src_root, node.module or "", node.level, path)
        if target is None:
            continue
        child = own_names(target) | star_names(src_root, target, seen)
        declared = _dunder_all(target)
        out |= (set(declared) if declared else {n for n in child if not n.startswith("_")})
    return out


def _dunder_all(path: Path) -> list[str] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return None


def check_astrbot_imports(src_root: Path, trees: list[ast.Module], files: list[str]) -> None:
    """插件里每一句 `from astrbot.x import y`，y 都必须在核心源码中真实存在。"""
    for tree, fname in zip(trees, files):
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("astrbot"):
                continue
            target = module_file(src_root, node.module, 0, MAIN_PY)
            if target is None:
                check(False, f"{fname}: 核心源码里找不到模块 {node.module}")
                continue
            public = own_names(target) | star_names(src_root, target, set())
            pkg_dir = target.parent if target.name == "__init__.py" else target.parent
            for a in node.names:
                if a.name == "*":
                    continue
                ok = a.name in public or (pkg_dir / a.name).exists() or (pkg_dir / f"{a.name}.py").exists()
                check(ok, f"{fname}: from {node.module} import {a.name}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("[指令注册]")
    main_tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    check_command_registration(main_tree)

    print("[astrbot 导入]")
    src_root = core_source_root()
    if src_root is None:
        print("  skip  未找到 _Refs/AstrBot 源码，跳过导入解析")
    else:
        mods, files = [main_tree], ["main.py"]
        for py in sorted(ROOT.glob("*.py")):
            if py.name == "main.py":
                continue
            try:
                mods.append(ast.parse(py.read_text(encoding="utf-8")))
                files.append(py.name)
            except (OSError, SyntaxError):
                pass
        check_astrbot_imports(src_root, mods, files)

    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for f in _failures:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
