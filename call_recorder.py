"""逐次 LLM 调用埋点：补齐核心 provider_stats 看不到的那一半数据。

为什么必须自己埋点（这是本模块存在的全部理由）
------------------------------------------------
核心只在**一轮 agent 对话结束后**写一行 ``provider_stats``，而且：

1. ``tool_loop_agent_runner`` 会按「主模型 → 备用模型」逐个试，失败时只打一行
   ``Chat Model X request error:`` 的 **warning** 就换下一个，
   并且 ``self.provider = candidate`` 把当前 provider 改掉；
2. 一轮结束后 ``_record_internal_agent_stats`` 只写**一行**，
   ``provider_id`` 归属于**最后那个跑成功的**候选。

结果就是：**被备用模型救回来的失败，在 provider_stats 里没有任何记录**。
用户因此会在日志里看到某个模型今天失败十几次，而监测面板说它 100% 成功。
另外异常直接抛出到 ``except Exception`` 那条路（日志里是
``Error occurred while processing agent``）连那一行都不会写。

本模块在 provider 实例的 ``text_chat`` / ``text_chat_stream`` 上包一层，
**每次尝试**记一行，失败也记，被换掉的也记 —— 这才是「这个模型今天到底怎么样」。

工程约束
--------
- **包在类上而不是实例上**：``OpenAICompatibleProvider`` 覆写了基类的
  ``text_chat``，只包 ``LanguageModel.text_chat`` 是拦不到的。所以从活着的 provider
  实例反推出具体类，逐类包一次；同类的新实例自动被覆盖。
- **幂等**：热重载会重复调用 ``install()``，靠函数对象上的标记位防止套娃
  （包两层就会记两条）。
- **绝不吞异常**：记完之后原样 ``raise``。埋点自身出错也不能影响对话，
  所以记录动作全部包在 try/except 里。
- **自己的探测要排除**：探测直接调 ``text_chat_stream``，会和本模块重复计数，
  所以用 ContextVar 在那个调用点关掉记录（见 ``suppress()``）。
- **不记 token**：token 用量已有两处（核心表与 llm_usage），这里只管成败与延迟。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import time
from typing import Any, Awaitable, Callable, Iterable, Optional

from astrbot.api import logger

# 记录回调：await record(dict)。由 main.py 注入，避免这里依赖存储层。
Recorder = Callable[[dict], Awaitable[None]]
# 错误归一化回调：classify(exc_or_text) -> (error_code, error_message)
Classifier = Callable[[Any], tuple[str, str]]

_FLAG = "_model_panel_wrapped"
_suppress: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "model_panel_suppress_calls", default=False
)
_recorder: Optional[Recorder] = None
_classifier: Optional[Classifier] = None
_wrapped: dict[int, str] = {}   # id(cls) -> 类名，用于面板自证「装上了没有」

# 我们关心的两个入口。名字写死成字面量，不做配置项 —— 换核心方法名属于版本适配，
# 应该改这里而不是加一个用户看不懂的配置。
_TARGETS = ("text_chat", "text_chat_stream")


@contextlib.contextmanager
def suppressed() -> Any:
    """在本次调用的动态作用域内不记录。

    插件自己的探测直接调 ``text_chat_stream``，那些结果已经写进
    ``model_test_results`` 了；不在这里关掉的话会被本模块再记一次，
    「今日调用次数」和成本都会虚高。
    """
    token = _suppress.set(True)
    try:
        yield
    finally:
        _suppress.reset(token)


def provider_identity(provider: Any) -> tuple[str, str]:
    """(provider_id, model)。取法与面板展示一致，拿不到时给空串而不是抛。"""
    cfg = getattr(provider, "provider_config", None)
    cfg = cfg if isinstance(cfg, dict) else {}
    pid = str(cfg.get("id") or "")
    model = ""
    try:
        model = str(provider.get_model() or "")
    except Exception:
        try:
            model = str(getattr(provider.meta(), "model", "") or "")
        except Exception:
            model = ""
    if not pid:
        try:
            pid = str(provider.meta().id or "")
        except Exception:
            pid = ""
    return pid, model


async def _emit(row: dict) -> None:
    """把一行记录交出去。任何失败都只打日志，绝不影响调用方。"""
    if _recorder is None:
        return
    try:
        await _recorder(row)
    except Exception as e:  # pragma: no cover - 记录失败不该冒泡
        logger.debug(f"[ModelPanel] 写入逐次调用记录失败: {e}")


def _classify(exc: Any) -> tuple[str, str]:
    if _classifier is not None:
        try:
            code, msg = _classifier(exc)
            return str(code or "unknown"), str(msg or "")[:200]
        except Exception:
            pass
    return "unknown", str(exc)[:200]


def _base_row(provider: Any, streamed: bool, started: float) -> dict:
    pid, model = provider_identity(provider)
    now = time.time()
    return {
        "ts": int(now),
        "provider_id": pid,
        "provider_model": model,
        "streamed": 1 if streamed else 0,
        "latency_ms": round((now - started) * 1000.0, 1),
        "ttft_ms": None,
        "ok": 0,
        "aborted": 0,
        "error_code": "",
        "error_message": "",
    }


async def _record_failure(row: dict, exc: BaseException | str) -> None:
    if isinstance(exc, asyncio.CancelledError):
        row["aborted"] = 1
        row["error_code"] = "cancelled"
        row["error_message"] = "调用被取消"
        await _emit(row)
        return
    code, msg = _classify(exc)
    row["error_code"] = code
    row["error_message"] = msg
    await _emit(row)


def _wrap_text_chat(orig: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(orig)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if _suppress.get():
            return await orig(self, *args, **kwargs)
        started = time.time()
        try:
            resp = await orig(self, *args, **kwargs)
        except asyncio.CancelledError as exc:
            await _record_failure(_base_row(self, False, started), exc)
            raise
        except Exception as exc:
            await _record_failure(_base_row(self, False, started), exc)
            raise
        row = _base_row(self, False, started)
        # 核心用 role="err" 表示「软失败」：函数正常返回但模型没给出结果
        if getattr(resp, "role", None) == "err":
            await _record_failure(row, getattr(resp, "completion_text", None) or "role=err")
        else:
            row["ok"] = 1
            await _emit(row)
        return resp

    setattr(wrapper, _FLAG, True)
    return wrapper


def _wrap_text_chat_stream(orig: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if _suppress.get():
            return orig(self, *args, **kwargs)
        return _instrumented_stream(orig, self, args, kwargs)

    functools.update_wrapper(wrapper, orig)
    setattr(wrapper, _FLAG, True)
    return wrapper


async def _instrumented_stream(orig: Callable[..., Any], self: Any,
                               args: tuple, kwargs: dict) -> Any:
    """包一层异步生成器：首块到达算 ttft，抛异常或 role=err 算失败。

    注意 ``finally`` 里 await 是可以的（不再 yield 就没问题），但要保证记录本身
    不会盖掉原始异常 —— 所以 _emit 内部已经吞掉所有异常。
    """
    started = time.time()
    ttft: Optional[float] = None
    err: BaseException | str | None = None
    soft_err: Any = None
    try:
        raw = orig(self, *args, **kwargs)
        # 有的实现是协程返回异步迭代器，而不是异步生成器函数；两种都接下来
        if asyncio.iscoroutine(raw):
            raw = await raw
        async for chunk in raw:
            if ttft is None:
                ttft = time.time() - started
            if getattr(chunk, "role", None) == "err" and soft_err is None:
                soft_err = getattr(chunk, "completion_text", None) or "role=err"
            yield chunk
    except asyncio.CancelledError as exc:
        err = exc
        raise
    except Exception as exc:
        err = exc
        raise
    finally:
        row = _base_row(self, True, started)
        # 判 None 而不是判真值：ttft 可能是 0.0（本地/极快网关第一块瞬间就出），
        # 用 `if ttft` 会把它当成「没测到」丢掉，于是首字延迟整列变空。
        row["ttft_ms"] = round(ttft * 1000.0, 1) if ttft is not None else None
        if err is not None:
            await _record_failure(row, err)
        elif soft_err is not None:
            await _record_failure(row, soft_err)
        else:
            row["ok"] = 1
            await _emit(row)


def wrap_class(cls: type) -> list[str]:
    """给一个 provider 类打补丁。返回本次新包的方法名（已包过的返回空）。"""
    done: list[str] = []
    for name in _TARGETS:
        fn = cls.__dict__.get(name)      # 只看本类自己的实现，不顺着 MRO 拿到基类的
        if fn is None or getattr(fn, _FLAG, False):
            continue
        if name == "text_chat_stream":
            setattr(cls, name, _wrap_text_chat_stream(fn))
        else:
            setattr(cls, name, _wrap_text_chat(fn))
        done.append(name)
    return done


def install(recorder: Recorder, classify: Classifier, providers: Iterable[Any]) -> int:
    """确保 providers 涉及的所有类都已包装。幂等，可反复调用。返回新覆盖的类数。"""
    global _recorder, _classifier
    _recorder = recorder
    _classifier = classify
    fresh = 0
    for p in providers:
        cls = type(p)
        if id(cls) in _wrapped:
            continue
        _wrapped[id(cls)] = cls.__name__
        got = wrap_class(cls)
        if got:
            fresh += 1
            logger.info(f"[ModelPanel] 已为 {cls.__name__} 挂上逐次调用记录：{'、'.join(got)}")
    return fresh


def wrapped_classes() -> list[str]:
    """已尝试包装过的 provider 类名。用来在面板上自证埋点确实装上了。"""
    return sorted(set(_wrapped.values()))
