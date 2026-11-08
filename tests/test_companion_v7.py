"""陪伴插件 V7 适配的运行时自检。

用法：
    python tests/test_companion_v7.py

为什么要单独一个：陪伴插件 V7（7.x）把「37 个 *_PROVIDER_ID + model_assignment_config
镜像」整体换成了「插件级默认 + 逐任务分层路由」，模型控制台里所有针对 V1 结构的
读写在 V7 下都会静默失效（读不到、写进去对方也不认）。而原有 11 个自检脚本
对陪伴插件配置读写是零覆盖 —— 这类「编译通过、页面能开、但配置一个都没改」的问题
只能靠真跑代码提前拦住。

main.py 依赖整个 astrbot 运行时，本机 import 不了，所以沿用 test_picker_flow.py 的
做法：用 AST 把 V7 相关的方法与常量**原样从 main.py 抠出来** exec 进干净命名空间，
再挂到一个带 stub 的 Host 类上跑。测的是磁盘上的真实代码，不是抄一份。

覆盖：版本探测三级兜底、A 层位置枚举（含 JSON 文本内层的 provider_id）、
一键替换、单键白名单、落盘与重载提示、总览计数，以及 plugin_models.py 的
V7 分支静态守卫（绝不能再用 V1 的 _flat_set 语义写 V7 配置）。
"""

from __future__ import annotations

import ast
import io
import json
import re
import sys
import textwrap
import types
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
PLUGIN_MODELS = ROOT / "plugin_models.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


WANT_CONSTS = {
    "COMPANION_PLUGIN_NAME",
    "COMPANION_PROVIDER_KEYS",
    "COMPANION_PROVIDER_GROUP",
    "COMPANION_V7_MODEL_GROUP",
    "COMPANION_V7_VOICE_GROUP",
    "COMPANION_V7_REPLACEMENT_RULES",
    "COMPANION_V7_ADDITIONAL_PROVIDERS",
    "COMPANION_V7_PROVIDER_SLOTS",
    "COMPANION_V7_SLOT_MAP",
    "COMPANION_V7_MANAGED_AREAS",
}
WANT_METHODS = {
    "_companion_generation",
    "_companion_v7_section",
    "_v7_set_field",
    "_companion_v7_positions",
    "_companion_v7_payload",
    "_companion_v7_replace",
    "_companion_v7_set",
    "_companion_v7_save",
    "_companion_provider_count",
}


def _assigned_names(node: ast.stmt) -> set[str]:
    out: set[str] = set()
    if isinstance(node, ast.Assign):
        out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        out.add(node.target.id)
    return out


def load_v7_ns() -> dict:
    src = io.open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    segments: list[str] = []
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = _assigned_names(node)
            if names & WANT_CONSTS:
                segments.append(ast.get_source_segment(src, node) or "")
                found |= names
        elif isinstance(node, ast.ClassDef):
            src_lines = src.splitlines(keepends=True)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name in WANT_METHODS:
                    # FunctionDef.lineno 指向 def 行，不含装饰器；必须把 @staticmethod
                    # 一起切进来并 dedent，否则抠出来的静态方法会退化成实例方法。
                    start = min([sub.lineno] + [d.lineno for d in sub.decorator_list])
                    segment = textwrap.dedent("".join(src_lines[start - 1 : sub.end_lineno]))
                    segments.append(segment)
                    found.add(sub.name)
    missing = (WANT_CONSTS | WANT_METHODS) - found
    assert not missing, f"main.py 里找不到这些定义：{sorted(missing)}"
    logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        debug=lambda *a, **k: None, error=lambda *a, **k: None,
    )
    ns: dict = {
        "re": re, "json": json, "logger": logger,
        "Any": Any, "Optional": Optional,
    }
    exec(compile("\n\n".join(segments), "<companion_v7>", "exec"), ns)  # noqa: S102
    return ns


NS = load_v7_ns()
BaseHost = type("BaseHost", (), {name: NS[name] for name in WANT_METHODS})


class FakeConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0
        self.fail_save = False

    def save_config(self) -> None:
        if self.fail_save:
            raise RuntimeError("disk full")
        self.saved += 1


class FakeStar:
    def __init__(self, version: str = "", star_cls: Any = None, config: Any = None):
        self.version = version
        self.star_cls = star_cls
        self.config = config
        self.name = "astrbot_plugin_private_companion"
        self.root_dir_name = "astrbot_plugin_private_companion"


class Host(BaseHost):
    def __init__(self, star: Any, model_map: dict | None = None, v1_values: dict | None = None):
        self.star = star
        self.model_map = model_map or {}
        self.v1_values = v1_values or {}

    def _companion_star(self):
        return self.star

    def _companion_config(self):
        return getattr(self.star, "config", None) if self.star else None

    def _companion_version(self) -> str:
        return getattr(self.star, "version", "") if self.star else ""

    def _provider_model_map(self) -> dict:
        return self.model_map

    def _companion_provider_values(self) -> dict:
        return self.v1_values


VOICE_FIELDS = [
    "provider_id", "zh_provider_id", "ja_provider_id", "en_provider_id",
    "private_zh_provider_id", "private_ja_provider_id", "private_en_provider_id",
    "group_zh_provider_id", "group_ja_provider_id", "group_en_provider_id",
]


def make_cfg() -> FakeConfig:
    rules = [
        {"id": "code", "provider_id": "pid-old", "keywords": ["写代码"], "priority": 10},
        {"id": "night", "provider_id": "pid-fb", "time_window": {"start": "22:00"}},
    ]
    return FakeConfig({
        "model": {
            "provider_id": "pid-old",
            "fallback_provider_id": "pid-fb",
            "replacement_rules": json.dumps(rules, ensure_ascii=False),
            "max_output_tokens": 2048,
        },
        "voice_reply": {field: ("tts-1" if field == "provider_id" else "") for field in VOICE_FIELDS},
    })


MODEL_MAP = {
    "pid-old": "vendor/old-model",
    "pid-fb": "vendor/fb-model",
    "pid-new": "vendor/new-model",
    "tts-1": "vendor/tts-model",
}


def test_generation() -> None:
    print("[版本探测]")
    cfg = make_cfg()
    check(Host(FakeStar("7.1.6", config=cfg))._companion_generation() == "v7", "7.x 判为 v7")
    check(Host(FakeStar("v6.2.1", config=cfg))._companion_generation() == "v1", "6.x 判为 v1")
    api = types.SimpleNamespace(api_version="companion.product.v1")
    check(
        Host(FakeStar("", star_cls=types.SimpleNamespace(extension_api=api), config=cfg))._companion_generation() == "v7",
        "无版本号时按公开 extension_api 判为 v7",
    )
    check(
        Host(FakeStar("", star_cls=types.SimpleNamespace(staging=object()), config=cfg))._companion_generation() == "v7",
        "无版本号时按 staging 宿主对象判为 v7",
    )
    legacy_cfg = FakeConfig({"model_assignment_config": {"FAST_RESPONSE_PROVIDER_ID": "pid-old"}})
    check(Host(FakeStar("", config=legacy_cfg))._companion_generation() == "v1", "配置含 model_assignment_config 判为 v1")
    v7_cfg = FakeConfig({"model": {"provider_id": "pid-old"}})
    check(Host(FakeStar("", config=v7_cfg))._companion_generation() == "v7", "配置含 model.provider_id 判为 v7")
    check(Host(FakeStar("", config=FakeConfig()))._companion_generation() == "unknown", "无特征时返回 unknown")
    check(Host(None)._companion_generation() == "missing", "插件未加载返回 missing")


def test_positions() -> None:
    print("[A 层位置枚举]")
    cfg = make_cfg()
    host = Host(FakeStar("7.1.6", config=cfg), MODEL_MAP)
    positions = host._companion_v7_positions(cfg)
    keys = [p["key"] for p in positions]
    check(len(positions) == 14, f"12 个插件级字段 + 2 条替换规则 = 14 处（实际 {len(positions)}）")
    check("model.replacement_rules[0].provider_id" in keys, "JSON 文本内层的 provider_id 也被枚举")
    check("voice_reply.group_en_provider_id" in keys, "voice_reply 的 10 个绑定都被枚举")
    check(sum(1 for p in positions if p["kind"] == "fallback") == 1, "只有 model.fallback_provider_id 标为备用")

    cfg["model"]["provider_id"] = ""
    p = next(x for x in positions if x["key"] == "model.replacement_rules[0].provider_id")
    p["apply"]("pid-new")
    rules = json.loads(cfg["model"]["replacement_rules"])
    check(rules[0]["provider_id"] == "pid-new", "apply 能写回 JSON 文本内层")
    check(rules[0].get("keywords") == ["写代码"] and rules[0].get("priority") == 10, "重写 JSON 不丢其它字段")
    check(rules[1].get("time_window", {}).get("start") == "22:00", "同列表里其它规则保持原样")


def test_replace() -> None:
    print("[一键替换]")
    cfg = make_cfg()
    host = Host(FakeStar("7.1.6", config=cfg), MODEL_MAP)
    res = host._companion_v7_replace(cfg, [
        {"old": "vendor/old-model", "replacement": "pid-new", "kind": "any"},
    ])
    check(res["ok"] is True and res["generation"] == "v7", "替换成功且标明 v7")
    check(res["reload_required"] is True, "返回需要重载陪伴插件")
    check(res["changed_count"] == 2, f"主模型 + 替换规则共 2 处（实际 {res['changed_count']}）")
    check(cfg["model"]["provider_id"] == "pid-new", "插件任务主模型已换")
    rules = json.loads(cfg["model"]["replacement_rules"])
    check(rules[0]["provider_id"] == "pid-new", "替换规则里的 provider_id 一并换掉")
    check(cfg["voice_reply"]["provider_id"] == "tts-1", "未命中的 voice_reply 保持原样")
    check(cfg["model"]["fallback_provider_id"] == "pid-fb", "未命中的备用模型保持原样")
    check(cfg.saved == 1, "整批只 save_config() 一次")

    cfg2 = make_cfg()
    host2 = Host(FakeStar("7.1.6", config=cfg2), MODEL_MAP)
    res2 = host2._companion_v7_replace(cfg2, [
        {"old": "vendor/fb-model", "replacement": "pid-new", "kind": "fallback"},
    ])
    check(res2["changed_count"] == 1, "kind=fallback 只改备用模型位置")
    check(cfg2["model"]["fallback_provider_id"] == "pid-new", "备用模型已换")
    check(cfg2["model"]["provider_id"] == "pid-old", "kind=fallback 不动主模型")

    cfg3 = make_cfg()
    host3 = Host(FakeStar("7.1.6", config=cfg3), MODEL_MAP)
    res3 = host3._companion_v7_replace(cfg3, [
        {"old": "不存在的模型", "replacement": "pid-new", "kind": "any"},
    ])
    check(res3["changed_count"] == 0 and res3["reload_required"] is False, "无命中时不落盘、不提示重载")
    check(cfg3.saved == 0, "无命中不调用 save_config")


def test_set_and_save() -> None:
    print("[单键设置与落盘]")
    cfg = make_cfg()
    host = Host(FakeStar("7.1.6", config=cfg), MODEL_MAP)
    res = host._companion_v7_set(cfg, [
        {"key": "voice_reply.zh_provider_id", "provider_id": "tts-2"},
        {"key": "model.provider_id", "provider_id": ""},
        {"key": "model.replacement_rules[0].provider_id", "provider_id": "hack"},
        {"key": "NOT_A_KEY", "provider_id": "hack"},
    ])
    check(res["changed_count"] == 2, "只接受 A 层 slot key 白名单内的项")
    check(cfg["voice_reply"]["zh_provider_id"] == "tts-2", "写入对应 voice_reply 分组字段")
    check(cfg["model"]["provider_id"] == "", "空 provider_id 表示清除")
    check(json.loads(cfg["model"]["replacement_rules"])[0]["provider_id"] == "pid-old", "JSON 内层不可单键改写")

    empty = make_cfg()
    r = Host(FakeStar("7.1.6", config=empty), MODEL_MAP)._companion_v7_save(empty, [])
    check(r["ok"] is True and r["changed_count"] == 0 and empty.saved == 0, "空改动不落盘")

    broken = make_cfg()
    broken.fail_save = True
    r2 = Host(FakeStar("7.1.6", config=broken), MODEL_MAP)._companion_v7_set(broken, [
        {"key": "model.provider_id", "provider_id": "pid-new"},
    ])
    check(r2["ok"] is False and "disk full" in (r2.get("error") or ""), "save_config 抛错时如实返回失败")


def test_payload_and_count() -> None:
    print("[总览数据与计数]")
    cfg = make_cfg()
    host = Host(FakeStar("7.1.6", config=cfg), MODEL_MAP)
    payload = host._companion_v7_payload(cfg)
    check(payload["generation"] == "v7", "总览标明 v7")
    check(payload["total_keys"] == 12, "总览按 12 个插件级字段计")
    v7 = payload["v7"]
    check(len(v7["slots"]) == 14, "总览列出全部 14 处引用位置")
    by_key = {s["key"]: s for s in v7["slots"]}
    check(by_key["model.provider_id"]["editable"] is True, "插件级字段可单键编辑")
    check(by_key["model.replacement_rules[0].provider_id"]["editable"] is False, "JSON 内层禁用单键编辑")
    check(by_key["model.provider_id"]["model"] == "vendor/old-model", "provider id 翻译成模型名展示")
    check(len(v7["managed_areas"]) >= 4, "列出由陪伴小屋管理的位置")
    check(v7["edit_requires_reload"] is True, "总览标明改配置需重载")
    check(payload["configured_count"] == host._companion_provider_count(), "总览已配置数与总览页计数一致")
    legacy = Host(FakeStar("6.2.1"), v1_values={"A": "pid-old", "B": ""})
    check(legacy._companion_provider_count() == 1, "V1 仍按 37 key 老结构计数")


def test_scanner_guards() -> None:
    print("[扫描器 V7 分支静态守卫]")
    src = PLUGIN_MODELS.read_text(encoding="utf-8")
    check("def _companion_is_v1(self)" in src, "扫描器有代际判定")
    check(
        "if plugin_name != COMPANION_PLUGIN_NAME or not self._companion_is_v1() or not path:" in src,
        "V7 不会走 V1 的 _flat_set 镜像写回",
    )
    check(
        "if not (name == COMPANION_PLUGIN_NAME and not self._companion_is_v1()):" in src,
        "V7 跳过镜像去重，避免同名字段误标 conflict",
    )
    check(
        "if plugin_name == COMPANION_PLUGIN_NAME and self._companion_is_v1():" in src,
        "V7 不做 V1 的运行时反射同步",
    )
    check("and self._companion_is_v1()" in src.split("def _set_json_map_value")[1], "V7 的 JSON 容器按 path 写回")


test_generation()
test_positions()
test_replace()
test_set_and_save()
test_payload_and_count()
test_scanner_guards()

print()
if _failures:
    print(f"问题 {len(_failures)} 项：")
    for f in _failures:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
