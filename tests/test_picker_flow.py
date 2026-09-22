"""序号选单（回复数字检测 / 切换默认模型）的运行时自检。

用法：
    python tests/test_picker_flow.py

main.py 依赖整个 astrbot 运行时，本机 import 不了，所以这里用 AST 把选单相关的
函数与 filter 类**原样抠出来**单独 exec —— 测的是磁盘上的真实代码，不是抄一份。
新增要抠的函数时改 WANT 名单即可。

为什么这几个函数值得单独测：选单是「谁回数字就执行一次高危操作」的入口，
出错方向很难看——要么把普通聊天里的「3」吞掉（用户觉得机器人装死），
要么群里别人回个数字把系统默认模型切走。这两种都只在真实对话里暴露，本机必须提前拦住。
"""

from __future__ import annotations

import ast
import io
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
    "_picker_key", "_picker_prune", "_picker_arm", "_picker_peek",
    "_picker_disarm", "_picker_take", "_actor_key",
}
WANT_CLASSES = {"NumberPickerFilter"}


class FakeTime(types.ModuleType):
    """只暴露被抠出来的代码真正用到的 time.time()，时钟由测试推着走。"""

    def __init__(self, start: float = 1_800_000_000.0):
        super().__init__("time")
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


def load_picker_ns():
    """把选单相关定义 exec 进一个干净的命名空间。"""
    src = io.open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    picked = [
        n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in WANT_FUNCS)
        or (isinstance(n, ast.ClassDef) and n.name in WANT_CLASSES)
    ]
    missing = WANT_FUNCS | WANT_CLASSES
    found = {getattr(n, "name", "") for n in picked}
    assert missing == found, f"main.py 里找不到这些定义：{sorted(missing - found)}"
    seg = "\n\n".join(ast.get_source_segment(src, n) or "" for n in picked)

    clock = FakeTime()
    ns: dict = {
        "time": clock,
        "astr_filter": types.SimpleNamespace(CustomFilter=object),
        "AstrMessageEvent": object,
        "_PICKERS": {},
        "_PICKER_TTL_SEC": 180,
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


# ------------------------------------------------------------------ 用例
def main() -> None:
    print("[登记与领取]")
    ns = load_picker_ns()
    arm, peek, take = ns["_picker_arm"], ns["_picker_peek"], ns["_picker_take"]
    disarm = ns["_picker_disarm"]
    pids = ["p_a", "p_b", "p_c"]
    arm("chat_1", "u_1", "probe", pids)
    check(peek("chat_1", "u_1"), "列好选单后本人能命中")
    check(not peek("chat_1", "u_2"), "同会话另一个人不算命中（防群里别人乱切默认模型）")
    check(not peek("chat_2", "u_1"), "另一个会话不算命中")
    check(take("chat_1", "u_1", 1) == ("probe", "p_a"), "序号 1 取到第一项（序号从 1 起）")
    check(take("chat_1", "u_1", 2) is None, "取过一次就作废，同一串数字不能重复执行")

    print("[带竖线的真实 umo]")
    # unified_msg_origin 长这样：G123|M123|aiocqhttp|group_normal，键里本来就有多段
    nsu = load_picker_ns()
    nsu["_picker_arm"]("G1|M9|aiocqhttp|group_normal", "777", "switch", pids)
    check(nsu["_picker_peek"]("G1|M9|aiocqhttp|group_normal", "777"), "多段 umo 能正常命中")
    check(nsu["_picker_take"]("G1|M9|aiocqhttp|group_normal", "777", 2) == ("switch", "p_b"), "多段 umo 能正常领取")
    nsu["_picker_arm"]("G1|M9|aiocqhttp|group_normal", "888", "probe", pids)
    nsu["_picker_arm"]("G2|M9|aiocqhttp|group_normal", "777", "probe", pids)
    check(len(nsu["_PICKERS"]) == 2, "不同人 / 不同会话各占一条，互不覆盖")

    print("[越界与不作废]")
    arm("chat_1", "u_1", "switch", pids)
    check(take("chat_1", "u_1", 0) is None, "序号 0 越界不执行")
    check(peek("chat_1", "u_1"), "越界之后选单仍然挂着，用户可以重输")
    check(take("chat_1", "u_1", 4) is None, "超出项数不执行")
    check(peek("chat_1", "u_1"), "超出项数同样不作废")
    check(take("chat_1", "u_1", 3) == ("switch", "p_c"), "改回正确序号能取到最后一项")
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

    print("[filter 放行条件]")
    Flt = ns["NumberPickerFilter"]
    flt = Flt()
    arm("chat_1", "u_1", "probe", pids)
    e = FakeEvent(sender_id="u_1", umo="chat_1")
    e.message_str = "3"
    check(flt.filter(e, None) is True, "纯数字 + 挂着选单 → 放行")
    e.message_str = " 2 "
    check(flt.filter(e, None) is True, "两侧空白不影响识别")
    e.message_str = "3楼见"
    check(flt.filter(e, None) is False, "夹了汉字的数字不吞（日常聊天照旧走别的插件）")
    e.message_str = ""
    check(flt.filter(e, None) is False, "空消息不放行")
    e.message_str = "12345"
    check(flt.filter(e, None) is False, "四位数以上不认（选单最多几十项）")
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
