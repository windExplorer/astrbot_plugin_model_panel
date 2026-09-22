"""卡片行表 ↔ 选单选项表的自检：序号必须一一对应、分组带序号、按名字排序。

用法：
    python tests/test_card_rows.py

为什么值得单独测：这张卡的主要用法是「看一眼 → 回一个数字」，而卡片是图片、
选单是内存里的选项表 —— 两者一旦错位，用户回的 3 和实际被检测/被切换的模型就不是同一个，
而且**不会报任何错**（图看着好好的）。所以这里对着 main.py 的真代码断言：
卡片第 i 行的序号 == 选项表第 i 项的 index == i + 1。

仍然用 AST 抠代码（main.py 依赖整个 astrbot 运行时，本机 import 不了）。
"""

from __future__ import annotations

import ast
import io
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


# 需要的模块级函数 / 常量，以及 ModelPanelPlugin 上的方法（原样 exec，不抄一份）
WANT_FUNCS = {"_fmt_ms", "_fmt_success", "_fmt_ago", "_name_sort_key", "_error_label",
              "_scope_label"}
WANT_CONSTS = {"_ERROR_LABELS", "_STATE_LABELS"}
WANT_METHODS = {
    "_billing_label", "_row_note", "_row_sub",
    "_sorted_vendor_groups", "_card_row_for", "_grouped_card_rows", "_counts_stats",
}
WANT_CLASS_CONSTS = {"_BILLING_LABELS"}


def _assigned_names(node: ast.stmt) -> set[str]:
    out: set[str] = set()
    if isinstance(node, ast.Assign):
        out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        out.add(node.target.id)
    return out


def _class_of(tree: ast.Module, methods: set[str]) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and methods & {
            n.name for n in node.body if isinstance(n, ast.FunctionDef)
        }:
            return node
    raise AssertionError("找不到含这些方法的类")


def load_ns():
    src = io.open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    consts = [n for n in tree.body if _assigned_names(n) & WANT_CONSTS]
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in WANT_FUNCS]
    cls = _class_of(tree, WANT_METHODS)
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANT_METHODS]
    cls_consts = [n for n in cls.body if _assigned_names(n) & WANT_CLASS_CONSTS]
    missing = (WANT_FUNCS | WANT_CONSTS | WANT_METHODS | WANT_CLASS_CONSTS) - (
        {n.name for n in funcs} | {n.name for n in methods}
        | set().union(*[_assigned_names(n) for n in consts + cls_consts])
    )
    assert not missing, f"main.py 里找不到这些定义：{sorted(missing)}"
    seg = "\n\n".join(
        ast.get_source_segment(src, n) or "" for n in (consts + funcs + cls_consts + methods)
    )
    ns = {"time": __import__("time")}
    exec(compile(seg, "<card_rows>", "exec"), ns)  # noqa: S102

    # 把抠出来的方法挂到一个空壳实例上：它们只用到 self._billing_label 与类常量
    class Fake:
        pass

    for name in WANT_METHODS:
        setattr(Fake, name, ns[name])
    for name in WANT_CLASS_CONSTS:
        setattr(Fake, name, ns[name])
    return ns, Fake()


def item(pid, vendor, model, state="healthy", **kw):
    """造一条与 _health_view 输出同形的最小视图项。"""
    out = {
        "id": pid, "name": vendor, "model": model, "display_model": f"{vendor}/{model}",
        "is_default": False, "state": state, "reason": "", "muted": False,
        "billing": {}, "calls": {}, "window": {}, "last": None, "scope": {}, "cost": {},
    }
    out.update(kw)
    return out


def main() -> None:
    ns, plug = load_ns()
    rows_of = plug._grouped_card_rows

    print("[序号：卡片行与选单选项一一对应]")
    view = {"items": [
        item("p3", "Zhipu", "glm-4"),
        item("p1", "OpenAI", "gpt-4o", is_default=True),
        item("p2", "OpenAI", "o3-mini"),
    ], "days": 1}
    rows, options = rows_of(view, numbered=True)
    check(len(rows) == len(options), "卡片行数与选项数相同（每一行都能被回号）")
    check([r.get("index") for r in rows] == list(range(1, len(rows) + 1)),
          "卡片序号是 1..N 连续无洞")
    check([o.get("index") for o in options] == list(range(1, len(options) + 1)),
          "选项 index 与卡片序号同一份")
    check(all(options[i]["index"] == i + 1 for i in range(len(options))),
          "options[i].index == i + 1（回数字直接按下标取）")
    check(rows[0].get("kind") == "group" and options[0].get("group") is True,
          "第一行是分组行，且组也是一个可选序号")
    check(all(o.get("pids") for o in options), "每个选项都至少带一个 pid（空选项点了会没反应）")

    print("[分组：组名与组内模型的 pid 对齐]")
    group_opts = [o for o in options if o.get("group")]
    check([o["label"] for o in group_opts] == ["OpenAI", "Zhipu"], "分组按供应商名升序（OpenAI 在 Zhipu 前）")
    check(group_opts[0]["pids"] == ["p1", "p2"], "回分组序号 = 组内全部模型（顺序与卡片一致）")
    check(group_opts[1]["pids"] == ["p3"], "第二个分组的 pid 表同样对齐")

    print("[排序：一律按名字]")
    labels = [r.get("label") for r in rows if r.get("kind") != "group"]
    check(labels == ["gpt-4o", "o3-mini", "glm-4"], f"组内按模型名升序（实得 {labels}）")

    print("[默认模型：做成行内胶囊]")
    default_rows = [r for r in rows if r.get("tag") == "默认"]
    check(len(default_rows) == 1 and default_rows[0]["label"] == "gpt-4o", "默认模型带「默认」标签")
    check(default_rows[0].get("highlight") is True, "默认模型那一行高亮")

    print("[明细卡：不画序号]")
    rows_d, _opt_d = rows_of({"items": view["items"]}, numbered=False, detailed=True)
    check(all("index" not in r for r in rows_d), "明细卡的行不带序号（它不需要回号）")
    check(all(len(r.get("cells") or []) == 6 for r in rows_d if r.get("kind") != "group"),
          "明细卡是六列（首字/首字P95/整轮/成功率/失败/更新）")

    print("[数值口径：有逐次埋点就以它为准]")
    it = item("p9", "OpenAI", "gpt-4o", state="degraded",
              calls={"counted": 10, "fail": 2, "fail_rate": 0.2, "avg_latency_ms": 1200.0,
                     "last": {"ok": False, "latency_ms": 900.0}},
              window={"counted": 3, "fail": 0, "fail_rate": 0.0, "avg_latency_ms": 50.0})
    row = plug._card_row_for(it, 1)
    check(row["cells"][:2] == ["900ms", "80.0%"],
          f"延迟取最近一次、成功率取逐次埋点（实得 {row['cells']}）")
    check(len(row["cells"]) == 3 and row["cells"][2] == "-",
          "第三列是「更新于」；这条没有 last.ts 时给 -（不是「刚刚」）")

    print("[更新于：标注这行数字是什么时候的]")
    # 必须拿真实时钟当基准：_card_row_for 内部用 time.time() 算相对时间，
    # 用一个写死的「未来时间戳」会让一切都变成「刚刚」，测试就成了永远通过的摆设
    now = time.time()
    # ① 有逐次埋点：时间必须跟着「逐次埋点的最近一次」走
    it2 = item("p10", "OpenAI", "gpt-4o", calls={"counted": 1, "fail": 0, "fail_rate": 0.0,
                                                 "last": {"ok": True, "latency_ms": 800.0,
                                                          "ts": int(now - 180)}})
    r2 = plug._card_row_for(it2, 1)
    check(r2["cells"][2] == "3 分钟前", f"180 秒前 → 「3 分钟前」（实得 {r2['cells'][2]}）")
    # ② 没有逐次埋点（只读核心表）：退回合并台账的最近一次时间
    it3 = item("p11", "OpenAI", "gpt-4o",
               last={"ok": True, "latency_ms": 700.0, "ts": int(now - 7200)},
               window={"counted": 2, "fail": 0, "fail_rate": 0.0})
    r3 = plug._card_row_for(it3, 1)
    check(r3["cells"][2] == "2 小时前", f"没有埋点时用窗口里最近一次的时间（实得 {r3['cells'][2]}）")
    ago = ns["_fmt_ago"]
    check(ago(int(now - 5), now) == "刚刚", "5 秒前 → 刚刚")
    check(ago(int(now - 7200), now) == "2 小时前", "2 小时前")
    check(ago(int(now - 86400 * 3), now) == "3 天前", "3 天前")
    check(ago(0, now) == "-" and ago(None, now) == "-", "没有时间戳时给 -，不编「刚刚」")
    check("失败 2 次" in row["note"], "副标题里写明失败次数（旧版看不见的那个数）")
    check(row["state"] == "degraded", "状态透传给渲染器（决定色点颜色）")

    print("[计数汇总]")
    stats = plug._counts_stats({"counts": {"healthy": 2, "degraded": 1, "down": 1}})
    check([s["value"] for s in stats] == ["2", "1", "1", "0"], "四个状态各自计数，缺的补 0")

    print("[空视图]")
    rows_e, opts_e = rows_of({"items": []}, numbered=True)
    check(rows_e == [] and opts_e == [], "没有模型时两表都为空（调用方自己发提示，不画空卡）")


if __name__ == "__main__":
    main()
    print("\n失败 %d 项" % len(_failures))
    for f in _failures:
        print("  - " + f)
    sys.exit(1 if _failures else 0)
