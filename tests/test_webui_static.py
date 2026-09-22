"""前端静态自检：只解析 .vue 源码文本，不需要 node / vue-tsc。

用法：
    python tests/test_webui_static.py

抓的是「vite 构建成功、页面却是空的或满是裸键」那一类，本机装不了 vue-tsc 时尤其需要：

1. 模板里用了 ``<n-xxx>`` 但没 import 对应组件 —— 生产构建只会静默把标签当未知元素渲染，
   页面看起来就是一片空白，控制台一个字都没有（历史上真踩过 NDataTable 漏 import）。
2. ``t("a.b.c")`` 的键在 zh.json 里不存在 —— 界面直接显示 ``models.colModel`` 这种裸路径。
3. ``t("monitor.scope." + c)`` 这类动态拼接的键，按 DYNAMIC 表逐个补齐校验。
4. import 了却没用 —— 通常是删代码时漏删，说明那块改动没通读干净。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webui-src" / "src"
LOCALES = json.loads((SRC / "locales/zh.json").read_text(encoding="utf-8"))

TAG_RE = re.compile(r"<(n-[a-z][a-z0-9-]*)[\s/>]")
T_RE = re.compile(r"""t\(\s*["']([^"']+)["']""")
IMPORT_RE = re.compile(r"import\s*\{([^}]*)\}\s*from", re.S)

# 动态拼接的 i18n 前缀 → 代码里可能取到的全部子键。
# 新增枚举值时必须同步这里，否则漏键只能靠人肉点界面发现。
DYNAMIC = {
    "monitor.scope.": ["manual", "scheduled", "command"],
    "monitor.billing.": ["unknown", "free", "temp_free", "trial", "paid_overage", "paid",
                         "subscription", "per_request"],
    "monitor.role.": ["unknown", "primary", "backup", "fallback", "dedicated", "watch", "retired"],
    "monitor.channel.": ["unknown", "official", "aggregator", "reseller", "self_hosted"],
    "monitor.stream.": ["unknown", "true", "false"],
    "monitor.state.": ["healthy", "degraded", "down", "unknown"],
    "monitor.kind.": ["fail_burst", "recovered", "free_expiring"],
    "profile.probeMode.": ["non_stream", "stream", "both"],
    "detect.result.": ["ok", "fail", "timeout", "connect", "refused", "auth", "rate_limit",
                       "not_found", "server", "unknown", "skipped", "not_found_provider"],
}

fails: list[str] = []


def has_key(dotted: str) -> bool:
    cur: object = LOCALES
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def check_view(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    template = text.split("</template>")[0] if "<template>" in text else ""
    script = text.split("<script")[1] if "<script" in text else ""
    imported: set[str] = set()
    for block in IMPORT_RE.findall(script):
        for tok in block.split(","):
            tok = tok.strip().split(" as ")[0].strip()
            if tok:
                imported.add(tok)

    for tag in sorted(set(TAG_RE.findall(template))):
        camel = "".join(p.capitalize() for p in tag.split("-"))
        if camel not in imported:
            fails.append(f"{path.name}: 模板用了 <{tag}> 但没有 import {camel}")

    for key in sorted(set(T_RE.findall(text))):
        if key.endswith("."):
            continue  # 动态拼接前缀，交给 DYNAMIC 表整体校验
        if "." not in key or not has_key(key):
            fails.append(f"{path.name}: i18n 键不存在 -> {key}")

    # 模板里的 <n-button> 与脚本里的 h(NButton) 都算「用到了」
    stripped = re.sub(r"import\s*\{[^}]*\}\s*from[^;]*;", "", text)
    for name in sorted(imported):
        if not re.match(r"^N[A-Z]", name):
            continue
        kebab = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
        if not (re.search(rf"\b{name}\b", stripped) or re.search(rf"<{kebab}[\s>/]", stripped)):
            fails.append(f"{path.name}: import 了 {name} 但没用到")


for view in sorted((SRC / "views").glob("*.vue")) + [SRC / "App.vue"]:
    check_view(view)

# 6) 手写 hash 链接必须指向真实路由。v1.3.5 合并页面后首页的「前往检测」还指着
#    已删除的 #/detect，点了直接白屏 —— 路由删了但字符串链接不会编译报错。
routes_src = (SRC / "main.ts").read_text(encoding="utf-8")
defined = set(re.findall(r'path:\s*"(/[^"]*)"', routes_src))
for vue in sorted((SRC / "views").glob("*.vue")) + [SRC / "App.vue"]:
    text = vue.read_text(encoding="utf-8")
    for href in re.findall(r'href="#(/[^"]*)"', text):
        if href not in defined:
            fails.append(f"{vue.name}: 链接指向不存在的路由 #{href}（已定义：{sorted(defined)}）")
    for to in re.findall(r'to:\s*"(/[^"]*)"', text):
        if to not in defined:
            fails.append(f"{vue.name}: 导航项指向不存在的路由 {to}")

for prefix, keys in DYNAMIC.items():
    for k in keys:
        if not has_key(prefix + k):
            fails.append(f"动态 i18n 键缺失 -> {prefix}{k}")

# 7) 单价输入框的 precision 必须 ≥ 8。n-input-number 的 precision 会把值**静默四舍五入**：
#    以前写 4，填 0.00025 这类便宜模型的缓存价会被悄悄改成 0.0003 ——
#    界面上毫无提示，花费却已经算错了（用户反馈过）。后端是 REAL 存原值，限制只在前端。
models_src = (SRC / "views/ModelsView.vue").read_text(encoding="utf-8")
m = re.search(r"const PRICE_FIELDS = \[(.*?)\] as const;", models_src, re.S)
if not m:
    fails.append("ModelsView.vue: 找不到 PRICE_FIELDS（单价输入框定义被挪走了？）")
else:
    precisions = [int(x) for x in re.findall(r"precision:\s*(\d+)", m.group(1))]
    if len(precisions) < 4:
        fails.append(f"ModelsView.vue: PRICE_FIELDS 只有 {len(precisions)} 个 precision（应为 4 个）")
    bad = [p for p in precisions if p < 8]
    if bad:
        fails.append(f"ModelsView.vue: 单价 precision 出现 {bad}（必须 ≥ 8，否则小数被静默舍掉）")

print(f"扫描 {len(list((SRC / 'views').glob('*.vue')))} 个视图 + App.vue")
if fails:
    print(f"\n问题 {len(fails)} 项：")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
