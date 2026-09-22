"""序号选单（回复数字检测 / 看明细 / 切换默认模型）的运行时自检。

用法：
    python tests/test_picker_flow.py

main.py 依赖整个 astrbot 运行时，本机 import 不了，所以这里用 AST 把选单相关的
函数、常量与 filter 类**原样抠出来**单独 exec —— 测的是磁盘上的真实代码，不是抄一份。
新增要抠的函数时改 WANT_* 名单即可。

为什么这几个函数值得单独测：选单是「谁回数字就执行一次高危操作」的入口，
出错方向很难看——要么把普通聊天里的「3」吞掉（用户觉得机器人装死），
要么群里别人回个数字把系统默认模型切走。这两种都只在真实对话里暴露，本机必须提前拦住。

v1.3.10 起选单改成「选项表 + 分组 + 多选」：
- 选项表 ``{label, pids, group}``：一个序号既可能指向一个模型、也可能指向一个供应商分组
  （回组号 = 选整组），所以不能再存一串裸 pid。
- 多选 ``1 3 5`` 只在 ``multi=True`` 的选单上生效（模型检测），
  单选类选单收到多个数字要**拒绝且不作废**。
"""

from __future__ import annotations

import ast
import io
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"

# 命令行默认 GBK，中文报告会花屏
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


# ------------------------------------------------------------------ 抠代码
WANT_FUNCS = {
    "_picker_key", "_picker_prune", "_normalize_options", "_picker_arm",
    "_picker_entry", "_picker_peek", "_picker_disarm", "_picker_take_multi",
    "_picker_take", "_flatten_pids", "_parse_index_list", "_actor_key",
}
WANT_CLASSES = {"NumberPickerFilter"}
# 模块级常量也要抠：序号上限与「一串序号」的识别式都必须来自磁盘上的真代码，
# 在测试里另抄一份的话，改了公式这里照样全绿。
WANT_CONSTS = {"_PICKERS", "_PICKER_TTL_SEC", "_MAX_PICKS", "_NUM_LIST_RE", "_INDEX_SPLIT_RE"}


class FakeTime(types.ModuleType):
    """只暴露被抠出来的代码真正用到的 time.time()，时钟由测试推着走。"""

    def __init__(self, start: float = 1_800_000_000.0):
        super().__init__("time")
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


def _assigned_names(node: ast.stmt) -> set[str]:
    out: set[str] = set()
    if isinstance(node, ast.Assign):
        out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        out.add(node.target.id)
    return out


def load_picker_ns():
    """把选单相关定义 exec 进一个干净的命名空间（顺序照文件里的先后）。"""
    src = io.open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    picked = [
        n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in WANT_FUNCS)
        or (isinstance(n, ast.ClassDef) and n.name in WANT_CLASSES)
        or (_assigned_names(n) & WANT_CONSTS and n.__class__.__name__ in ("Assign", "AnnAssign"))
    ]
    missing = WANT_FUNCS | WANT_CLASSES | WANT_CONSTS
    found: set[str] = set()
    for n in picked:
        found.add(getattr(n, "name", ""))
        found |= _assigned_names(n)
    assert missing <= found, f"main.py 里找不到这些定义：{sorted(missing - found)}"
    seg = "\n\n".join(ast.get_source_segment(src, n) or "" for n in picked)

    clock = FakeTime()
    ns: dict = {
        "time": clock,
        "re": re,
        "astr_filter": types.SimpleNamespace(CustomFilter=object),
        "AstrMessageEvent": object,
    }
    exec(compile(seg, "<picker>", "exec"), ns)  # noqa: S102
    ns["clock"] = clock
    return ns


class FakeEvent:
    """够用的事件替身：get_sender_id 复刻核心的坑——user_id 是 int 时返回空串。"""

    def __init__(self, sender_id="", user_id=None, umo="Group_1|aiocqhttp|group_private", sender=None):
        self._sender_id = sender_id
        self._user_id = user_id
        self.unified_msg_origin = umo
        self.message_str = ""
        self.message_obj = types.SimpleNamespace(sender=sender if sender is not None else {"user_id": user_id})

    def get_sender_id(self) -> str:
        return self._sender_id


def pids_of(option: dict) -> list:
    return list(option.get("pids") or [])


# ------------------------------------------------------------------ 用例
def main() -> None:
    print("[登记与领取]")
    ns = load_picker_ns()
    arm, peek, take = ns["_picker_arm"], ns["_picker_peek"], ns["_picker_take"]
    disarm, take_multi = ns["_picker_disarm"], ns["_picker_take_multi"]
    pids = ["p_a", "p_b", "p_c"]
    arm("chat_1", "u_1", "probe", pids)  # 允许直接传裸 pid 表（老调用点）
    check(peek("chat_1", "u_1"), "列好选单后本人能命中")
    check(not peek("chat_1", "u_2"), "同会话另一个人不算命中（防群里别人乱切默认模型）")
    check(not peek("chat_2", "u_1"), "另一个会话不算命中")
    got = take("chat_1", "u_1", 1)
    check(got and got[0] == "probe" and pids_of(got[1]) == ["p_a"], "序号 1 取到第一项（序号从 1 起）")
    check(take("chat_1", "u_1", 2) is None, "取过一次就作废，同一串数字不能重复执行")

    print("[选项表：分组与多选]")
    options = [
        {"index": 1, "label": "OpenAI", "pids": ["p_a", "p_b"], "group": True},
        {"index": 2, "label": "gpt-4o", "pids": ["p_a"]},
        {"index": 3, "label": "o3", "pids": ["p_b"]},
    ]
    arm("chat_1", "u_1", "probe", options, multi=True)
    got = take_multi("chat_1", "u_1", [2, 3])
    check(got and [o["label"] for o in got[1]] == ["gpt-4o", "o3"], "多选按用户给的顺序返回")
    check(ns["_flatten_pids"](got[1]) == ["p_a", "p_b"], "多选摊平成去重、保序的 pid 表")
    arm("chat_1", "u_1", "probe", options, multi=True)
    got = take_multi("chat_1", "u_1", [1])
    check(got and pids_of(got[1][0]) == ["p_a", "p_b"], "回分组序号 = 选中组内全部模型")
    arm("chat_1", "u_1", "probe", options, multi=True)
    got = take_multi("chat_1", "u_1", [3, 3, 2])
    check(got and len(got[1]) == 2, "同一个序号重复回不会被执行两次")
    arm("chat_1", "u_1", "probe", options, multi=True)
    check(take_multi("chat_1", "u_1", [2, 9]) is None, "只要有一个越界就整体不执行")
    check(peek("chat_1", "u_1"), "越界之后选单仍然挂着（用户可以重输）")

    print("[同一个序号空间：卡片序号 = 选项下标]")
    arm("chat_1", "u_1", "probe", options, multi=True)
    got = take_multi("chat_1", "u_1", [1, 2, 3])
    check(got and [o["index"] for o in got[1]] == [1, 2, 3], "选项的 index 与回的数字一一对应")

    print("[按号查而不是按下标]")
    # 查询按选项自带的 index 走：将来若有人拿裁剪过的选项表挂选单（下标 ≠ 号），不会被静默错位
    arm("chat_1", "u_1", "probe", [{"index": 5, "label": "只有第 5 号", "pids": ["p_x"]}])
    check(take("chat_1", "u_1", 5) is not None, "选项 index=5 时回 5 能拿到")
    arm("chat_1", "u_1", "probe", [{"index": 5, "label": "只有第 5 号", "pids": ["p_x"]}])
    check(take("chat_1", "u_1", 1) is None, "回 1 拿不到（那个号不存在），且不作废")
    check(peek("chat_1", "u_1"), "拿不到之后选单仍挂着")

    print("[带竖线的真实 umo]")
    # unified_msg_origin 长这样：G123|M123|aiocqhttp|group_normal，键里本来就有多段
    nsu = load_picker_ns()
    nsu["_picker_arm"]("G1|M9|aiocqhttp|group_normal", "777", "switch", pids)
    check(nsu["_picker_peek"]("G1|M9|aiocqhttp|group_normal", "777"), "多段 umo 能正常命中")
    check(pids_of(nsu["_picker_take"]("G1|M9|aiocqhttp|group_normal", "777", 2)[1]) == ["p_b"],
          "多段 umo 能正常领取")
    nsu["_picker_arm"]("G1|M9|aiocqhttp|group_normal", "888", "probe", pids)
    nsu["_picker_arm"]("G2|M9|aiocqhttp|group_normal", "777", "probe", pids)
    check(len(nsu["_PICKERS"]) == 2, "不同人 / 不同会话各占一条，互不覆盖")

    print("[越界与不作废]")
    arm("chat_1", "u_1", "switch", pids)
    check(take("chat_1", "u_1", 0) is None, "序号 0 越界不执行")
    check(peek("chat_1", "u_1"), "越界之后选单仍然挂着，用户可以重输")
    check(take("chat_1", "u_1", 4) is None, "超出项数不执行")
    check(peek("chat_1", "u_1"), "超出项数同样不作废")
    check(pids_of(take("chat_1", "u_1", 3)[1]) == ["p_c"], "改回正确序号能取到最后一项")
    check(not peek("chat_1", "u_1"), "正确领取后关闭")

    print("[过期]")
    ns2 = load_picker_ns()
    arm2, peek2, take2, prune2 = ns2["_picker_arm"], ns2["_picker_peek"], ns2["_picker_take"], ns2["_picker_prune"]
    clock2 = ns2["clock"]
    arm2("chat_1", "u_1", "probe", pids)
    clock2.advance(179)
    check(peek2("chat_1", "u_1"), "TTL 内仍然有效")
    clock2.advance(2)
    check(not peek2("chat_1", "u_1"), "超过 180 秒失效")
    check(take2("chat_1", "u_1", 1) is None, "过期后领取拿不到东西")
    check("chat_1|u_1" not in ns2["_PICKERS"], "过期领取会把条目清掉，不留残单")
    arm2("chat_x", "u_1", "probe", pids)
    clock2.advance(500)
    arm2("chat_y", "u_1", "probe", pids)
    check("chat_x|u_1" not in ns2["_PICKERS"], "登记新选单时顺手清掉已过期的（字典不会只增不减）")
    prune2()
    check(len(ns2["_PICKERS"]) == 1, "prune 后只剩未过期的那一条")
    ns2["_picker_disarm"]("chat_y", "u_1")
    check(not ns2["_PICKERS"], "disarm 能显式清空")

    print("[序号解析]")
    parse = ns["_parse_index_list"]
    check(parse("3") == [3], "单个数字")
    check(parse(" 2 ") == [2], "两侧空白不影响")
    check(parse("1 3 5") == [1, 3, 5], "空格分隔多选")
    check(parse("1,3") == [1, 3], "英文逗号分隔")
    check(parse("1、3") == [1, 3], "顿号分隔")
    check(parse("3 3") == [3], "重复序号去重")
    check(parse("3楼见") == [], "夹了汉字不是序号")
    check(parse("12345") == [], "四位数以上不认（选单最多几十项）")
    check(parse("") == [], "空消息不是序号")
    check(parse("1 2 3 4 5 6 7 8 9") == [], f"超过 {ns['_MAX_PICKS']} 个序号不认（防御性上限）")

    print("[filter 放行条件]")
    Flt = ns["NumberPickerFilter"]
    flt = Flt()
    arm("chat_1", "u_1", "probe", pids, multi=True)
    e = FakeEvent(sender_id="u_1", umo="chat_1")
    e.message_str = "3"
    check(flt.filter(e, None) is True, "纯数字 + 挂着选单 → 放行")
    e.message_str = " 2 "
    check(flt.filter(e, None) is True, "两侧空白不影响识别")
    e.message_str = "1 3"
    check(flt.filter(e, None) is True, "多选写法也放行（模型检测要能一次点几个）")
    e.message_str = "1,3"
    check(flt.filter(e, None) is True, "逗号分隔的多选同样放行")
    e.message_str = "3楼见"
    check(flt.filter(e, None) is False, "夹了汉字的数字不吞（日常聊天照旧走别的插件）")
    e.message_str = ""
    check(flt.filter(e, None) is False, "空消息不放行")
    e.message_str = "12345"
    check(flt.filter(e, None) is False, "四位数以上不认（选单最多几十项）")
    e.message_str = "1 2 3 4 5 6 7 8 9"
    check(flt.filter(e, None) is False, "序号个数超上限不放行（不吞用户的购物清单）")
    disarm("chat_1", "u_1")
    e.message_str = "3"
    check(flt.filter(e, None) is False, "没挂选单时纯数字绝不放行——否则机器人会吃掉用户日常发的「3」")

    print("[谁发的消息]")
    actor = ns["_actor_key"]
    check(actor(FakeEvent(sender_id="u_ok")) == "u_ok", "get_sender_id 正常时用它的返回值")
    # 核心 user_id 为 int 时 get_sender_id 直接返回空串，这里必须兜底取原始字段
    check(actor(FakeEvent(sender_id="", user_id=12345)) == "12345", "sender_id 为空时回落到原始 user_id")
    check(actor(FakeEvent(sender_id="", sender={"uid": "abc"})) == "abc", "dict 形态的 sender 也认 uid")
    check(actor(FakeEvent(sender_id="", user_id=None, sender=None, umo="chat_z")) == "chat_z", "全拿不到时退回会话，至少不抛错")
    obj = types.SimpleNamespace(user_id=999)
    check(actor(FakeEvent(sender_id="", user_id=None, sender=obj, umo="chat_z")) == "999", "对象形态的 sender 取属性")


if __name__ == "__main__":
    main()
    print("\n失败 %d 项" % len(_failures))
    for f in _failures:
        print("  - " + f)
    sys.exit(1 if _failures else 0)
