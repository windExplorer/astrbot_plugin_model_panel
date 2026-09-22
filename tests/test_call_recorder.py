"""逐次调用埋点自检：真跑异步生成器与异常传播，不打桩包装逻辑本身。

用法：
    python tests/test_call_recorder.py

这里要防的是三类「看起来能用其实坏了」：
- 包装层把异常吞了 —— 对话直接坏掉，比不埋点严重得多；
- 热重载重复 install —— 一次调用记两条，今日次数与成本全部虚高；
- 只包了基类 —— 子类覆写后根本拦不到，静默零记录。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("astrbot", type(sys)("astrbot"))
_api = type(sys)("astrbot.api")


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


_api.logger = _Logger()
sys.modules["astrbot.api"] = _api

import call_recorder as CR  # noqa: E402

_failures: list[str] = []
ROWS: list[dict] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


def eq(actual, expected, label: str) -> None:
    ok = actual == expected
    check(ok, label if ok else f"{label}（期望 {expected!r}，实得 {actual!r}）")


class Boom(Exception):
    pass


class Resp:
    def __init__(self, role="assistant", text="hi"):
        self.role = role
        self.completion_text = text
        self.is_chunk = False


class BaseProvider:
    """基类也定义了 text_chat，用来验证「包基类拦不到子类覆写」这件事。"""

    def __init__(self, pid="p_base"):
        self.provider_config = {"id": pid}

    def get_model(self):
        return "base-model"

    async def text_chat(self, **kw):
        return Resp()

    async def text_chat_stream(self, **kw):
        yield Resp()


class ChatProvider(BaseProvider):
    fail = False
    soft_fail = False
    stream_fail_at = 0

    def get_model(self):
        return "chat-model"

    async def text_chat(self, **kw):
        if self.fail:
            raise Boom("connection refused by peer")
        if self.soft_fail:
            return Resp(role="err", text="All chat models failed")
        await asyncio.sleep(0.01)
        return Resp()

    async def text_chat_stream(self, **kw):
        for i in range(3):
            if self.stream_fail_at and i == self.stream_fail_at:
                raise Boom("stream timeout by upstream")
            yield Resp()
            await asyncio.sleep(0.01)


class ErrStreamProvider(BaseProvider):
    async def text_chat_stream(self, **kw):
        yield Resp()
        yield Resp(role="err", text="rate limit reached")


async def recorder(row: dict) -> None:
    ROWS.append(row)


def classify(exc):
    text = str(exc).lower()
    if "refus" in text:
        return "refused", str(exc)
    if "timeout" in text:
        return "timeout", str(exc)
    if "rate limit" in text:
        return "rate_limit", str(exc)
    return "unknown", str(exc)


def reset() -> None:
    ROWS.clear()


def only() -> dict:
    assert len(ROWS) == 1, f"期望恰好 1 行记录，实得 {len(ROWS)}"
    return ROWS[0]


async def main() -> int:
    CR.install(recorder, classify, [])

    print("[text_chat]")
    p = ChatProvider()
    CR.install(recorder, classify, [p])
    reset()
    await p.text_chat(prompt="x")
    r = only()
    eq((r["ok"], r["aborted"], r["error_code"]), (1, 0, ""), "成功调用记一行且标 ok")
    eq(r["provider_id"], "p_base", "provider_id 取自 provider_config")
    eq(r["provider_model"], "chat-model", "模型名取自 get_model()")
    check(r["latency_ms"] and r["latency_ms"] >= 5, "延迟按真实耗时统计（毫秒）")

    reset()
    p.fail = True
    raised = False
    try:
        await p.text_chat(prompt="x")
    except Boom:
        raised = True
    p.fail = False
    check(raised, "异常必须原样抛出，包装层绝不吞")
    r = only()
    eq((r["ok"], r["error_code"]), (0, "refused"), "失败记 ok=0 并归一化错误码")

    reset()
    p.soft_fail = True
    await p.text_chat(prompt="x")
    p.soft_fail = False
    eq((len(ROWS), only()["ok"], only()["error_code"]), (1, 0, "unknown"),
       "role=err 的软失败也算失败（核心靠它表达「函数返回了但没结果」）")

    print("[text_chat_stream]")
    reset()
    chunks = [c async for c in p.text_chat_stream(prompt="x")]
    eq(len(chunks), 3, "流式分片数量不变")
    r = only()
    eq((r["ok"], r["streamed"]), (1, 1), "流式成功记一行")
    check(r["ttft_ms"] is not None and r["ttft_ms"] <= r["latency_ms"],
          "首字延迟有值且不超过总耗时（本地极快网关会测出 0.0ms，那也是「测到了」不是缺失）")

    reset()
    p.stream_fail_at = 2
    raised = False
    try:
        async for _ in p.text_chat_stream(prompt="x"):
            pass
    except Boom:
        raised = True
    p.stream_fail_at = 0
    check(raised, "流式中途抛异常仍然向上传播")
    r = only()
    eq((r["ok"], r["error_code"], r["streamed"]), (0, "timeout", 1), "流式失败记错误码")
    check(r["ttft_ms"] is not None, "失败前已经出过首块，ttft 要保留而不是被丢掉")

    reset()
    ep = ErrStreamProvider(pid="p_err")
    CR.install(recorder, classify, [ep])
    _ = [c async for c in ep.text_chat_stream(prompt="x")]
    r = only()
    eq((r["ok"], r["error_code"]), (0, "rate_limit"), "流式里出现 role=err 分片算失败")

    print("[提前放弃生成器]")
    reset()
    agen = p.text_chat_stream(prompt="x")
    async for _ in agen:
        break
    await agen.aclose()
    eq(len(ROWS), 1, "消费方提前 aclose 也要落一行，且不能抛错")

    print("[幂等与覆盖范围]")
    reset()
    CR.install(recorder, classify, [p, p, ChatProvider()])
    await p.text_chat(prompt="x")
    eq(len(ROWS), 1, "重复 install 不会套娃记录（一次调用恰好一行）")
    check("ChatProvider" in CR.wrapped_classes(), "记录已包装的类名（子类覆写也能拦到）")
    base_only = BaseProvider()
    reset()
    await base_only.text_chat(prompt="x")
    eq(len(ROWS), 0, "没被实例化过的基类不该被顺带包装成两层")

    print("[探测抑制]")
    reset()
    with CR.suppressed():
        await p.text_chat(prompt="probe")
        _ = [c async for c in p.text_chat_stream(prompt="probe")]
    eq(len(ROWS), 0, "suppressed() 内的调用不记录，避免与探测历史重复计数")
    reset()
    await p.text_chat(prompt="after")
    eq(len(ROWS), 1, "退出 suppressed() 后恢复正常记录")

    print("[记录器自身故障]")
    async def broken(_row):
        raise RuntimeError("db is gone")

    CR.install(broken, classify, [])
    reset()
    ok = True
    try:
        await p.text_chat(prompt="x")
    except Exception:
        ok = False
    check(ok, "存储写失败不能影响对话主流程")
    CR.install(recorder, classify, [])

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
