from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter as astr_filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.provider.entities import LLMResponse, ProviderType
from quart import Response, request

from .card_render import DEFAULT_THEME, THEME_CHOICES, TONE_THEMES, render_card
from .call_recorder import install as install_call_recorder, suppressed as suppress_call_recording, wrapped_classes as recorder_wrapped_classes
from .monitor import (
    KIND_FAIL,
    KIND_FREE_EXPIRING,
    KIND_RECOVER,
    MonitorConfig,
    evaluate as evaluate_monitor,
    free_expiry_decisions,
    redact,
)
from .stats_reader import CallRecord, LiveStatsReader, percentile
from .storage import (
    BILLING_TYPES,
    CHANNEL_KINDS,
    DETECT_CHANNELS,
    PROFILE_FIELDS,
    PROBE_MODES,
    ROLES,
    SCHEDULED_DEFAULT_ON,
    STREAM_FLAGS,
    Storage,
    VENDOR_FIELDS,
    estimate_cost,
    resolve_pricing,
)
from .session_manager import SessionManager
from .plugin_models import PluginModelScanner

COMPANION_PLUGIN_NAME = "astrbot_plugin_private_companion"

# 插件页面 API 路由前缀：/<plugin_name>/<endpoint>。
# 对齐 AstrBot 官方《插件 Pages》约定：后端注册带插件名前缀、不带 /page；
# 前端 bridge 写相对路径，由 Dashboard 自动转发。
PLUGIN_NAME = "astrbot_plugin_model_panel"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}"

# 默认数据库路径：AstrBot 插件数据目录 / model_panel.db
DEFAULT_DB_PATH = os.path.join("data", "model_panel.db")

# 伴侣插件精准模型配置的 provider 字段（与伴侣插件 _allowed_provider_keys 一致）
COMPANION_PROVIDER_KEYS = [
    "FAST_RESPONSE_PROVIDER_ID",
    "COMPLEX_REASONING_PROVIDER_ID",
    "CREATIVE_MODEL_PROVIDER_ID",
    "LLM_PROVIDER_ID",
    "MAI_STYLE_PROVIDER_ID",
    "DAILY_PLAN_PROVIDER_ID",
    "DETAIL_ENHANCEMENT_PROVIDER_ID",
    "DREAM_DIARY_PROVIDER_ID",
    "CREATIVE_PROVIDER_ID",
    "CREATIVE_OUTLINE_PROVIDER_ID",
    "CREATIVE_REVIEW_PROVIDER_ID",
    "VOICE_PROMPT_PROVIDER_ID",
    "tts_conversion_provider_id",
    "PHOTO_PROMPT_PROVIDER_ID",
    "NARRATION_PROVIDER_ID",
    "HISTORY_SUMMARY_PROVIDER_ID",
    "RESPONSE_REVIEW_PROVIDER_ID",
    "SMART_SILENCE_PROVIDER_ID",
    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
    "TROUBLESHOOTING_PROVIDER_ID",
    "DAILY_REVIEW_PROVIDER_ID",
    "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
    "REST_WAKEUP_PROVIDER_ID",
    "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
    "COMPANION_MEMORY_PROVIDER_ID",
    "DIALOGUE_EPISODE_PROVIDER_ID",
    "GROUP_INTERJECT_PROVIDER_ID",
    "GROUP_EPISODE_PROVIDER_ID",
    "GROUP_SLANG_PROVIDER_ID",
    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
    "FORWARD_MESSAGE_PROVIDER_ID",
    "PLUGIN_VISION_PROVIDER_ID",
    "PRIVATE_READING_VISION_PROVIDER_ID",
    "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID",
    "NEWS_PROVIDER_ID",
    "WEB_EXPLORATION_PROVIDER_ID",
    "EMOTION_JUDGEMENT_PROVIDER_ID",
]

# 陪伴插件 constants.MODEL_PROVIDER_KEYS 之外的 key 也要保留：
# 写回 model_fallback_overrides 时不能因为"不认识"就把用户已有配置抹掉。

# 伴侣插件 provider key 的中文标签（在伴侣插件 command_handlers.py 中整理）。
# 这里只内置我们关心的部分；找不到时前端显示 key 原名。
COMPANION_KEY_LABELS: dict[str, str] = {
    "FAST_RESPONSE_PROVIDER_ID": "快速响应模型",
    "COMPLEX_REASONING_PROVIDER_ID": "复杂推理模型",
    "CREATIVE_MODEL_PROVIDER_ID": "创作模型",
    "LLM_PROVIDER_ID": "插件主模型",
    "MAI_STYLE_PROVIDER_ID": "风格/轻量任务模型",
    "DAILY_PLAN_PROVIDER_ID": "每日计划模型",
    "DETAIL_ENHANCEMENT_PROVIDER_ID": "细节增强模型",
    "DREAM_DIARY_PROVIDER_ID": "梦境日记模型",
    "CREATIVE_PROVIDER_ID": "通用创作模型",
    "CREATIVE_OUTLINE_PROVIDER_ID": "创作大纲模型",
    "CREATIVE_REVIEW_PROVIDER_ID": "创作审阅模型",
    "VOICE_PROMPT_PROVIDER_ID": "语音提示词模型",
    "tts_conversion_provider_id": "TTS 文本转换",
    "PHOTO_PROMPT_PROVIDER_ID": "生图提示词模型",
    "NARRATION_PROVIDER_ID": "叙述模型",
    "HISTORY_SUMMARY_PROVIDER_ID": "历史摘要模型",
    "RESPONSE_REVIEW_PROVIDER_ID": "回复复核模型",
    "SMART_SILENCE_PROVIDER_ID": "智能沉默模型",
    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": "主动人格判定模型",
    "TROUBLESHOOTING_PROVIDER_ID": "插件答疑/排障模型",
    "DAILY_REVIEW_PROVIDER_ID": "每日复盘模型",
    "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": "智能收口小模型",
    "REST_WAKEUP_PROVIDER_ID": "休息醒来判断模型",
    "RELATIONSHIP_ANALYSIS_PROVIDER_ID": "关系分析模型",
    "COMPANION_MEMORY_PROVIDER_ID": "陪伴记忆模型",
    "DIALOGUE_EPISODE_PROVIDER_ID": "对话剧集模型",
    "GROUP_INTERJECT_PROVIDER_ID": "群聊插话模型",
    "GROUP_EPISODE_PROVIDER_ID": "群聊剧集模型",
    "GROUP_SLANG_PROVIDER_ID": "群聊俚语模型",
    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": "群聊连续对话判断模型",
    "FORWARD_MESSAGE_PROVIDER_ID": "转发消息模型",
    "PLUGIN_VISION_PROVIDER_ID": "插件视觉模型",
    "PRIVATE_READING_VISION_PROVIDER_ID": "私读视觉模型",
    "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID": "表情反应向量模型",
    "NEWS_PROVIDER_ID": "新闻模型",
    "WEB_EXPLORATION_PROVIDER_ID": "联网探索模型",
    "EMOTION_JUDGEMENT_PROVIDER_ID": "情绪判定模型",
}

# 错误归一化规则：按关键字匹配出 error_code，避免把接口返回的整段堆栈塞到前端
_ERROR_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("timeout", re.compile(r"timeout|timed?\s*out|超时", re.I)),
    ("refused", re.compile(r"connection\s*refused", re.I)),
    ("connect", re.compile(r"connection\s*(reset|aborted)|could\s*not\s*connect|connect\s*timeout|网络|unreachable|NameResolutionError", re.I)),
    ("auth", re.compile(r"unauthorized|invalid\s*api\s*key|invalid\s*token|forbidden|401|403|auth", re.I)),
    ("rate_limit", re.compile(r"rate\s*limit|too\s*many\s*requests|429|quota", re.I)),
    ("not_found", re.compile(r"model\s*not\s*found|404|not\s*found", re.I)),
    ("server", re.compile(r"internal\s*server|502|503|504|bad\s*gateway|service\s*unavailable", re.I)),
]

# 错误码 → 卡片上给人看的说法。卡片会发到聊天里，直接印 `rate_limit` 这种内部码
# 既看不懂也不体面。
_ERROR_LABELS = {
    "timeout": "超时",
    "refused": "拒绝连接",
    "connect": "连接失败",
    "auth": "鉴权失败",
    "rate_limit": "请求受限",
    "not_found": "模型不存在",
    "server": "服务异常",
    "unknown": "未知异常",
}


def _error_label(code) -> str:
    return _ERROR_LABELS.get(str(code or "").strip(), "异常")


def _alert_code(decision) -> str:
    """告警卡片「错误码」那一列：优先最近一次失败的码，其次本批出现最多的码。

    返回内部码会看不懂（``rate_limit``），所以一律过 :func:`_error_label` 说人话。
    拿不到就返回 ``-``：宁可空着，也不要编一个「未知异常」骗人。
    """
    det = getattr(decision, "detail", None) or {}
    code = str(det.get("last_error_code") or getattr(decision, "last_error_code", "") or "")
    if code:
        return _error_label(code)
    codes = det.get("error_codes") or {}
    if codes:
        return _error_label(max(codes.items(), key=lambda kv: kv[1])[0])
    return "-"


def _alert_cause(decision) -> str:
    """告警里「为什么坏」那一段：错误码分布 + 最近一次的原始短文本。

    为什么必须带上：收到「连续失败 3 次」的人第一反应是「它怎么坏的」——
    是中转站 502、key 被限流，还是模型名写错了，处置方式完全不同。

    核心 ``provider_stats`` 只记 status 不记原因，所以真实对话路径**常常拿不到**；
    这时返回空串，由调用方在卡片上写「原因未知」并提示用 ``/模型检测`` 复测拿具体报错。
    """
    det = getattr(decision, "detail", None) or {}
    codes = det.get("error_codes") or {}
    code = str(det.get("last_error_code") or getattr(decision, "last_error_code", "") or "")
    msg = str(det.get("last_error_message") or "")
    parts: list[str] = []
    if codes:
        top = sorted(codes.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0])))[:3]
        parts.append(" ".join(
            f"{_error_label(c)}{'' if int(n or 0) < 2 else f'×{int(n)}'}" for c, n in top))
    elif code:
        parts.append(_error_label(code))
    # 原文只截 90 字且已脱敏（见 monitor.redact），重复时不再贴第二遍
    if msg and msg not in parts:
        parts.append(msg)
    return " · ".join(p for p in parts if p)


def _alert_distribution(group) -> str:
    """把一组告警里的错误码汇总成「超时×2 · 鉴权失败×1」，给卡片脚注用。

    一家供应商挂 10 个模型时，每一行都写「超时」不如一句话说清「这波是什么在坏」。
    """
    total: dict[str, int] = {}
    for d in group or []:
        for code, n in ((getattr(d, "detail", None) or {}).get("error_codes") or {}).items():
            total[str(code)] = total.get(str(code), 0) + int(n or 0)
    if not total:
        return ""
    top = sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    return " · ".join(f"{_error_label(c)}×{n}" for c, n in top)


# 错误短消息最长字符数（防止 200 字符堆栈塞满前端）
ERROR_MESSAGE_MAX = 80

# 流式探测用的提示词与非流式保持一致，否则两种延迟测的不是同一件事。
_STREAM_PROBE_PROMPT = "REPLY `PONG` ONLY"
# 拿到第几个分片就断流。1 = 只为测首字，绝不为跑完整段生成付钱。
_STREAM_PROBE_CHUNKS = 1


def _normalize_error(exc: BaseException | str) -> tuple[str, str]:
    """把异常归一化为 (error_code, error_message)。"""
    raw = str(exc or "")
    for code, pattern in _ERROR_RULES:
        if pattern.search(raw):
            return code, raw[:ERROR_MESSAGE_MAX]
    return "unknown", raw[:ERROR_MESSAGE_MAX]


# 健康态判定阈值。样本不足时坚决不给结论——「1 次失败 = 100% 失败率」是误报的主要来源。
MIN_LIVE_SAMPLES = 3
DEGRADED_FAIL_RATE = 0.10
DOWN_FAIL_RATE = 0.50

# 卡片/选单上的状态文案。集中一处，免得同一个状态在两张卡上叫法不一样。
_STATE_LABELS = {"healthy": "正常", "degraded": "降级", "down": "故障", "unknown": "无数据"}

# 聊天指令只展示「指令检测」通道开放的模型（卡片与能测的范围必须一致）。
# 一个都没开时这张卡必然是空的，所以这句提示在四个指令里共用。
_NO_COMMAND_SCOPE_HINT = (
    "没有任何模型开放了「指令检测」通道，所以这张卡是空的。\n"
    "去 WebUI 的「模型管理」页，把需要在这里关注的模型勾上「指令检测」。"
)

# /模型静音 的显式开关词（不带参数时是「切换」，带了就按词意走）
_MUTE_ON_WORDS = {"开", "开启", "打开", "启用", "恢复", "on", "true", "1"}
_MUTE_OFF_WORDS = {"关", "关闭", "静音", "取消", "解除", "off", "false", "0", "mute"}

# 序号选单：``f"{umo}|{actor}"`` -> {kind, options, multi, expires}。
# 放模块级而不是实例级，是因为拦数字消息的 CustomFilter 拿不到插件实例，
# 而它必须能在 filter 阶段就判断「这个会话有没有挂过选单」——
# 否则就得在 handler 里决定吞不吞消息，而 handler 一旦匹配上，
# 日常发一句「3」的人就会被我们的逻辑拦下来。
#
# 选项表（``options``）而不是一串 pid：卡片上的序号和「回数字」必须是**同一份表**，
# 而且现在序号既可能指向一个模型、也可能指向一个供应商分组（回组号 = 选整组），
# 只有存 {label, pids, group} 这种结构才表达得清楚。
_PICKERS: dict[str, dict] = {}
_PICKER_TTL_SEC = 180
# 一条消息里最多回这么多个序号（多选场景的防御性上限，正常用不到）
_MAX_PICKS = 8

# 「整条消息就是一串序号」的识别式：``3`` / ``1 3`` / ``1,3`` / ``1、3``
_NUM_LIST_RE = re.compile(r"^\d{1,3}(?:[\s,，、]+\d{1,3}){0,%d}$" % (_MAX_PICKS - 1))
_INDEX_SPLIT_RE = re.compile(r"[\s,，、]+")


def _picker_key(umo: str, actor: str) -> str:
    return f"{umo}|{actor}"


def _parse_index_list(text: Any) -> list[int]:
    """把「1 3 5」/「1,3」解析成 [1, 3, 5]；解析不出返回空表。

    去重但保序：用户回「3 3」不该被执行两次。
    """
    raw = str(text or "").strip()
    if not _NUM_LIST_RE.match(raw):
        return []
    out: list[int] = []
    for part in _INDEX_SPLIT_RE.split(raw):
        try:
            n = int(part)
        except (TypeError, ValueError):
            return []
        if n not in out:
            out.append(n)
    return out


def _picker_prune() -> None:
    now = time.time()
    for key in [k for k, v in _PICKERS.items() if v.get("expires", 0) <= now]:
        _PICKERS.pop(key, None)


def _normalize_options(options: list) -> list[dict]:
    """选项归一化：允许直接传 pid 字符串表（老调用点与自检脚本都这么用）。"""
    out: list[dict] = []
    for i, raw in enumerate(list(options or []), start=1):
        if isinstance(raw, dict):
            item = dict(raw)
        else:
            text = str(raw or "")
            item = {"label": text, "pids": [text]}
        item.setdefault("label", "")
        item["index"] = int(item.get("index") or i)
        item["pids"] = [str(p) for p in (item.get("pids") or ([item["label"]] if item["label"] else [])) if str(p)]
        item["group"] = bool(item.get("group"))
        out.append(item)
    return out


def _picker_arm(umo: str, actor: str, kind: str, options: list, multi: bool = False) -> None:
    _picker_prune()
    _PICKERS[_picker_key(umo, actor)] = {
        "kind": kind,
        "options": _normalize_options(options),
        "multi": bool(multi),
        "expires": time.time() + _PICKER_TTL_SEC,
    }


def _picker_entry(umo: str, actor: str) -> Optional[dict]:
    """取未过期的选单（过期的顺手清掉）。领取前想先看看「能不能多选」时用。"""
    key = _picker_key(umo, actor)
    entry = _PICKERS.get(key)
    if not entry:
        return None
    if float(entry.get("expires", 0) or 0) <= time.time():
        _PICKERS.pop(key, None)
        return None
    return entry


def _picker_peek(umo: str, actor: str) -> bool:
    """这个 (会话, 人) 有没有挂着未过期的选单。filter 阶段用，不作废。"""
    return _picker_entry(umo, actor) is not None


def _picker_disarm(umo: str, actor: str) -> None:
    _PICKERS.pop(_picker_key(umo, actor), None)


def _picker_take_multi(umo: str, actor: str, indices: list):
    """按序号领取（可多个），返回 ``(kind, [option, ...])``；取完即作废。

    **按选项自带的 ``index`` 查，而不是按列表下标**：卡片承诺的就是「回这个号」，
    万一以后有人拿一份裁剪过的选项表来挂选单（下标 ≠ 号），按号查才不会被静默错位。
    越界返回 ``None`` 且**不作废** —— 用户回了个不存在的数字时应该能重输，
    而不是让整张卡片白白失效（旧版就是这个语义，继续保留）。
    """
    key = _picker_key(umo, actor)
    entry = _picker_entry(umo, actor)
    if not entry:
        return None
    options = entry.get("options") or []
    if not options:
        return None
    by_index: dict[int, dict] = {}
    for opt in options:
        try:
            n = int(opt.get("index") or 0)
        except (TypeError, ValueError):
            continue
        by_index.setdefault(n, opt)
    picked: list[dict] = []
    seen: set = set()
    for idx in list(indices or []):
        opt = by_index.get(int(idx))
        if opt is None:
            return None
        if idx in seen:
            continue
        seen.add(idx)
        picked.append(opt)
    if not picked:
        return None
    _PICKERS.pop(key, None)
    return str(entry.get("kind") or ""), picked


def _picker_take(umo: str, actor: str, index: int):
    """单个序号领取，返回 ``(kind, option)``。越界返回 None 且不作废。"""
    got = _picker_take_multi(umo, actor, [int(index)])
    return None if got is None else (got[0], got[1][0])


def _flatten_pids(options: list) -> list[str]:
    """把选中的选项摊平成去重、保序的 provider id 表（分组会摊成组内所有模型）。"""
    out: list[str] = []
    for opt in list(options or []):
        for pid in (opt.get("pids") or []):
            pid = str(pid or "")
            if pid and pid not in out:
                out.append(pid)
    return out


def _actor_key(event: AstrMessageEvent) -> str:
    """这条消息是谁发的。

    核心 ``get_sender_id()`` 在 ``user_id`` 是 int 时会直接返回空串（已知行为），
    所以兜底去取原始 sender 字段。选单要按人而不只按会话来认，否则群里管理员
    刚列出模型，别人回一个数字就把系统默认模型切走了。
    """
    try:
        sid = str(event.get_sender_id() or "").strip()
        if sid:
            return sid
    except Exception:
        pass
    raw = getattr(getattr(event, "message_obj", None), "sender", None)
    for attr in ("user_id", "uid", "user"):
        try:
            v = raw.get(attr) if isinstance(raw, dict) else getattr(raw, attr, None)
        except Exception:
            v = None
        if v:
            return str(v)
    return str(event.unified_msg_origin or "")


class NumberPickerFilter(astr_filter.CustomFilter):
    """只在「整条消息就是一串序号」且这个会话+这个人挂着未过期选单时才放行。

    两条都要在 filter 阶段判完：如果先进了 handler 再决定不管，
    普通用户日常发一句「3」就会被插件吃掉不回话 —— 那是最难排查的投诉之一。

    允许 ``1 3 5`` / ``1,3`` 这种多选写法（``/模型检测`` 支持一次点几个模型），
    但**整条消息必须只有数字和分隔符**：夹了任何汉字就一律不认，
    日常聊天里的「3楼见」「12345」照旧走原来的流程。
    """

    def filter(self, event, cfg) -> bool:
        text = re.sub(r"\s+", " ", str(getattr(event, "message_str", "") or "").strip())
        if not _NUM_LIST_RE.match(text):
            return False
        return _picker_peek(str(event.unified_msg_origin or ""), _actor_key(event))


def _derive_state(live: dict, probe: dict) -> tuple[str, str]:
    """推一个健康态：真实对话监测优先，样本不够时退回最近一次探测。

    ``rate_limit`` 不判故障：并发探测和真实高负载都会自己触发 429，
    把它当故障会把健康模型标红，进而误导「换模型」的决策。

    Returns:
        ``(state, reason)``，state 取值 healthy / degraded / down / unknown。
    """
    counted = int((live or {}).get("counted") or 0)
    if counted >= MIN_LIVE_SAMPLES:
        rate = float((live or {}).get("fail_rate") or 0.0)
        if rate >= DOWN_FAIL_RATE:
            return "down", f"真实调用失败率 {rate:.0%}（{int(live.get('fail') or 0)}/{counted}）"
        if rate > DEGRADED_FAIL_RATE:
            return "degraded", f"真实调用失败率 {rate:.0%}"
        return "healthy", ""
    if probe:
        if probe.get("ok"):
            return "healthy", ""
        code = str(probe.get("error_code") or "")
        if code == "skipped":
            return "unknown", "未参与检测"
        if code == "rate_limit":
            return "degraded", "请求受限（可能并发自触发，未判为故障）"
        if code:
            return "down", code
    return "unknown", "暂无数据"


def _fmt_ms(value) -> str:
    """延迟格式化：**一律按毫秒原样给**，只有超过 1 分钟才折成「X分Y秒」。

    之前把 4200ms 显示成 4.2s 被否掉了 —— 一位小数的秒等于把精度四舍五入掉了，
    而看延迟的人要比的正是那几百毫秒的差别。拿不到值显示 ``-`` 而不是 0：
    非流式调用本就不产出 TTFT，显示 0 会让人以为模型快得离谱。
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "-"
    ms = int(round(n))
    if ms < 60_000:
        return f"{ms}ms"
    total_sec = ms // 1000
    minutes, seconds = divmod(total_sec, 60)
    if minutes < 60:
        return f"{minutes}分{seconds}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}时{minutes}分"


def _today_days(now: Optional[float] = None) -> float:
    """「今天」折算成滚动窗口的天数：从本地零点到现在。

    为什么不新增一个 today 分支贯穿整条链路：窗口计算本来就是 ``now - days*86400``，
    把本地零点差值折成小数天数就能复用全部现有逻辑（含 provider_stats 的
    created_at 字符串比较），改动面从三个文件缩到一个函数。
    凌晨刚过时窗口极短，所以留 0.05 天（约 72 分钟）的下限，避免零点后立刻什么都查不到。
    """
    ts = now if now is not None else time.time()
    lt = time.localtime(ts)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return max(0.05, (ts - midnight) / 86400.0)


def _parse_days(raw) -> float:
    """把 days 查询参数解析成天数。``today`` 表示本地零点到现在。"""
    text = str(raw or "").strip().lower()
    if text in ("today", "今天"):
        return _today_days()
    try:
        return max(0.5, min(90.0, float(text)))
    except (TypeError, ValueError):
        return _today_days()


def _fmt_success(fail_rate) -> str:
    """失败率转成功率。样本为 0 时不显示 100%（那是「没数据」不是「全对」）。"""
    try:
        return f"{(1.0 - float(fail_rate)) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _name_sort_key(value) -> tuple:
    """按名字排序的 key：小写化 + 原文兜底。

    卡片现在**一律按名字排序**（供应商、模型名各自升序），而序号选单要求顺序稳定：
    同一份数据刷新两次不能换顺序，否则用户照着上一条卡片回的序号会指到别的模型。
    """
    text = str(value if value is not None else "").strip()
    return (text.lower(), text)


def _fmt_ago(ts: Any, now: Optional[float] = None) -> str:
    """时间戳 → 「刚刚 / 3 分钟前 / 2 小时前 / 3 天前」；拿不到时间返回 ``-``。

    卡片上写**相对时间**而不是 ``09-22 14:05``：看这张卡的人要判断的是「这条数据还新鲜吗」，
    而「14:05」得先跟当前时刻做一次减法 —— 那个心智负担该由程序承担。
    超过一个月才退回绝对日期，否则会变成「47 天前」这种没意义的数字。
    """
    try:
        t = int(float(ts or 0))
    except (TypeError, ValueError):
        return "-"
    if t <= 0:
        return "-"
    delta = max(0.0, float(now if now is not None else time.time()) - t)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)} 天前"
    return time.strftime("%m-%d", time.localtime(t))


def _scope_label(days: float) -> str:
    """窗口天数 → 给人看的范围说法（今天 / 近 N 天）。"""
    try:
        d = float(days)
    except (TypeError, ValueError):
        return ""
    if d <= 0:
        return ""
    if abs(d - _today_days()) < 0.02:
        return "今天"
    return f"近 {d:g} 天"


def _cmd_args(event: AstrMessageEvent) -> str:
    """取指令的参数部分（第一个 token 之后的所有内容）。

    没有用 GreedyStr 声明形参：那样缺参时 CommandFilter 会抛 ValueError，
    而本指令希望缺参时回一句用法提示，所以自己解析。
    """
    try:
        text = re.sub(r"\s+", " ", str(event.get_message_text() or "")).strip()
    except Exception:
        return ""
    parts = text.split(" ", 1)
    return parts[1].strip() if len(parts) > 1 else ""


# v1.3.10 起 /模型静音 只做全局开关，逐模型静音连带它的时长解析一并下线
# （状态机里的 muted_until 仍然保留：将来要在 WebUI 上做单模型静音时是现成的）。


# 陪伴插件的 provider 配置真实存放在 schema 分组 model_assignment_config 下，
# 同时存在一层 invisible 的顶层扁平 legacy 副本。陪伴插件读写 config 时：
#   - 读取（_flat_get）：优先读 schema 分组嵌套值，其次读顶层扁平副本；
#   - 写入（_set_config_value / _set_into_config / _set_schema_group_config_value）：
#     递归同步所有出现位置（含扁平 + 分组嵌套）。
# 我们插件原先只用 cfg[key] 操作顶层扁平副本，导致改的是"假配置"，
# 陪伴插件仍读到分组里的旧值——这就是"自欺欺人"的根因。下面复刻陪伴插件的语义。
_MISSING = object()

# 陪伴插件 model_assignment_config（模型分流）schema 分组名
COMPANION_PROVIDER_GROUP = "model_assignment_config"


def _flat_get(cfg: Any, key: str, default: Any = _MISSING) -> Any:
    """复刻陪伴插件 helpers._flat_get：嵌套 schema 分组优先，其次扁平 legacy 副本。

    保证我们读到的 provider 值与陪伴插件运行时看到的一致。
    """
    if isinstance(cfg, dict):
        for value in cfg.values():
            if isinstance(value, dict):
                found = _flat_get(value, key, _MISSING)
                if found is not _MISSING:
                    return found
        if key in cfg:
            return cfg[key]
    for attr in ("data", "config"):
        target = getattr(cfg, attr, None)
        if isinstance(target, dict):
            found = _flat_get(target, key, _MISSING)
            if found is not _MISSING:
                return found
    getter = getattr(cfg, "get", None)
    if callable(getter):
        try:
            value = getter(key, _MISSING)
        except Exception:
            value = _MISSING
        if value is not _MISSING:
            return value
    return default


def _flat_set(cfg: Any, key: str, value: Any) -> None:
    """复刻陪伴插件 _set_config_value：把值写回 config 的所有位置。

    递归更新顶层扁平副本 + 嵌套 schema 分组（model_assignment_config），
    确保陪伴插件运行时（读分组）和我们显示（读扁平）看到一致的新值。
    """
    if not isinstance(cfg, dict):
        for attr in ("data", "config"):
            t = getattr(cfg, attr, None)
            if isinstance(t, dict):
                cfg = t
                break
    if not isinstance(cfg, dict):
        return

    def find_and_set(target: dict) -> bool:
        changed = False
        for child in target.values():
            if isinstance(child, dict):
                changed = find_and_set(child) or changed
        if key in target:
            target[key] = value
            changed = True
        return changed

    find_and_set(cfg)
    # 若分组存在但缺该 key（旧配置），补齐，保证分组内一致
    group = cfg.get(COMPANION_PROVIDER_GROUP)
    if isinstance(group, dict):
        group[key] = value


def _flat_set_existing(cfg: Any, key: str, value: Any) -> None:
    """只更新 config 中已存在该 key 的位置（含嵌套分组），都不存在时写顶层。

    用于 legacy flat 配置（如 model_fallback_overrides）：它能被 _flat_get 读到，
    但不属于 provider key，不能往 model_assignment_config 分组里补写，
    否则会把非 provider 字段塞进"模型分流"分组。
    """
    if not isinstance(cfg, dict):
        for attr in ("data", "config"):
            t = getattr(cfg, attr, None)
            if isinstance(t, dict):
                cfg = t
                break
    if not isinstance(cfg, dict):
        return

    def update(target: dict) -> bool:
        changed = False
        for child in target.values():
            if isinstance(child, dict):
                changed = update(child) or changed
        if key in target:
            target[key] = value
            changed = True
        return changed

    if not update(cfg):
        cfg[key] = value


def _empty_window() -> dict:
    """模型在窗口内一次调用都没有时的空值。注意 success_rate 是 None 不是 100%——
    「没数据」和「全成功」是两件事，显示成 100% 会误导换模型决策。"""
    return {
        "total": 0, "ok": 0, "fail": 0, "aborted": 0, "counted": 0,
        "fail_rate": 0.0, "success_rate": None,
        "avg_ttft_ms": None, "p95_ttft_ms": None, "ttft_samples": 0,
        "avg_latency_ms": None, "p95_latency_ms": None, "latency_samples": 0,
        "probe_avg_latency_ms": None, "probe_p95_latency_ms": None,
        "probe_avg_ttft_ms": None, "probe_latency_samples": 0,
        "tokens": 0, "chat": {"total": 0, "ok": 0, "fail": 0, "aborted": 0},
        "probe": {"total": 0, "ok": 0, "fail": 0},
    }


def _probe_source(trigger: Any) -> str:
    """把探测会话的 trigger 映射成展示用的调用来源标签。"""
    t = str(trigger or "")
    if t == "command":
        return "command"
    if t == "scheduled":
        return "scheduled"
    if t == "gateway":
        # 同伴插件（权限控制台）的「切换模型检测」发起的检测。单独标一类而不是并进
        # command：面板上要能分清「谁花的这笔钱」，也便于排查那边的冷却是否生效。
        return "gateway"
    return "manual"  # single / all / stream 都是人在面板上点的


def _merge_ledger(live_rows: list, probe_rows: list,
                  call_rows: Optional[list] = None) -> dict[str, dict]:
    """把三个互不相通的记录处合并成「每个模型的调用台账」。

    三路来源与谁说了算：

    - ``call_rows``（llm_calls，本插件埋的）：**每次 ``provider.text_chat`` 尝试**一行。
      覆盖面最广的一份 —— AstrBot 后台的「提供商测试」、WebChat 聊天界面、其它插件直调
      provider，只要走了 provider 就都在这里；而核心 provider_stats 只在 agent 对话轮
      结束时写一条。所以**只要有它的数据，次数与最近一次就以它为准** ——
      否则 AstrBot 侧的调用会被整个漏掉（用户提过）。
    - ``live_rows``（核心 provider_stats）：llm_calls 还没有任何数据时的兜底；
      它的另一项独有数据是 **token 用量**（llm_calls 不记 token），所以 token 始终从它算。
    - ``probe_rows``（model_test_results）：探测与真实对话的延迟量级不同，
      只并成败与最近一次。

    两路**不能相加**：正常的一轮 agent 对话，provider_stats 记一条、llm_calls 也记一条，
    描述的是同一次调用 —— 两边都算就是双倍计数。所以 llm_calls 有数据时，
    次数全部改从它出（口径也和「每次尝试」一致），provider_stats 只贡献 token。

    **成功率合并、延迟不合并**：探测是空载一句 PONG 的往返，和真实对话差一个数量级，
    混在一起平均就失去意义（详见 docs/模型监测与配置规划.md 第五节）。
    所以延迟/首字各留各的列，只有成败相加。
    """
    out: dict[str, dict] = {}
    call_rows = call_rows or []

    def bucket(pid: str) -> dict:
        return out.setdefault(pid, {
            "chat_rows": [], "call_rows": [], "probe_rows": [], "last": None,
        })

    for r in live_rows:
        pid = str(getattr(r, "provider_id", "") or "") or "(unknown)"
        b = bucket(pid)
        b["chat_rows"].append(r)
        # 「最近一次」只在 llm_calls 没有数据时才由 provider_stats 决定：
        # 同一次调用两边都有记录、时间戳又差着几秒，都参与比较只会让结果抖动。
        if call_rows:
            continue
        ts = int(getattr(r, "started_at", 0) or 0)
        cand = {
            "ts": ts, "source": "chat",
            "ok": bool(getattr(r, "ok", False)),
            "aborted": bool(getattr(r, "aborted", False)),
            "latency_ms": getattr(r, "latency_ms", None),
            "ttft_ms": getattr(r, "ttft_ms", None),
            "error_code": "",
        }
        if b["last"] is None or ts >= b["last"]["ts"]:
            b["last"] = cand

    for r in call_rows:
        pid = str(r.get("provider_id") or "") or "(unknown)"
        b = bucket(pid)
        b["call_rows"].append(r)
        ts = int(r.get("ts") or 0)
        cand = {
            "ts": ts, "source": "chat",
            "ok": bool(r.get("ok")), "aborted": bool(r.get("aborted")),
            "latency_ms": r.get("latency_ms"), "ttft_ms": r.get("ttft_ms"),
            "error_code": str(r.get("error_code") or ""),
        }
        if b["last"] is None or ts >= b["last"]["ts"]:
            b["last"] = cand

    for r in probe_rows:
        pid = str(r.get("provider_id") or "") or "(unknown)"
        b = bucket(pid)
        b["probe_rows"].append(r)
        ts = int(r.get("checked_at") or 0)
        cand = {
            "ts": ts, "source": _probe_source(r.get("trigger")),
            "ok": bool(r.get("ok")), "aborted": False,
            "latency_ms": r.get("latency_ms"), "ttft_ms": r.get("ttft_ms"),
            "error_code": str(r.get("error_code") or ""),
        }
        if b["last"] is None or ts >= b["last"]["ts"]:
            b["last"] = cand

    merged: dict[str, dict] = {}
    for pid, b in out.items():
        probes = b["probe_rows"]
        if b["call_rows"]:
            # 每次尝试一行：这才是「这个模型今天到底被调了多少次、成了几次」
            chats = b["call_rows"]
            c_total = len(chats)
            c_aborted = sum(1 for r in chats if r.get("aborted"))
            c_fail = sum(1 for r in chats if not r.get("ok") and not r.get("aborted"))
            lat = [r.get("latency_ms") for r in chats if r.get("latency_ms")]
            ttft = [r.get("ttft_ms") for r in chats if r.get("ttft_ms")]
        else:
            chats = b["chat_rows"]
            c_total = len(chats)
            c_aborted = sum(1 for r in chats if getattr(r, "aborted", False))
            c_fail = sum(1 for r in chats if not getattr(r, "ok", False)
                         and not getattr(r, "aborted", False))
            lat = [r.latency_ms for r in chats if getattr(r, "latency_ms", None)]
            ttft = [r.ttft_ms for r in chats if getattr(r, "ttft_ms", None)]
        c_ok = c_total - c_aborted - c_fail
        p_total = len(probes)
        p_fail = sum(1 for r in probes if not r.get("ok"))
        p_ok = p_total - p_fail
        # 探测路没有 aborted 概念，也不产出 token
        counted = (c_total - c_aborted) + p_total
        fail = c_fail + p_fail
        p_lat = [r.get("latency_ms") for r in probes if r.get("latency_ms")]
        p_ttft = [r.get("ttft_ms") for r in probes if r.get("ttft_ms")]
        last = b["last"]
        if last is not None and last["source"] != "chat":
            last = dict(last)
            last["aborted"] = False
        merged[pid] = {
            "last": last,
            "window": {
                "total": c_total + p_total,
                "ok": c_ok + p_ok,
                "fail": fail,
                "aborted": c_aborted,
                "counted": counted,
                "fail_rate": round(fail / counted, 4) if counted else 0.0,
                "success_rate": round(1 - (fail / counted), 4) if counted else None,
                # 延迟按来源分列，绝不跨来源平均
                "avg_ttft_ms": round(sum(ttft) / len(ttft), 1) if ttft else None,
                "p95_ttft_ms": percentile(ttft, 95),
                "ttft_samples": len(ttft),
                "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
                "p95_latency_ms": percentile(lat, 95),
                "latency_samples": len(lat),
                "probe_avg_latency_ms": round(sum(p_lat) / len(p_lat), 1) if p_lat else None,
                "probe_p95_latency_ms": percentile(p_lat, 95),
                "probe_avg_ttft_ms": round(sum(p_ttft) / len(p_ttft), 1) if p_ttft else None,
                "probe_latency_samples": len(p_lat),
                # token 只有 provider_stats 有（llm_calls 不记），所以始终从它算 ——
                # 即使次数已经改由 llm_calls 说了算。
                "tokens": sum(int(getattr(r, "token_input", 0) or 0)
                              + int(getattr(r, "token_cached", 0) or 0)
                              + int(getattr(r, "token_output", 0) or 0)
                              for r in b["chat_rows"]),
                "chat": {"total": c_total, "ok": c_ok, "fail": c_fail, "aborted": c_aborted},
                "probe": {"total": p_total, "ok": p_ok, "fail": p_fail},
            },
        }
    return merged


@register("astrbot_plugin_model_panel", "local", "模型管理与检测面板", "0.1.0")
class ModelPanelPlugin(Star):
    def __init__(self, context: Context, config: Optional[Any] = None):
        super().__init__(context)
        # AstrBot 注入的插件配置（AstrBotConfig，dict 子类，含 save_config()）。
        # 必须保存为 self.config，否则 api_set_config 等写入接口拿不到配置对象。
        self.config: Optional[Any] = config
        self.storage: Optional[Storage] = None
        self.sessions = SessionManager()
        # 模型名 -> provider id 的缓存，避免每次 LLM 调用都枚举全部 provider
        self._model_provider_cache: dict[str, str] = {}
        # 全局并发去重：同时只允许一个一键检测任务在跑
        self._test_all_lock = asyncio.Lock()
        # 全插件模型配置扫描器（惰性创建，见 _scanner()）
        self._plugin_scanner: Optional[PluginModelScanner] = None
        # 核心 provider_stats 只读适配层。传 lambda 而不是库对象，
        # 是为了热重载后仍拿到当前 Context 里的数据库实例。
        self.live_stats = LiveStatsReader(lambda: context.get_db())
        # 巡检后台任务（initialize 里按配置启动）
        self._monitor_running = False
        self._monitor_task: Optional[asyncio.Task] = None

    async def initialize(self):
        # 数据库路径：优先用 AstrBot 提供的数据目录，回退到相对路径
        db_path = DEFAULT_DB_PATH
        try:
            base = getattr(self.context, "data_dir", None) or getattr(self.context, "_data_dir", None)
            if base:
                db_path = os.path.join(str(base), "model_panel.db")
        except Exception:
            pass
        self.storage = Storage(db_path)
        await self.storage.init()
        # 历史清理一次
        try:
            retention = self._test_config().get("history_retention_days") or 30
            deleted = await self.storage.cleanup_older_than(int(retention))
            if deleted:
                logger.info(f"[ModelPanel] 清理历史会话 {deleted} 条（> {retention} 天）")
        except Exception as e:
            logger.warning(f"[ModelPanel] 清理历史失败: {e}")
        # 用量记录单独保留 90 天（趋势图需要按天数据，比检测历史保留更久）
        try:
            deleted_usage = await self.storage.cleanup_usage_older_than(90)
            if deleted_usage:
                logger.info(f"[ModelPanel] 清理用量记录 {deleted_usage} 条（> 90 天）")
        except Exception as e:
            logger.warning(f"[ModelPanel] 清理用量记录失败: {e}")
        # 逐次调用记录是这张表里增长最快的一份（每轮对话可能好几行），保留 30 天
        try:
            deleted_calls = await self.storage.cleanup_calls_older_than(30)
            if deleted_calls:
                logger.info(f"[ModelPanel] 清理逐次调用记录 {deleted_calls} 条（> 30 天）")
        except Exception as e:
            logger.warning(f"[ModelPanel] 清理逐次调用记录失败: {e}")
        # 装上逐次调用埋点。核心 provider_stats 是「每轮一条且归属最终模型」，
        # 被备用模型救回来的失败在它那里根本没有记录，只能自己包一层才看得全。
        self._install_call_recorder()
        # 探测核心 provider_stats 能否读（老版本 AstrBot 没这张表）。
        # 读不到就整块实时监测降级，原有的主动探测功能不受影响。
        try:
            if await self.live_stats.probe():
                logger.info("[ModelPanel] 实时监测已启用：只读核心 provider_stats")
            else:
                logger.info("[ModelPanel] 实时监测不可用（核心表缺失），仅保留主动探测")
        except Exception as e:
            logger.warning(f"[ModelPanel] 探测 provider_stats 失败: {e}")
        # provider 被删除或改名后，这边的档案/范围行不会自动消失（核心不做级联清理）
        await self._reap_orphans()
        self._register_routes()
        cfg = MonitorConfig.from_config(self.config)
        if cfg.enabled:
            self._monitor_running = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info(f"[ModelPanel] 巡检已启动：每 {cfg.interval_sec}s 一轮，告警通知={'开' if cfg.notify_enabled else '关'}")
        logger.info(f"[ModelPanel] 模型控制台插件已初始化（db={db_path}）")

    def _install_call_recorder(self) -> None:
        """给当前加载的 provider 类挂上逐次调用记录。幂等，可在巡检轮里重复调。

        重复调是有意的：用户新加一种 provider 类型时它的类还没被包过，
        而 install() 内部按类去重，代价只是几次集合查询。
        """
        try:
            n = install_call_recorder(self._record_call, _normalize_error, self._chat_providers())
            if n:
                logger.info(f"[ModelPanel] 逐次调用埋点：本次新覆盖 {n} 个 provider 类，"
                            f"累计 {len(recorder_wrapped_classes())} 个")
        except Exception as e:
            # 埋点装不上只是失去逐次数据，绝不能因此让插件起不来
            logger.warning(f"[ModelPanel] 挂载逐次调用记录失败: {e}")

    async def _has_calls(self) -> bool:
        """逐次调用表里有没有过任何一行。

        用来区分两种「逐次列是空的」：埋点根本没装上（要提示用户），
        和今天确实还没人调用过（正常）。两者都显示空白会误导排查方向。
        """
        try:
            return bool(self.storage) and (await self.storage.calls_count_all()) > 0
        except Exception:
            return False

    async def _record_call(self, row: dict) -> None:
        """call_recorder 的落库回调。"""
        if self.storage is None:
            return
        await self.storage.insert_call(row)

    async def _reap_orphans(self) -> None:
        """回收 provider 已不存在的档案与检测范围行。"""
        try:
            if not self.storage:
                return
            known = {str(i) for i in self._provider_ids() if str(i)}
            if not known:
                # 一个 chat provider 都没加载时不做回收，避免把档案全清掉
                return
            removed = await self.storage.reap_orphans(known)
            if any(removed.values()):
                logger.info(f"[ModelPanel] 回收孤儿行: {removed}")
        except Exception as e:
            logger.warning(f"[ModelPanel] 回收孤儿行失败: {e}")

    # ---------------- LLM 用量采集 ----------------
    @astr_filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, response: LLMResponse
    ) -> None:
        """统计每次 LLM 调用的 token 用量，写入本插件数据库供首页展示。

        AstrBot 在每次 LLM 调用结束后触发。usage 为空（部分 provider 不上报用量）
        或流式分片时跳过，避免重复计数。
        """
        try:
            if self.storage is None:
                return
            usage = getattr(response, "usage", None)
            if usage is None:
                return
            if getattr(response, "is_chunk", False):
                return
            inp = int(getattr(usage, "input_other", 0) or 0)
            cached = int(getattr(usage, "input_cached", 0) or 0)
            out = int(getattr(usage, "output", 0) or 0)
            total = int(getattr(usage, "total", 0) or (inp + cached + out))
            if total <= 0:
                return
            model = self._usage_model_name(response)
            await self.storage.record_usage(
                model=model,
                provider_id=self._provider_id_by_model(model),
                input_tokens=inp,
                cached_tokens=cached,
                output_tokens=out,
            )
        except Exception as e:
            # 统计失败绝不能影响正常对话
            logger.debug(f"[ModelPanel] 记录 LLM 用量失败: {e}")

    def _usage_model_name(self, response: Any) -> str:
        """从响应的原始对象里尽量取出模型名（不同 provider 字段名不同）。"""
        raw = getattr(response, "raw_completion", None)
        if raw is None:
            return ""
        for attr in ("model", "model_name", "model_version"):
            v = getattr(raw, attr, None)
            if isinstance(v, str) and v.strip():
                return v.strip()
        if isinstance(raw, dict):
            for k in ("model", "model_name", "model_version"):
                v = raw.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""

    def _provider_id_by_model(self, model: str) -> str:
        """按模型名反查 provider id（同名模型取第一个匹配，结果缓存）。"""
        if not model:
            return ""
        if model in self._model_provider_cache:
            return self._model_provider_cache[model]
        pid = ""
        try:
            for p in self._chat_providers():
                d = self._provider_display(p)
                if str(d.get("model") or "").strip() == model:
                    pid = str(d.get("id") or "")
                    break
        except Exception:
            pid = ""
        self._model_provider_cache[model] = pid
        return pid

    # ---------------- 隐藏指令：/陪伴分数 ----------------
    # 只读查询陪伴插件用户记录里的好感度快照；不写陪伴插件任何状态。
    _AFFINITY_STAGES: tuple[tuple[int, str], ...] = (
        (900, "亲密"),
        (600, "亲近"),
        (200, "熟悉"),
        (0, "初识"),
        (-400, "疏离"),
        (-800, "强烈疏离"),
    )

    @classmethod
    def _affinity_stage_label(cls, score: int) -> str:
        if score >= 1200:
            return "深度联结"
        for minimum, label in cls._AFFINITY_STAGES:
            if score >= minimum:
                return label
        return "极度疏离"

    # 伴侣插件"当前互动状态"（短期互动温度）七档，key 与中文标签取自
    # companion_interaction_expression.ExpressionBand / EXPRESSION_BAND_LABELS，
    # 从冷到暖排序（下面按顺序比较档位高低，不能乱序）。
    _INTERACTION_BANDS: tuple[str, ...] = (
        "avoidant",
        "hurt",
        "relaxed",
        "lively",
        "warm",
        "close",
        "affectionate",
    )
    _INTERACTION_BAND_LABELS: dict[str, str] = {
        "avoidant": "回避",
        "hurt": "受伤",
        "relaxed": "放松",
        "lively": "活泼",
        "warm": "温暖",
        "close": "亲近",
        "affectionate": "爱意",
    }
    # 仅主要用户可用的档位（普通用户会被伴侣插件降到「温暖」）
    _INTERACTION_OWNER_ONLY: frozenset[str] = frozenset({"close", "affectionate"})
    # 普通用户的互动温度上限候选（伴侣插件 NORMAL_INTERACTION_BAND_CAPS）
    _INTERACTION_NORMAL_CAPS: tuple[str, ...] = ("relaxed", "lively", "warm")
    _INTERACTION_DEFAULT_CAP = "warm"

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _interaction_band_of(cls, value: Any) -> str:
        """从多种历史结构里取出互动档位 key（兼容 band/expression_band/state/mode）。"""
        if isinstance(value, dict):
            for key in ("expression_band", "band", "state", "mode"):
                text = str(value.get(key) or "").strip().lower()
                if text in cls._INTERACTION_BAND_LABELS:
                    return text
            return ""
        text = str(value or "").strip().lower()
        return text if text in cls._INTERACTION_BAND_LABELS else ""

    @classmethod
    def _baseline_interaction_band(cls, score: int, *, owner_exclusive: bool) -> str:
        """伴侣插件 `_baseline_band`：专属联结恒为亲近，其余按关系分数分档。

        用于档案里没有 current_interaction（旧档案 / 未开启关系距离感）时的推算，
        保证指令仍能给出一个与伴侣插件运行时一致的基线状态。
        """
        if owner_exclusive:
            return "close"
        if score < -400:
            return "avoidant"
        if score < 0:
            return "hurt"
        if score >= 600:
            return "warm"
        if score >= 200:
            return "lively"
        return "relaxed"

    @classmethod
    def _interaction_state(
        cls,
        record: dict,
        score: int,
        *,
        owner_exclusive: bool,
        role: str,
    ) -> dict:
        """解析伴侣插件的「当前互动状态」（短期互动温度，七档）。

        数据源：用户档案的 `current_interaction`（伴侣插件由
        `current_interaction_projection` 归一化后写入）。历史数据可能：
        - 档位键名是 `expression_band` / `band` / `state` / `mode` 之一；
        - 整个字段缺失（旧档案、或未开启关系距离感）→ 用关系分数按伴侣插件的
          `_baseline_band` 推算，并标注 inferred（不谎报为"真实状态"）。

        同时复刻投影的归一化语义（否则会显示伴侣插件实际不会用的档位）：
        - 普通用户不能进入「亲近 / 爱意」，会被降到「温暖」；
        - 普通用户不得超过 `normal_interaction_band_cap`（默认温暖）；
        - `expires_at` 过期且无动态余波时回到「放松」。
        """
        payload = record.get("current_interaction")
        band = cls._interaction_band_of(payload)
        if not band and isinstance(payload, dict):
            # 兜底：档案里可能只写了中文标签
            label = str(payload.get("label") or "").strip()
            for key, cn in cls._INTERACTION_BAND_LABELS.items():
                if label == cn:
                    band = key
                    break
        raw: dict = payload if isinstance(payload, dict) else {}

        inferred = not band
        if inferred:
            band = cls._baseline_interaction_band(
                score, owner_exclusive=owner_exclusive
            )

        manual = bool(raw.get("manual_override")) or str(
            raw.get("source") or ""
        ).strip().lower() == "manual"
        expires_at = cls._num(raw.get("expires_at"))

        capped = False
        if role != "owner":
            if band in cls._INTERACTION_OWNER_ONLY:
                band = "warm"
                capped = True
            else:
                cap = str(raw.get("normal_interaction_band_cap") or "").strip().lower()
                if cap not in cls._INTERACTION_NORMAL_CAPS:
                    cap = cls._INTERACTION_DEFAULT_CAP
                if cls._INTERACTION_BANDS.index(band) > cls._INTERACTION_BANDS.index(cap):
                    band = cap
                    capped = True

        expired = False
        if not inferred and expires_at and expires_at <= time.time():
            expired = True
            band = "relaxed"
            manual = False

        if inferred:
            source = "baseline"
        elif manual:
            source = "manual"
        else:
            source = "auto"
        reason = " ".join(str(raw.get("reason") or raw.get("reason_code") or "").split())
        return {
            "band": band,
            "label": cls._INTERACTION_BAND_LABELS.get(band, band),
            "source": source,
            "manual": manual,
            "expires_at": expires_at,
            "reason": reason[:40],
            "capped": capped,
            "expired": expired,
            "inferred": inferred,
        }

    @staticmethod
    def _find_affinity_record(users: dict, sender: str) -> Optional[dict]:
        direct = users.get(sender)
        if isinstance(direct, dict):
            return direct
        for item in users.values():
            if not isinstance(item, dict):
                continue
            if str(item.get("user_id") or "").strip() == sender:
                return item
            aliases = item.get("alias_user_ids")
            if isinstance(aliases, list) and sender in {str(a).strip() for a in aliases}:
                return item
        # 兜底：按私聊 umo 结尾匹配（如 aiocqhttp:FriendMessage:12345）
        suffix = ":" + sender
        for item in users.values():
            if not isinstance(item, dict):
                continue
            umo = str(item.get("umo") or item.get("last_inbound_umo") or "")
            if umo.endswith(suffix):
                return item
        return None

    async def _companion_affinity_record(self, sender_id: str) -> Optional[dict]:
        star = self._companion_star()
        if star is None:
            return None
        plugin_obj = getattr(star, "star_cls", None) or getattr(star, "instance", None) or star
        data = getattr(plugin_obj, "data", None)
        if not isinstance(data, dict):
            return None
        users = data.get("users")
        if not isinstance(users, dict):
            return None
        sender = str(sender_id or "").strip()
        if not sender:
            return None
        lock = getattr(plugin_obj, "_data_lock", None)
        if isinstance(lock, asyncio.Lock):
            async with lock:
                return self._find_affinity_record(users, sender)
        return self._find_affinity_record(users, sender)

    @astr_filter.command("陪伴分数")
    async def cmd_companion_affinity(self, event: AstrMessageEvent):
        """隐藏指令：查询自己在陪伴插件中的好感度分数与更新时间（私聊群聊均可用）。"""
        try:
            sender_id = str(event.get_sender_id() or "").strip()
        except Exception:
            sender_id = ""
        if not sender_id:
            await self._reply(event, "暂时拿不到你的身份信息，稍后再试试吧～")
            return
        if self._companion_star() is None:
            await self._reply(event, "陪伴插件还没加载，暂时查不到好感度哦～")
            return
        record = await self._companion_affinity_record(sender_id)
        if record is None:
            await self._reply(event, "还没有你的好感度记录，和 bot 聊聊天就会有啦～")
            return
        try:
            score = int(record.get("relationship_score") or 0)
        except (TypeError, ValueError):
            score = 0
        score = max(-1200, min(1200, score))
        updated_text = "暂无互动记录"
        try:
            ts = float(record.get("relationship_last_effective_at") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 0:
            updated_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        extra = ""
        if str(record.get("relationship_mode") or "").strip().lower() == "owner_exclusive":
            extra = "\n状态：专属联结（分数已冻结）"
        elif str(record.get("relationship_role") or "").strip().lower() == "owner":
            extra = "\n身份：主要用户"
        # 当前互动状态（短期互动温度）：伴侣插件按边界、被刺到、重新接近等事件
        # 实时调整，和"关系阶段"（长期分数）是两个维度，所以单独一行展示。
        role = str(record.get("relationship_role") or "").strip().lower()
        state = self._interaction_state(
            record,
            score,
            owner_exclusive=str(record.get("relationship_mode") or "")
            .strip()
            .lower()
            == "owner_exclusive",
            role=role,
        )
        notes: list[str] = []
        if state["inferred"]:
            notes.append("按好感度推算")
        elif state["manual"]:
            notes.append("管理员设置")
        elif state["expired"]:
            notes.append("原状态已过期")
        else:
            notes.append("自动判定")
        if state["capped"]:
            notes.append("受互动温度上限限制")
        expires_at = float(state.get("expires_at") or 0)
        if expires_at > time.time():
            notes.append(
                "有效期至 " + time.strftime("%m-%d %H:%M", time.localtime(expires_at))
            )
        # 原因多为内部错误码（no_contact / boundary_violation…），只在写了中文说明时展示
        reason = str(state.get("reason") or "")
        if reason and any("\u4e00" <= ch <= "\u9fff" for ch in reason):
            notes.append(f"原因：{reason}")
        await self._reply(event,
            f"💕 好感度查询\n"
            f"当前分数：{score}（{self._affinity_stage_label(score)}）{extra}\n"
            f"互动状态：{state['label']}（{' · '.join(notes)}）\n"
            f"更新时间：{updated_text}"
        )

    # ---------------- 配置读取 ----------------
    def _test_config(self) -> dict[str, Any]:
        """从插件 config 读取检测参数；字段不存在则用默认值。"""
        defaults = {
            "test_timeout": 45,
            "test_retry_count": 1,
            "test_retry_backoff": 2.0,
            "history_retention_days": 30,
        }
        try:
            cfg = getattr(self, "config", None)
            if cfg is not None and hasattr(cfg, "get"):
                for k in defaults:
                    v = cfg.get(k)
                    if v is None:
                        continue
                    if k == "test_retry_backoff":
                        defaults[k] = float(v)
                    elif k == "history_retention_days":
                        defaults[k] = max(0, int(v))
                    else:
                        defaults[k] = int(v)
        except Exception:
            pass
        defaults["test_timeout"] = max(1, int(defaults["test_timeout"]))
        defaults["test_retry_count"] = max(0, int(defaults["test_retry_count"]))
        defaults["test_retry_backoff"] = max(0.0, float(defaults["test_retry_backoff"]))
        return defaults

    async def _probe_modes(self) -> dict[str, str]:
        """provider_id -> probe_mode。档案缺失一律 non_stream：最保守，不多花钱。"""
        if not self.storage:
            return {}
        try:
            profiles = await self.storage.get_all_profiles()
        except Exception:
            return {}
        out = {}
        for pid, prof in profiles.items():
            mode = str((prof or {}).get("probe_mode") or "non_stream")
            out[pid] = mode if mode in PROBE_MODES else "non_stream"
        return out

    # ---------------- 路由注册 ----------------
    def _register_routes(self) -> None:
        routes = [
            ("/panel/overview", self.api_overview, ["GET"]),
            ("/panel/providers", self.api_list_providers, ["GET"]),
            ("/panel/providers/test", self.api_test_provider, ["POST"]),
            ("/panel/providers/test_all", self.api_test_all_providers, ["POST"]),
            ("/panel/providers/test_all_stream", self.api_test_all_stream, ["POST"]),
            ("/panel/providers/session/<session_id>", self.api_session_state, ["GET"]),
            ("/panel/providers/results", self.api_test_results, ["GET"]),
            ("/panel/providers/history", self.api_test_history, ["GET"]),
            ("/panel/health", self.api_health, ["GET"]),
            ("/panel/profile", self.api_profile_set, ["POST"]),
            ("/panel/vendor", self.api_vendor_set, ["POST"]),
            ("/panel/scope", self.api_scope_set, ["POST"]),
            ("/panel/preferences", self.api_get_preferences, ["GET"]),
            ("/panel/preferences", self.api_set_preferences, ["POST"]),
            ("/panel/config", self.api_get_config, ["GET"]),
            ("/panel/config", self.api_set_config, ["POST"]),
            ("/panel/default_model", self.api_default_model, ["GET"]),
            ("/panel/default_model/config", self.api_default_model_config, ["GET"]),
            ("/panel/default_model/set", self.api_default_model_set, ["POST"]),
            ("/panel/companion/providers", self.api_companion_providers, ["GET"]),
            ("/panel/companion/replace", self.api_companion_replace, ["POST"]),
            ("/panel/companion/set", self.api_companion_set, ["POST"]),
            ("/panel/plugin_models", self.api_plugin_models, ["GET"]),
            ("/panel/plugin_models/set", self.api_plugin_models_set, ["POST"]),
        ]
        for path, handler, methods in routes:
            self.context.register_web_api(
                f"{PAGE_API_PREFIX}{path}",
                handler,
                methods,
                "ModelPanel " + path,
            )

    # ---------------- 工具 ----------------
    def _chat_providers(self) -> list[Any]:
        try:
            providers = self.context.get_all_providers()
            logger.debug(f"[ModelPanel] get_all_providers 数量: {len(providers)}")
            return providers
        except Exception as e:
            logger.warning(f"[ModelPanel] get_all_providers 失败: {e}")
            return []

    def _provider_display(self, provider: Any) -> dict:
        cfg = getattr(provider, "provider_config", None)
        cfg = cfg if isinstance(cfg, dict) else {}
        pid = str(cfg.get("id") or "")
        ptype = str(cfg.get("type") or cfg.get("provider_type") or "")
        # 供应商名 = 提供商源唯一 ID（provider_source_id），其次 name/provider。
        # 绝不能退回用 type（openai_chat_completion 这类）当供应商名。
        name = str(
            cfg.get("provider_source_id")
            or cfg.get("name")
            or cfg.get("provider")
            or pid
            or ""
        )
        model = ""
        try:
            m = provider.meta()
            pid = str(getattr(m, "id", None) or pid)
            ptype = str(getattr(m, "type", None) or ptype)
            model = str(getattr(m, "model", None) or "")
        except Exception:
            pass
        if not model:
            try:
                model = str(getattr(provider, "get_model", lambda: "")() or "")
            except Exception:
                model = ""
        # 最后兜底：从 cfg 里找 model 字段
        if not model:
            for k in ("model", "default_model", "selected_model"):
                v = cfg.get(k)
                if isinstance(v, str) and v.strip():
                    model = v.strip()
                    break
        # 如果 model 还是空，给一个基于 name 或 id 的可读 fallback（避免下拉显示空白）
        if not model:
            base = name or pid or "unknown"
            # 大部分 provider 把 model 写在 cfg.model，但有些隐藏在 list 形式
            for k in ("models", "model_list"):
                lst = cfg.get(k)
                if isinstance(lst, list) and lst:
                    if isinstance(lst[0], str):
                        model = lst[0]
                        break
                    if isinstance(lst[0], dict):
                        model = str(lst[0].get("model") or lst[0].get("name") or "")
                        if model:
                            break
            if not model:
                # 标识成"未指定"而不是空字符串，避免前端下拉空白
                model = f"{base} (model unknown)"
        # display_model：供应商(/)模型 的完整展示名。若 model 已自带供应商前缀则不再重复。
        display_model = model
        try:
            if name and model and name != model:
                prefix = f"{name}/"
                if not model.startswith(prefix) and not model.startswith(f"{name} ") and not model.startswith(f"{name}-"):
                    display_model = f"{name}/{model}"
        except Exception:
            display_model = model
        return {
            "id": pid,
            "name": name,
            "type": ptype,
            "model": model,
            "display_model": display_model,
        }

    def _provider_model_map(self) -> dict[str, str]:
        """provider id -> model 展示名。陪伴插件 config 里存的是 provider id，
        但我们按"模型名"匹配替换，需要先把 id 翻译成 model 名。
        用带供应商前缀的 display_model，保证展示和匹配都是完整路径
        （如 NVIDIA/deepseek-ai/deepseek-v4-flash-0731）。"""
        out: dict[str, str] = {}
        try:
            for p in self._chat_providers():
                d = self._provider_display(p)
                if d["id"]:
                    out[d["id"]] = d.get("display_model") or d["model"]
        except Exception:
            pass
        return out

    def _model_for_provider_id(self, provider_id: str) -> str:
        if not provider_id:
            return ""
        return self._provider_model_map().get(provider_id, "")

    def _companion_star(self) -> Any:
        try:
            stars = self.context.get_all_stars()
            logger.debug(f"[ModelPanel] get_all_stars 数量: {len(stars)}")
            for star in stars:
                name = getattr(star, "name", "") or ""
                root = getattr(star, "root_dir_name", "") or ""
                if name == COMPANION_PLUGIN_NAME or root == COMPANION_PLUGIN_NAME:
                    logger.debug(f"[ModelPanel] 已找到陪伴插件: name={name} root={root}")
                    return star
            all_names = [
                f"{getattr(s, 'root_dir_name', '?')}({getattr(s, 'name', '?')})"
                for s in stars
            ]
            logger.warning(
                f"[ModelPanel] 未找到陪伴插件 {COMPANION_PLUGIN_NAME}，当前插件: {all_names}"
            )
        except Exception as e:
            logger.warning(f"[ModelPanel] 遍历插件失败: {e}")
        return None

    def _companion_config(self) -> Any:
        star = self._companion_star()
        if star is None:
            return None
        return getattr(star, "config", None)

    def _companion_provider_values(self) -> dict[str, str]:
        cfg = self._companion_config()
        if cfg is None:
            return {}
        values: dict[str, str] = {}
        for key in COMPANION_PROVIDER_KEYS:
            raw = _MISSING
            try:
                # 用与陪伴插件一致的 _flat_get：优先 schema 分组嵌套值，
                # 避免读到顶层 legacy 扁平副本与真实值不一致。
                # 注意 _flat_get 找不到配置项时会返回哨兵 _MISSING（一个
                # object 实例），不能用 `if raw` 判空，否则会被当成真值而
                # str() 出 "<object object at 0x...>"。必须显式排除 _MISSING。
                raw = _flat_get(cfg, key)
            except Exception:
                raw = _MISSING
            values[key] = str(raw).strip() if (raw is not _MISSING and raw) else ""
        return values

    # 陪伴插件"备用模型"配置：model_fallback_overrides，值为 {provider_key: 备用 provider_id}
    # 的 JSON 字符串（顶层 legacy flat key）。用与陪伴插件一致的 _normalize 语义解析。
    FALLBACK_CONFIG_KEY = "model_fallback_overrides"

    def _companion_fallback_raw(self) -> dict[str, str]:
        """解析 model_fallback_overrides 的全部内容。

        保留未收录在 COMPANION_PROVIDER_KEYS 里的 key：陪伴插件每次升级都可能
        新增 provider key，若这里按白名单过滤，写回时会把用户已有（我们还不认识的）
        备用配置静默抹掉。
        """
        cfg = self._companion_config()
        if cfg is None:
            return {}
        raw = None
        try:
            raw = _flat_get(cfg, self.FALLBACK_CONFIG_KEY)
        except Exception:
            raw = None
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            try:
                raw = json.loads(text)
            except Exception:
                return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for raw_key, raw_provider_id in raw.items():
            key = str(raw_key or "").strip()
            pid = str(raw_provider_id or "").strip()
            if key and pid:
                out[key] = pid
        return out

    def _companion_fallback_values(self) -> dict[str, str]:
        """返回 {provider_key: 备用 provider_id}，只含我们认识的 provider key。"""
        raw = self._companion_fallback_raw()
        return {k: v for k, v in raw.items() if k in COMPANION_PROVIDER_KEYS}

    def _set_companion_fallback(self, key: str, provider_id: str) -> None:
        """把某 key 的备用 provider 写入 model_fallback_overrides。

        model_fallback_overrides 是顶层 legacy flat 配置（不是 provider key），
        用 _flat_set_existing 写，避免被补进 model_assignment_config 分组。
        """
        cfg = self._companion_config()
        if cfg is None:
            return
        current = self._companion_fallback_raw()
        if provider_id:
            current[key] = provider_id
        else:
            current.pop(key, None)
        encoded = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
        _flat_set_existing(cfg, self.FALLBACK_CONFIG_KEY, encoded)

    def _companion_runtime_fallback(self) -> dict[str, str]:
        """陪伴插件实例属性里的 model_fallback_overrides（运行时真正生效的值）。

        陪伴插件 WebUI 的 settings 快照优先取实例属性，所以我们改完配置后
        必须把这个属性也同步，否则"配置已改、页面还显示旧的"。
        """
        star = self._companion_star()
        if star is None:
            return {}
        plugin_obj = getattr(star, "star_cls", None) or getattr(star, "instance", None) or star
        raw = getattr(plugin_obj, "model_fallback_overrides", None)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for raw_key, raw_provider_id in raw.items():
            key = str(raw_key or "").strip()
            pid = str(raw_provider_id or "").strip()
            if key and pid:
                out[key] = pid
        return out

    def _sync_companion_runtime(self) -> None:
        """把刚写进 config 的值同步到陪伴插件的运行时实例属性。

        陪伴插件在 bootstrap 时把 model_fallback_overrides、fast_response_provider_id
        等一次性读到实例属性，之后：
        - 主模型：其 WebUI 直接 _config_get(key) 读配置，所以我们改 config 即刻可见；
        - 备用模型：走 _runtime_settings 的
          getattr(self.plugin, "model_fallback_overrides", self._config_get(key))，
          实例属性存在就优先返回它 —— 只 save_config() 写文件不会刷新实例属性，
          陪伴插件面板/运行逻辑会继续用旧值（就是"备用模型没替换"的根因）。
        所以这里必须显式同步：备用模型 setattr 归一化后的 dict，主模型调其
        _apply_quick_provider_defaults()。
        """
        star = self._companion_star()
        if star is None:
            return
        plugin_obj = getattr(star, "star_cls", None) or getattr(star, "instance", None) or star
        cfg = self._companion_config()
        if cfg is None:
            return

        # ---- 备用模型：model_fallback_overrides（实例属性必须是 dict）----
        try:
            raw = _flat_get(cfg, self.FALLBACK_CONFIG_KEY, "")
        except Exception:
            raw = ""
        normalized: dict[str, str] = {}
        normalizer = getattr(plugin_obj, "_normalize_model_fallback_overrides", None)
        if callable(normalizer):
            try:
                result = normalizer(raw)
                if isinstance(result, dict):
                    normalized = result
            except Exception as e:
                logger.warning(f"[ModelPanel] 归一化 model_fallback_overrides 失败: {e}")
        if not normalized:
            normalized = self._companion_fallback_raw()
        try:
            setattr(plugin_obj, "model_fallback_overrides", normalized)
            logger.info(f"[ModelPanel] 已同步陪伴插件实例属性 model_fallback_overrides: {normalized}")
        except Exception as e:
            logger.warning(f"[ModelPanel] 同步 model_fallback_overrides 失败: {e}")

        # ---- 主模型：fast_response_provider_id 等实例属性 ----
        apply = getattr(plugin_obj, "_apply_quick_provider_defaults", None)
        if callable(apply):
            try:
                apply()
                logger.info("[ModelPanel] 已调 _apply_quick_provider_defaults 同步实例属性")
            except Exception as e:
                logger.warning(f"[ModelPanel] _apply_quick_provider_defaults 调用失败: {e}")
        else:
            logger.warning("[ModelPanel] 陪伴插件未找到 _apply_quick_provider_defaults 方法，可能需要重启插件才能生效")

    def _default_provider_id_sync(self) -> str:
        """同步兜底：读配置里的 default_provider_id。"""
        try:
            pm = self.context.provider_manager
            ps = getattr(pm, "provider_settings", None) or {}
            return str(ps.get("default_provider_id") or "")
        except Exception:
            return ""

    async def _default_provider_id(self) -> str:
        """返回 AstrBot 实际生效的默认 chat 模型 provider id。

        使用 AstrBot 官方 get_using_provider_async(CHAT_COMPLETION) 决议，
        与 AstrBot 运行时"实际用哪个模型"完全一致（优先配置 default_provider_id，
        校验 provider 是否已加载）。避免读已弃用的 curr_provider_inst（不随
        配置页改动更新）或 SharedPreferences 残留的 curr_provider。
        """
        try:
            pm = self.context.provider_manager
            provider = await pm.get_using_provider_async(ProviderType.CHAT_COMPLETION)
            if provider is not None:
                cfg = getattr(provider, "provider_config", None)
                if isinstance(cfg, dict):
                    pid = str(cfg.get("id") or "").strip()
                    if pid:
                        return pid
        except Exception:
            pass
        return self._default_provider_id_sync()

    # ---------------- 默认模型配置（对话 / 回退 / 图片转述） ----------------
    def _astrbot_config(self):
        """返回 AstrBot 主配置（AstrBotConfig，dict 子类，含 save_config()）。"""
        try:
            return self.context.get_config()
        except Exception as e:
            logger.warning(f"[ModelPanel] 读取 AstrBot 主配置失败: {e}")
            return None

    def _provider_ids(self) -> list:
        """所有已加载 provider 的 id，用于校验写入值是否真实存在。"""
        ids = []
        for p in self._chat_providers():
            try:
                pid = str(self._provider_display(p).get("id") or "").strip()
            except Exception:
                pid = ""
            if pid and pid not in ids:
                ids.append(pid)
        return ids

    def _ids_from_legacy_fallback(self, raw: list, known) -> list:
        """把旧配置 fallback_chat_models（model 名或 provider id）映射成 provider id。"""
        model_map = {}
        for p in self._chat_providers():
            try:
                d = self._provider_display(p)
            except Exception:
                continue
            pid = str(d.get("id") or "").strip()
            model = str(d.get("model") or "").strip()
            if pid and model:
                model_map.setdefault(model, pid)
        ids = []
        for v in raw:
            s = str(v).strip()
            if not s:
                continue
            pid = s if s in known else model_map.get(s, "")
            if pid and pid not in ids:
                ids.append(pid)
        return ids

    def _default_model_state(self, known=None) -> dict:
        """读取 默认对话模型 / 回退对话模型列表 / 默认图片转述模型。

        新版（Agent Runner，config_version>=3）：
            agent_runner.config.model.provider_id
            agent_runner.config.model.fallback_provider_ids
        旧版（legacy）：
            provider_settings.default_provider_id
            provider_settings.fallback_chat_models
        图片转述两版一致：provider_settings.default_image_caption_provider_id
        """
        if known is None:
            known = set(self._provider_ids())
        chat_id = ""
        fallback_ids: list = []
        vision_id = ""
        runner_type = ""
        new_style = False
        cfg = self._astrbot_config()
        if isinstance(cfg, dict):
            ps = cfg.get("provider_settings")
            ps = ps if isinstance(ps, dict) else {}
            ar = cfg.get("agent_runner")
            if isinstance(ar, dict) and isinstance(ar.get("config"), dict):
                runner_type = str(ar.get("runner_type") or "")
                mc = ar["config"].get("model")
                if isinstance(mc, dict):
                    new_style = True
                    chat_id = str(mc.get("provider_id") or "").strip()
                    raw = mc.get("fallback_provider_ids")
                    if isinstance(raw, list):
                        fallback_ids = [str(x).strip() for x in raw if str(x).strip()]
            if not chat_id:
                chat_id = str(ps.get("default_provider_id") or "").strip()
            if not fallback_ids:
                raw = ps.get("fallback_chat_models")
                if isinstance(raw, list):
                    fallback_ids = self._ids_from_legacy_fallback(raw, known)
            vision_id = str(ps.get("default_image_caption_provider_id") or "").strip()
        return {
            "chat_provider_id": chat_id,
            "fallback_provider_ids": fallback_ids,
            "vision_provider_id": vision_id,
            "runner_type": runner_type,
            "new_style": new_style,
        }

    def _set_chat_provider_id(self, cfg, pid: str) -> bool:
        """写入默认对话模型：新版 agent_runner 与旧键同时写，保证两个版本都生效。"""
        changed = False
        ps = cfg.get("provider_settings")
        if not isinstance(ps, dict):
            ps = {}
            cfg["provider_settings"] = ps
        ar = cfg.get("agent_runner")
        if isinstance(ar, dict) and isinstance(ar.get("config"), dict):
            mc = ar["config"].get("model")
            if not isinstance(mc, dict):
                mc = {}
                ar["config"]["model"] = mc
            if str(mc.get("provider_id") or "") != pid:
                mc["provider_id"] = pid
                changed = True
        if str(ps.get("default_provider_id") or "") != pid:
            ps["default_provider_id"] = pid
            changed = True
        return changed

    def _set_fallback_ids(self, cfg, ids: list) -> bool:
        """写入回退对话模型列表（有序 provider id），新旧键同时写。"""
        changed = False
        ps = cfg.get("provider_settings")
        if not isinstance(ps, dict):
            ps = {}
            cfg["provider_settings"] = ps
        ar = cfg.get("agent_runner")
        if isinstance(ar, dict) and isinstance(ar.get("config"), dict):
            mc = ar["config"].get("model")
            if not isinstance(mc, dict):
                mc = {}
                ar["config"]["model"] = mc
            if [str(x) for x in (mc.get("fallback_provider_ids") or [])] != ids:
                mc["fallback_provider_ids"] = list(ids)
                changed = True
        if [str(x) for x in (ps.get("fallback_chat_models") or [])] != ids:
            ps["fallback_chat_models"] = list(ids)
            changed = True
        return changed

    def _sync_default_chat_runtime(self, chat_id: str) -> None:
        """同步 provider_manager 的 default_chat_provider_id。

        该属性在 provider 加载时快照一次，改配置不会自动刷新；虽然每次请求
        会重新解析配置，但显式同步可让依赖该属性的逻辑立刻生效。
        """
        try:
            pm = self.context.provider_manager
            if pm is not None and hasattr(pm, "default_chat_provider_id"):
                pm.default_chat_provider_id = chat_id
        except Exception as e:
            logger.warning(f"[ModelPanel] 同步默认对话模型运行时失败: {e}")

    # ---------------- API ----------------
    async def api_overview(self) -> dict:
        providers = self._chat_providers()
        displays = [self._provider_display(p) for p in providers]
        ids = {d["id"] for d in displays}
        default_id = await self._default_provider_id()
        # 默认模型的友好展示名（供应商 · 模型），避免前端只显示裸 provider id
        default_label = ""
        for d in displays:
            if d.get("id") == default_id:
                default_label = " · ".join(
                    [x for x in (d.get("name") or "", d.get("model") or "") if x]
                )
                break
        stats: dict = {}
        latest_results: dict = {}
        try:
            if self.storage:
                stats = await self.storage.session_stats()
                latest_results = await self.storage.latest_per_provider()
        except Exception as e:
            logger.warning(f"[ModelPanel] overview 统计失败: {e}")
        usage = None
        try:
            if self.storage:
                usage = await self.storage.usage_stats(days=7, top_models=5)
        except Exception as e:
            logger.warning(f"[ModelPanel] 用量统计失败: {e}")
        # 调用次数统计：与「实时监测」同一份来源（llm_calls，逐次埋点），
        # 覆盖所有调用 —— AstrBot 后台的「提供商测试」、WebChat、其它插件直调都算。
        # 下面的 history 只统计「检测」（probe），两件事不能混在一个数里。
        calls_totals: dict = {}
        try:
            if self.storage:
                calls_totals = await self.storage.calls_totals()
        except Exception as e:
            logger.warning(f"[ModelPanel] 调用次数统计失败: {e}")
        # 花费是「用量 × 人工填的单价/倍率」，没填价的模型估不出来，
        # 所以总额只含有价模型；unpriced_models 让前端能说明「这不是全量」。
        cost = (await self._cost_rollup(displays)).get("totals") or {}
        return {
            "total": len(providers),
            "default_provider_id": default_id,
            "default_label": default_label,
            "default_set": bool(default_id and default_id in ids),
            "companion_loaded": self._companion_star() is not None,
            "companion_provider_count": sum(1 for v in self._companion_provider_values().values() if v),
            "history": stats,
            "latest_results": latest_results,
            "usage": usage,
            "cost": cost,
            "calls": calls_totals,
        }

    async def api_list_providers(self) -> dict:
        providers = self._chat_providers()
        default_id = await self._default_provider_id()
        items = []
        seen_models: dict[str, dict] = {}  # model -> {id, name}
        for p in providers:
            d = self._provider_display(p)
            d["is_default"] = bool(d["id"] and d["id"] == default_id)
            items.append(d)
            model = d.get("model") or ""
            if model and model not in seen_models:
                seen_models[model] = {"id": d["id"], "name": d.get("name") or ""}
        # models: 去重后的纯 model 名列表（兼容旧前端）
        models = list(seen_models.keys())
        # provider_models: 全量 {model, vendor, id} 列表，每个 provider 一条、不按 model 去重。
        # 新前端下拉用 vendor · model 作 label、provider id 作 value，
        # 因为陪伴插件 config 里存的是 provider id，必须写回 provider id 才能匹配上。
        # 不同 provider 渠道可能配置同一个 model（同名模型）：
        # 这里若按 model 去重，同名模型的其它渠道会在陪伴插件页下拉里消失。
        provider_models = [
            {"id": d["id"], "model": d.get("model") or "", "vendor": d.get("name") or ""}
            for d in items
            if d.get("model")
        ]
        logger.info(
            f"[ModelPanel] /panel/providers 返回 {len(items)} 个 provider, "
            f"{len(provider_models)} 个模型选项, 默认={default_id or '无'}"
        )
        return {
            "items": items,
            "models": models,
            "provider_models": provider_models,
            "default_provider_id": default_id,
        }

    # ---------- 插件配置（检测参数 + 卡片外观） ----------
    # 面板设置页只管这几个键；其余配置（告警阈值、定时探测…）走 AstrBot 的插件配置页。
    _CONFIG_DEFAULTS = {
        "test_timeout": 45,
        "test_retry_count": 1,
        "test_retry_backoff": 2.0,
        "history_retention_days": 30,
        "card_theme": "rose",
        "card_font_path": "",
    }
    _CONFIG_TYPES = {
        "test_timeout": int,
        "test_retry_count": int,
        "test_retry_backoff": float,
        "history_retention_days": int,
        "card_theme": str,
        "card_font_path": str,
    }

    async def api_get_config(self) -> dict:
        """读取插件 _conf_schema 暴露的字段（含默认值），返回当前生效值。"""
        cfg = getattr(self, "config", None)
        values = dict(self._CONFIG_DEFAULTS)
        if cfg is not None and hasattr(cfg, "get"):
            for k, default in self._CONFIG_DEFAULTS.items():
                v = cfg.get(k)
                if v is None:
                    continue
                try:
                    values[k] = type(default)(v)
                except (TypeError, ValueError):
                    values[k] = default
        # 主题候选跟着配置一起下发：主题表的唯一来源是 card_render.THEME_CHOICES，
        # 前端再抄一份的话，以后加主题就会出现「下拉里没有、后台也选不到」
        return {"items": values,
                "themes": [{"value": k, "label": label} for k, label in THEME_CHOICES]}

    async def api_set_config(self) -> dict:
        """批量设置插件配置字段。仅更新传入的 key，缺省保留原值。"""
        payload = await self._json_payload()
        raw = payload.get("items")
        if not isinstance(raw, dict):
            return {"ok": False, "error": "items 必须是对象 {key: value}"}
        cfg = getattr(self, "config", None)
        if cfg is None or not hasattr(cfg, "__setitem__"):
            return {"ok": False, "error": "插件配置不可写（self.config 缺失）"}
        try:
            for k, v in raw.items():
                cast = self._CONFIG_TYPES.get(k)
                if cast is None:
                    continue
                casted = cast(v)
                if k == "test_timeout":
                    casted = max(1, casted)
                elif k == "test_retry_count":
                    casted = max(0, casted)
                elif k == "test_retry_backoff":
                    casted = max(0.0, casted)
                elif k == "history_retention_days":
                    casted = max(0, casted)
                elif k == "card_theme":
                    # 不认识的主题名照收（渲染器会回落到默认），但空串也归一成默认名，
                    # 免得配置页出现一个空下拉、看着像没配上
                    casted = str(casted or "").strip().lower() or DEFAULT_THEME
                elif k == "card_font_path":
                    casted = str(casted or "").strip()
                cfg[k] = casted
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
            logger.info(f"[ModelPanel] 写入插件配置: {raw}")
            return {"ok": True}
        except Exception as e:
            logger.warning(f"[ModelPanel] 写入插件配置失败: {e}")
            return {"ok": False, "error": str(e)}

    # ---------- 检测勾选偏好 ----------
    async def api_get_preferences(self) -> dict:
        try:
            if not self.storage:
                return {"items": {}}
            prefs = await self.storage.get_detection_preferences()
            return {"items": prefs}
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/preferences GET 失败: {e}")
            return {"items": {}, "error": str(e)}

    async def api_set_preferences(self) -> dict:
        """批量设置用户对 provider 的勾选偏好。

        body: { "items": { provider_id: true|false } }
        不传 provider_id 视为默认勾选（即从前端传完整的当前集合）。
        """
        payload = await self._json_payload()
        raw = payload.get("items")
        if not isinstance(raw, dict):
            return {"ok": False, "error": "items 必须是对象 {provider_id: bool}"}
        prefs: dict[str, bool] = {}
        for k, v in raw.items():
            if not k:
                continue
            prefs[str(k)] = bool(v)
        try:
            if self.storage:
                await self.storage.set_detection_preferences(prefs)
            return {"ok": True, "count": len(prefs)}
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/preferences PUT 失败: {e}")
            return {"ok": False, "error": str(e)}

    async def _skip_ids(self, payload: dict) -> set[str]:
        """本次检测需要跳过的 provider id 集合。

        前端会显式传 skip；但「分组一键测试」历史上只传 ids、不传 skip，
        导致分组内未勾选的模型也被真实检测（既浪费额度又违反"勾选=参与检测"
        的约定）。这里以 storage 里的检测开关偏好做兜底合并，
        保证「没勾选就不测」在任何调用路径下都成立（前端也同步修了）。
        """
        skip = {
            str(x).strip() for x in (payload.get("skip") or []) if str(x).strip()
        }
        try:
            if self.storage:
                prefs = await self.storage.get_detection_preferences()
                for pid, enabled in prefs.items():
                    if not enabled:
                        skip.add(str(pid))
        except Exception as e:
            logger.warning(f"[ModelPanel] 读取检测开关偏好失败: {e}")
        return skip

    async def api_test_provider(self) -> dict:
        payload = await self._json_payload()
        provider_id = str(payload.get("id") or "").strip()
        cfg = self._test_config()
        timeout = float(payload.get("timeout") or cfg["test_timeout"])
        for p in self._chat_providers():
            d = self._provider_display(p)
            if d["id"] == provider_id:
                result = await self._test_one(
                    p, timeout, cfg, (await self._probe_modes()).get(provider_id, "non_stream"))
                result.update({
                    "id": provider_id,
                    "name": d["name"],
                    "model": d["model"],
                    "checked_at": int(time.time()),
                })
                try:
                    if self.storage:
                        session = await self.storage.create_session("single", 1)
                        await self.storage.insert_result(session["id"], result)
                        await self.storage.finish_session(
                            session["id"],
                            ok_count=1 if result.get("ok") else 0,
                            fail_count=0 if result.get("ok") else 1,
                            skip_count=0,
                        )
                except Exception as e:
                    logger.warning(f"[ModelPanel] 写历史失败: {e}")
                return result
        return {
            "id": provider_id,
            "name": "",
            "model": "",
            "ok": False,
            "latency_ms": None,
            "error_code": "not_found",
            "error": "provider not found",
            "retry_count": 0,
        }

    async def api_test_all_providers(self) -> dict:
        """同步版本：一次性跑完所有模型再返回。兼容老前端。"""
        if self.sessions.is_busy():
            return {"ok": False, "error": "已有检测任务在执行，请稍后再试", "items": []}
        if self._test_all_lock.locked():
            return {"ok": False, "error": "已有检测任务在执行，请稍后再试", "items": []}
        async with self._test_all_lock:
            payload = await self._json_payload()
            skip = await self._skip_ids(payload)
            cfg = self._test_config()
            timeout = float(payload.get("timeout") or cfg["test_timeout"])
            providers = self._chat_providers()
            modes = await self._probe_modes()
            results: list[dict] = []
            session_id: Optional[int] = None
            for p in providers:
                d = self._provider_display(p)
                if d["id"] in skip:
                    results.append({
                        "id": d["id"],
                        "name": d["name"],
                        "model": d["model"],
                        "skipped": True,
                        "ok": False,
                        "latency_ms": None,
                        "error_code": "skipped",
                        "error": "",
                        "retry_count": 0,
                        "checked_at": int(time.time()),
                    })
                    continue
                r = await self._test_one(p, timeout, cfg, modes.get(d["id"], "non_stream"))
                r.update({
                    "id": d["id"],
                    "name": d["name"],
                    "model": d["model"],
                    "skipped": False,
                    "checked_at": int(time.time()),
                })
                results.append(r)
            try:
                if self.storage:
                    session = await self.storage.create_session("all", len(providers))
                    session_id = session["id"]
                    ok_n = sum(1 for x in results if x.get("ok"))
                    fail_n = sum(1 for x in results if not x.get("ok") and not x.get("skipped"))
                    skip_n = sum(1 for x in results if x.get("skipped"))
                    for r in results:
                        await self.storage.insert_result(session_id, r)
                    await self.storage.finish_session(session_id, ok_n, fail_n, skip_n)
            except Exception as e:
                logger.warning(f"[ModelPanel] 写历史失败: {e}")
            return {"items": results, "total": len(providers), "session_id": session_id}

    async def api_test_all_stream(self) -> dict:
        """异步任务版：立即返回 session_id，由前端轮询 /session/{id} 拿进度。

        为什么不用真 SSE：AstrBot 插件 Page 桥接层只支持 JSON 同步调用。
        异步任务 + 轮询既能给前端"边跑边显示"的体验，又能稳定运行。
        """
        if self.sessions.is_busy():
            return {"ok": False, "error": "已有检测任务在执行，请稍后再试", "session_id": None}
        if self._test_all_lock.locked():
            return {"ok": False, "error": "已有检测任务在执行，请稍后再试", "session_id": None}
        payload = await self._json_payload()
        skip = await self._skip_ids(payload)
        cfg = self._test_config()
        timeout = float(payload.get("timeout") or cfg["test_timeout"])
        providers = self._chat_providers()
        # 可选：只测指定的 provider 子集（分组一键测试用）。
        # 注意：ids 只负责"缩小范围"，跳过的判定仍由 _skip_ids（skip ∪ 未勾选偏好）
        # 在 _run_stream 里逐项生效 —— 分组内未勾选的模型同样不会被检测。
        only_ids = set(str(x).strip() for x in (payload.get("ids") or []) if str(x).strip())
        if only_ids:
            providers = [p for p in providers if str(self._provider_display(p)["id"]) in only_ids]
        # 创建持久会话记录（同时返回 id 给前端轮询）
        session_db_id: Optional[int] = None
        if self.storage:
            try:
                s = await self.storage.create_session("stream", len(providers))
                session_db_id = s["id"]
            except Exception as e:
                logger.warning(f"[ModelPanel] stream 创建 session 失败: {e}")
        if session_db_id is None:
            # storage 不可用时给个虚拟 id，session_manager 不依赖 SQLite
            session_db_id = int(time.time() * 1000) % 1_000_000_000
        await self.sessions.create(session_db_id, len(providers))
        # 在后台启动任务跑检测；不 await 以便立即返回
        asyncio.create_task(
            self._run_stream(session_db_id, providers, skip, timeout, cfg)
        )
        return {"ok": True, "session_id": session_db_id, "total": len(providers)}

    async def _run_stream(
        self,
        session_db_id: int,
        providers: list[Any],
        skip: set[str],
        timeout: float,
        cfg: dict,
    ) -> None:
        modes = await self._probe_modes()
        async with self._test_all_lock:
            ok_n = fail_n = skip_n = 0
            try:
                for p in providers:
                    d = self._provider_display(p)
                    item: dict
                    if d["id"] in skip:
                        item = {
                            "id": d["id"],
                            "name": d["name"],
                            "model": d["model"],
                            "ok": False,
                            "latency_ms": None,
                            "error_code": "skipped",
                            "error": "",
                            "retry_count": 0,
                            "skipped": True,
                            "checked_at": int(time.time()),
                        }
                        skip_n += 1
                    else:
                        r = await self._test_one(p, timeout, cfg, modes.get(d["id"], "non_stream"))
                        item = {
                            "id": d["id"],
                            "name": d["name"],
                            "model": d["model"],
                            "ok": r["ok"],
                            "latency_ms": r["latency_ms"],
                            "error_code": r.get("error_code") or "",
                            "error": r.get("error") or "",
                            "retry_count": r.get("retry_count") or 0,
                            "skipped": False,
                            "checked_at": int(time.time()),
                        }
                        if item["ok"]:
                            ok_n += 1
                        else:
                            fail_n += 1
                    # 推进内存 session（前端轮询可见）
                    self.sessions.append_item(session_db_id, item)
                    # 写 SQLite 历史
                    if self.storage:
                        try:
                            await self.storage.insert_result(session_db_id, item)
                        except Exception as e:
                            logger.warning(f"[ModelPanel] stream 写历史失败: {e}")
                if self.storage:
                    try:
                        await self.storage.finish_session(session_db_id, ok_n, fail_n, skip_n)
                    except Exception as e:
                        logger.warning(f"[ModelPanel] stream finish_session 失败: {e}")
                self.sessions.finish(session_db_id, error=None)
                logger.info(
                    f"[ModelPanel] 一键检测完成 session={session_db_id} "
                    f"ok={ok_n} fail={fail_n} skip={skip_n}"
                )
            except Exception as e:
                logger.error(f"[ModelPanel] 一键检测异常: {e}", exc_info=True)
                self.sessions.finish(session_db_id, error=str(e)[:200])

    async def api_session_state(self, session_id: Any) -> dict:
        # AstrBot Page 桥接层对 `<int:name>` 类型的 converter 匹配不了（只支持
        # `<name>` / `<path:name>`），故路由用 `<session_id>` 传入字符串，这里转 int。
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            return {"error": "invalid session_id", "session_id": str(session_id)}
        st = self.sessions.get(session_id)
        if st is None:
            return {"error": "session not found", "session_id": session_id}
        return st.snapshot()

    async def api_test_results(self) -> dict:
        """每个 provider 的最新一次检测结果。"""
        try:
            if not self.storage:
                return {"items": {}}
            items = await self.storage.latest_per_provider()
            return {"items": items}
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/providers/results 失败: {e}")
            return {"items": {}, "error": str(e)}

    async def api_test_history(self) -> dict:
        """最近若干条历史记录，按时间倒序。"""
        try:
            if not self.storage:
                return {"items": [], "stats": {}}
            limit = 50
            try:
                limit_raw = request.args.get("limit")
                if limit_raw:
                    limit = max(1, min(500, int(limit_raw)))
            except Exception:
                pass
            import aiosqlite
            async with aiosqlite.connect(self.storage.db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    """SELECT * FROM model_test_results ORDER BY checked_at DESC, id DESC LIMIT ?""",
                    (limit,),
                )
                rows = await cur.fetchall()
            items = [
                {
                    "id": r["provider_id"],
                    "name": r["provider_name"] or "",
                    "model": r["provider_model"] or "",
                    "ok": bool(r["ok"]),
                    "latency_ms": r["latency_ms"],
                    "error_code": r["error_code"] or "",
                    "error": r["error_message"] or "",
                    "retry_count": int(r["retry_count"] or 0),
                    "checked_at": int(r["checked_at"] or 0),
                    "session_id": int(r["session_id"] or 0),
                }
                for r in rows
            ]
            stats = await self.storage.session_stats()
            latest_session = await self.storage.latest_session()
            return {"items": items, "stats": stats, "latest_session": latest_session}
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/providers/history 失败: {e}")
            return {"items": [], "stats": {}, "error": str(e)}

    # ================= 模型档案 / 检测范围 / 实时监测 =================
    def _free_expiry(self, profile: dict) -> tuple[Optional[int], bool]:
        """算限时免费的剩余天数。返回 (剩余天数或 None, 是否三天内到期)。

        到期日目前只做展示与提示，不自动改计费类型——「到期后到底转付费还是继续免费」
        得人来判断，猜错会让后面的成本统计整体失真。
        """
        raw = (profile or {}).get("free_until")
        try:
            until = int(raw) if raw else 0
        except (TypeError, ValueError):
            return None, False
        if until <= 0:
            return None, False
        left = int((until - time.time()) // 86400)
        return left, left <= 3

    _PRICE_FIELDS = ("price_input_per_m", "price_output_per_m",
                     "price_cached_per_m", "price_per_call")

    async def _cost_rollup(self, displays: list[dict]) -> dict:
        """把 llm_usage 的原始量按档案单价折算成金额：逐模型、逐供应商、全局。

        金额一律是**估算**：token 数来自 provider 上报的 usage，不上报的就没有；
        单价与倍率靠人工填。所以估不出来时返回 None，前端显示 –，
        绝不能显示 0.00 —— 那会被读成「这个模型真没花钱」。

        每日次数限制是供应商级的，所以「今日已用次数」也按供应商汇总。
        次数来自逐次埋点（llm_calls，含 AstrBot 后台的提供商测试与 WebChat），
        **不含检测**（探测走自己的表、埋点对它抑制）—— 供应商那边的计数器通常是
        两者一起算的，所以这里显示的仍可能比它小，这是口径差异不是漏记。
        """
        empty = {"by_provider": {}, "by_vendor": {}, "totals": {}}
        if not self.storage:
            return empty
        try:
            today_u = await self.storage.usage_aggregate(days=1)
            week_u = await self.storage.usage_aggregate(days=7)
            profiles = await self.storage.get_all_profiles()
            vendors = await self.storage.get_all_vendor_profiles()
            # 分组「今日次数」的来源（v1.4.1 起）：逐次埋点，而不是 llm_usage。
            # llm_usage 由 on_llm_response 写、流式分片直接跳过 —— 流式模型调 10 次
            # 可能只记两三次，于是这里显示的数字与实时监测对不上，也没有成败可分。
            day_calls = await self.storage.calls_day_by_provider()
        except Exception as e:
            logger.warning(f"[ModelPanel] 花费统计失败: {e}")
            return empty

        vendor_of = {str(d.get("id") or ""): str(d.get("name") or "").strip() for d in displays}
        # 用量表里可能有已删除 provider 的孤儿行，也要计入全局总额，
        # 否则「今日花费」会悄悄比真实支出少一截
        pids = set(vendor_of) | set(today_u) | set(week_u) | set(day_calls)
        by_provider: dict[str, dict] = {}
        by_vendor: dict[str, dict] = {}
        tot_today = tot_week = 0.0
        priced = 0
        for pid in pids:
            vname = vendor_of.get(pid) or "未归组供应商"
            prof = profiles.get(pid) or {}
            pricing = resolve_pricing(prof, vendors.get(vname))
            prices = {f: prof.get(f) for f in self._PRICE_FIELDS}
            btype = str(prof.get("billing_type") or "unknown")
            c_today = estimate_cost(btype, prices, today_u.get(pid) or {}, pricing["multiplier"])
            c_week = estimate_cost(btype, prices, week_u.get(pid) or {}, pricing["multiplier"])
            dc = day_calls.get(pid) or {}
            by_provider[pid] = {
                "currency": pricing["currency"],
                "multiplier": pricing["multiplier"],
                "multiplier_from_group": pricing["multiplier_from_group"],
                "today": c_today,
                "week": c_week,
                # 今日次数与成败：逐次埋点（llm_calls），口径与实时监测一致。
                # 不含检测（探测走自己的表、且埋点对它抑制），供应商后台若把探测
                # 也算进配额，这里的数字会比它小 —— 这是口径差异，不是漏记。
                "calls_today": int(dc.get("total") or 0),
                "calls_ok": int(dc.get("ok") or 0),
                "calls_fail": int(dc.get("fail") or 0),
                "calls_aborted": int(dc.get("aborted") or 0),
                "billable": c_week is not None or c_today is not None,
            }
            v = by_vendor.setdefault(vname, {
                "name": vname,
                "currency": pricing["currency"],
                "rate_multiplier": (vendors.get(vname) or {}).get("rate_multiplier"),
                "daily_call_limit": pricing["daily_call_limit"],
                "note": (vendors.get(vname) or {}).get("note") or "",
                "calls_today": 0,
                "calls_ok": 0,
                "calls_fail": 0,
                "calls_aborted": 0,
                "today": 0.0,
                "week": 0.0,
                "priced": False,
            })
            v["calls_today"] += by_provider[pid]["calls_today"]
            v["calls_ok"] += by_provider[pid]["calls_ok"]
            v["calls_fail"] += by_provider[pid]["calls_fail"]
            v["calls_aborted"] += by_provider[pid]["calls_aborted"]
            if c_today is not None:
                v["today"] += c_today
                tot_today += c_today
                v["priced"] = True
                priced += 1
            if c_week is not None:
                v["week"] += c_week
                tot_week += c_week
        for v in by_vendor.values():
            v["today"] = round(v["today"], 4)
            v["week"] = round(v["week"], 4)
            limit = v.get("daily_call_limit")
            v["limit_ratio"] = (round(v["calls_today"] / limit, 4) if limit else None)
        return {
            "by_provider": by_provider,
            "by_vendor": by_vendor,
            "totals": {"today": round(tot_today, 4), "week": round(tot_week, 4),
                       "priced_models": priced, "unpriced_models": len(pids) - priced},
        }

    async def _health_view(self, days: float = 7.0) -> dict:
        """把五份数据拼成一张视图表：档案 / 三通道范围 / 最近探测 / 核心对话监测 / 逐次调用。

        刻意不合并成一个「平均延迟」：探测测的是空载非流式往返，监测记录的是真实对话
        首字与整轮耗时，数量级不同、语义不同，混起来统计就会骗人
        （口径详见 docs/模型监测与配置规划.md 第五节）。
        """
        now = int(time.time())
        profiles: dict = {}
        scopes: dict = {}
        mstates: dict = {}
        alerts: list = []
        if self.storage:
            try:
                profiles = await self.storage.get_all_profiles()
                scopes = await self.storage.get_detect_scopes()
                mstates = await self.storage.get_model_states()
                alerts = await self.storage.open_alerts()
            except Exception as e:
                logger.warning(f"[ModelPanel] 读取档案/范围/探测历史失败: {e}")
        live_available = self.live_stats.available
        truncated = False
        records: list = []
        probe_rows: list = []
        if live_available:
            records, truncated = await self.live_stats.fetch_window(days=days)
        if self.storage:
            try:
                probe_rows = await self.storage.probe_window(days=days)
            except Exception as e:
                logger.warning(f"[ModelPanel] 读取探测明细失败: {e}")
        # 逐次调用：本插件自己包的 provider.text_chat / text_chat_stream。
        # 这是唯一能看见「主模型失败但被备用模型救回来」的数据源，
        # 核心 provider_stats 在那种情况下把整轮记到了备用模型头上。
        calls: dict = {}
        if self.storage:
            try:
                calls = await self.storage.calls_stats(int(now - days * 86400))
            except Exception as e:
                logger.warning(f"[ModelPanel] 读取逐次调用统计失败: {e}")
        # 原始行也给台账一份：llm_calls 的覆盖面比 provider_stats 广
        # （AstrBot 后台的「提供商测试」、WebChat、其它插件直调 provider 都只在这里），
        # 有它的数据时次数与「最后一次调用」就该以它为准。
        call_rows: list = []
        if self.storage:
            try:
                call_rows = await self.storage.calls_window(int(now - days * 86400))
            except Exception as e:
                logger.warning(f"[ModelPanel] 读取逐次调用明细失败: {e}")
        ledger = _merge_ledger(records, probe_rows, call_rows)
        default_id = await self._default_provider_id()
        displays = []
        for p in self._chat_providers():
            try:
                displays.append(self._provider_display(p))
            except Exception:
                continue
        costs = await self._cost_rollup(displays)
        cost_of = costs.get("by_provider") or {}
        items = []
        for d in displays:
            pid = d["id"]
            prof = profiles.get(pid) or {}
            scope = scopes.get(pid) or {
                "manual": True, "scheduled": False, "command": True,
                "billing_type": "unknown", "explicit": {},
            }
            entry = ledger.get(pid) or {}
            w = entry.get("window") or _empty_window()
            last = entry.get("last")
            ms = mstates.get(pid) or {}
            state, reason = _derive_state(w, {"ok": (last or {}).get("ok"),
                                              "error_code": (last or {}).get("error_code") or ""}
                                          if last else {})
            # 巡检状态机优先：它带连续失败计数与静音语义，而 _derive_state 只是按需现算的
            # 近似值。两边各给一个结论而不统一的话，面板显示的和告警发出去的会各说各话。
            persisted = str(ms.get("state") or "")
            if persisted and persisted != "unknown":
                state = persisted
                if not reason and int(ms.get("consecutive_fail") or 0) > 0:
                    reason = f"连续失败 {int(ms['consecutive_fail'])} 次"
            left, expiring = self._free_expiry(prof)
            muted_until = int(ms.get("muted_until") or 0)
            items.append({
                "id": pid,
                "name": d["name"],
                "model": d["model"],
                "display_model": d["display_model"],
                "is_default": bool(pid and pid == default_id),
                "billing": {
                    "type": str(prof.get("billing_type") or "unknown"),
                    "free_until": prof.get("free_until"),
                    "free_days_left": left,
                    "free_expiring": expiring,
                    "currency": prof.get("currency") or "",
                    "price_input_per_m": prof.get("price_input_per_m"),
                    "price_output_per_m": prof.get("price_output_per_m"),
                    "price_cached_per_m": prof.get("price_cached_per_m"),
                    "price_per_call": prof.get("price_per_call"),
                    # 模型级倍率原样回显（NULL = 跟随分组），effective 值在 cost.multiplier 里
                    "rate_multiplier": prof.get("rate_multiplier"),
                    "channel_kind": str(prof.get("channel_kind") or "unknown"),
                    "role": str(prof.get("role") or "unknown"),
                    "supports_streaming": str(prof.get("supports_streaming") or "unknown"),
                    "probe_mode": str(prof.get("probe_mode") or "non_stream"),
                    "note": prof.get("note") or "",
                },
                "cost": cost_of.get(pid) or {},
                "calls": calls.get(pid) or {},
                "scope": {
                    "manual": bool(scope.get("manual")),
                    "scheduled": bool(scope.get("scheduled")),
                    "command": bool(scope.get("command")),
                    "explicit": scope.get("explicit") or {},
                },
                "window": w,
                "last": last,
                "state": state,
                "reason": reason,
                "muted": muted_until > now,
                "muted_until": muted_until,
                "consecutive_fail": int(ms.get("consecutive_fail") or 0),
            })
        counts = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}
        for it in items:
            counts[it["state"]] = counts.get(it["state"], 0) + 1
        label_of = {str(it.get("id")): str(it.get("display_model") or it.get("id") or "") for it in items}
        open_alerts = [
            {
                "provider_id": str(a.get("provider_id") or ""),
                "name": label_of.get(str(a.get("provider_id") or ""), str(a.get("provider_id") or "")),
                "kind": str(a.get("kind") or ""),
                "detail": redact(a.get("detail")),
                "opened_at": int(a.get("opened_at") or 0),
                # notified_at 为空表示这条还没送达过，正在等下一轮补发
                "pending": not int(a.get("notified_at") or 0),
            }
            for a in alerts
        ]
        return {
            "items": items,
            "counts": counts,
            "days": days,
            "vendors": costs.get("by_vendor") or {},
            "cost_totals": costs.get("totals") or {},
            "live_available": live_available,
            # 逐次埋点是否已经在跑。没装上时前端要说明「逐次列是空的」而不是让表格骗人
            "calls_available": bool(calls) or (await self._has_calls()),
            "truncated": truncated,
            "samples": sum(int((it.get("window") or {}).get("total") or 0) for it in items),
            "alerts": open_alerts,
            "muted_count": sum(1 for it in items if it.get("muted")),
            "enums": {
                "billing_type": list(BILLING_TYPES),
                "channel_kind": list(CHANNEL_KINDS),
                "role": list(ROLES),
                "supports_streaming": list(STREAM_FLAGS),
                "probe_mode": list(PROBE_MODES),
                "scheduled_default_on": list(SCHEDULED_DEFAULT_ON),
            },
        }

    async def api_health(self) -> dict:
        """GET /panel/health：监测页与档案页共用的数据源。

        ``days`` 接受 ``today``（本地零点到现在）或数字；缺省为今天 ——
        看监测的人第一问题永远是「现在/今天怎么样」，默认给 7 天会把今天的故障摊平掉。
        """
        try:
            days = _parse_days(request.args.get("days"))
        except Exception:
            days = _today_days()
        try:
            return await self._health_view(days)
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/health 失败: {e}")
            return {"items": [], "counts": {}, "live_available": False, "error": str(e)}

    async def api_vendor_set(self) -> dict:
        """POST /panel/vendor：保存一个供应商分组（倍率 / 每日限额 / 币种 / 备注）。

        分组档案只按供应商名存，键不是 provider_id。同样拒绝野名字，
        否则改一次供应商源 ID 就会在表里留下一堆没人看的旧分组。
        """
        payload = await self._json_payload()
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "缺少分组名"}
        patch = payload.get("patch")
        if not isinstance(patch, dict):
            patch = {k: v for k, v in payload.items() if k in VENDOR_FIELDS}
        if not patch:
            return {"ok": False, "error": "没有可保存的字段"}
        unknown = [k for k in patch if k not in VENDOR_FIELDS]
        if unknown:
            return {"ok": False, "error": f"未知字段: {', '.join(unknown)}"}
        known = {str(self._provider_display(p).get("name") or "") for p in self._chat_providers()}
        if known and name not in known:
            return {"ok": False, "error": "该供应商分组当前不存在"}
        if not self.storage:
            return {"ok": False, "error": "存储未就绪"}
        try:
            saved = await self.storage.upsert_vendor_profile(name, patch)
            return {"ok": True, "vendor": saved}
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/vendor 保存失败: {e}")
            return {"ok": False, "error": str(e)}

    async def api_profile_set(self) -> dict:
        """POST /panel/profile：保存一个 provider 的档案补丁。

        body 两种写法都收：``{id, patch:{...}}`` 或把字段直接平铺在顶层。
        """
        payload = await self._json_payload()
        pid = str(payload.get("id") or payload.get("provider_id") or "").strip()
        if not pid:
            return {"ok": False, "error": "缺少 id"}
        patch = payload.get("patch")
        if not isinstance(patch, dict):
            patch = {k: v for k, v in payload.items() if k in PROFILE_FIELDS}
        if not patch:
            return {"ok": False, "error": "没有可保存的字段"}
        unknown = [k for k in patch if k not in PROFILE_FIELDS]
        if unknown:
            return {"ok": False, "error": f"未知字段: {', '.join(unknown)}"}
        known = set(self._provider_ids())
        if known and pid not in known:
            # 不接受野 id，否则档案表会被历史脏数据撑大
            return {"ok": False, "error": "provider 不存在或已删除"}
        if not self.storage:
            return {"ok": False, "error": "存储未就绪"}
        try:
            saved = await self.storage.upsert_profile(pid, patch)
            return {"ok": True, "profile": saved}
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/profile 保存失败: {e}")
            return {"ok": False, "error": str(e)}

    async def api_scope_set(self) -> dict:
        """POST /panel/scope：设置某 provider 在某通道的参与情况。

        body: ``{id, channel: manual|scheduled|command, enabled: true|false|null}``，
        ``enabled=null`` 表示取消显式设置、回到跟随计费类型推导的默认值。
        """
        payload = await self._json_payload()
        pid = str(payload.get("id") or payload.get("provider_id") or "").strip()
        channel = str(payload.get("channel") or "").strip()
        if not pid:
            return {"ok": False, "error": "缺少 id"}
        if channel not in DETECT_CHANNELS:
            return {"ok": False, "error": f"channel 只能是 {'/'.join(DETECT_CHANNELS)}"}
        enabled = payload.get("enabled")
        if enabled is not None:
            enabled = bool(enabled)
        if not self.storage:
            return {"ok": False, "error": "存储未就绪"}
        try:
            await self.storage.set_detect_scope(pid, channel, enabled)
            scopes = await self.storage.get_detect_scopes()
            return {"ok": True, "scope": scopes.get(pid) or {}}
        except Exception as e:
            logger.warning(f"[ModelPanel] /panel/scope 失败: {e}")
            return {"ok": False, "error": str(e)}

    async def api_default_model(self) -> dict:
        return {"default_provider_id": await self._default_provider_id()}

    async def api_default_model_config(self) -> dict:
        """默认模型配置：对话模型 / 回退列表 / 图片转述模型 + 可选模型列表。"""
        known = set(self._provider_ids())
        state = self._default_model_state(known)
        items = []
        for p in self._chat_providers():
            try:
                d = self._provider_display(p)
            except Exception:
                continue
            pid = str(d.get("id") or "").strip()
            if not pid:
                continue
            items.append(
                {
                    "id": pid,
                    "name": str(d.get("name") or ""),
                    "model": str(d.get("model") or ""),
                    "type": str(d.get("type") or ""),
                }
            )
        effective_id = await self._default_provider_id()
        logger.info(
            f"[ModelPanel] /panel/default_model/config: 对话={state['chat_provider_id'] or '无'}, "
            f"回退={len(state['fallback_provider_ids'])} 个, "
            f"图片转述={state['vision_provider_id'] or '无'}, 可选模型 {len(items)} 个"
        )
        return {
            "ok": True,
            "items": items,
            "effective_chat_provider_id": effective_id,
            **state,
        }

    async def api_default_model_set(self) -> dict:
        """保存默认模型配置。只处理传入的字段，缺省字段保持原值。

        body: {
          "chat_provider_id": str,        # 空字符串 = 清除（由 AstrBot 选第一个）
          "fallback_provider_ids": [str], # 有序，空数组 = 清空
          "vision_provider_id": str,      # 空字符串 = 不使用图片转述
        }
        """
        payload = await self._json_payload()
        cfg = self._astrbot_config()
        if not isinstance(cfg, dict):
            return {"ok": False, "error": "无法读取 AstrBot 主配置"}
        known = set(self._provider_ids())
        changed: list = []

        def _pid(v) -> str:
            return str(v or "").strip()

        if "chat_provider_id" in payload:
            pid = _pid(payload.get("chat_provider_id"))
            if pid and known and pid not in known:
                return {"ok": False, "error": f"未知的对话模型: {pid}"}
            if self._set_chat_provider_id(cfg, pid):
                changed.append("chat_provider_id")

        if "fallback_provider_ids" in payload:
            raw = payload.get("fallback_provider_ids")
            if raw is not None and not isinstance(raw, list):
                return {"ok": False, "error": "fallback_provider_ids 必须为数组"}
            ids: list = []
            for x in raw or []:
                pid = _pid(x)
                if not pid:
                    continue
                if known and pid not in known:
                    return {"ok": False, "error": f"未知的回退模型: {pid}"}
                if pid not in ids:
                    ids.append(pid)
            if self._set_fallback_ids(cfg, ids):
                changed.append("fallback_provider_ids")

        if "vision_provider_id" in payload:
            pid = _pid(payload.get("vision_provider_id"))
            if pid and known and pid not in known:
                return {"ok": False, "error": f"未知的图片转述模型: {pid}"}
            ps = cfg.get("provider_settings")
            if not isinstance(ps, dict):
                ps = {}
                cfg["provider_settings"] = ps
            if str(ps.get("default_image_caption_provider_id") or "").strip() != pid:
                ps["default_image_caption_provider_id"] = pid
                changed.append("vision_provider_id")

        state = self._default_model_state(known)
        if not changed:
            return {"ok": True, "changed": [], "no_change": True, **state}
        try:
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
        except Exception as e:
            logger.warning(f"[ModelPanel] 保存默认模型配置失败: {e}")
            return {"ok": False, "error": f"保存失败: {e}", "changed": changed}
        # provider_manager 的 default_chat_provider_id 是加载时快照，显式同步让改动立刻可见
        self._sync_default_chat_runtime(state.get("chat_provider_id", ""))
        logger.info(f"[ModelPanel] /panel/default_model/set: 已更新 {changed}")
        return {"ok": True, "changed": changed, **state}

    async def api_companion_providers(self) -> dict:
        cfg = self._companion_config()
        if cfg is None:
            logger.warning("[ModelPanel] /panel/companion/providers: 未找到陪伴插件配置")
            return {
                "loaded": False,
                "items": [],
                "config_mode": "",
                "configured_count": 0,
                "total_keys": len(COMPANION_PROVIDER_KEYS),
            }
        values = self._companion_provider_values()
        # 备用模型：{key: 备用 provider_id}
        fallback_values = self._companion_fallback_values()
        # provider id -> model 名映射，供前端"按模型名匹配替换"使用
        model_map = self._provider_model_map()
        items = []
        configured = 0
        for key, value in values.items():
            provider_id = str(value).strip()
            is_configured = bool(provider_id)
            if is_configured:
                configured += 1
            # value 展示/匹配用 model 名（陪伴插件 config 里存的是 provider id，
            # 这里转成 model 名给前端显示与替换匹配）；provider_id 保留原始值。
            model = model_map.get(provider_id, provider_id)
            items.append({
                "key": key,
                "kind": "main",
                "value": model,
                "provider_id": provider_id,
                "configured": is_configured,
                "label": COMPANION_KEY_LABELS.get(key, key),
            })
            # 备用模型条目（仅当该 key 配置了备用 provider）
            fb_id = fallback_values.get(key, "")
            if fb_id:
                fb_model = model_map.get(fb_id, fb_id)
                items.append({
                    "key": key,
                    "kind": "fallback",
                    "value": fb_model,
                    "provider_id": fb_id,
                    "configured": True,
                    "label": COMPANION_KEY_LABELS.get(key, key),
                })
        config_mode = ""
        try:
            config_mode = str(_flat_get(cfg, "provider_config_mode") or "")
        except Exception:
            config_mode = ""
        # 陪伴插件运行时（实例属性）实际生效的备用模型。与配置不一致说明
        # 陪伴插件内存里还是旧值（例如用旧版本替换过、或用户直接改了配置文件）。
        # 自愈：把 config 的真实值推给运行时实例属性（不写配置，只刷新缓存），
        # 这样打开本页就能纠正它的 WebUI 与运行逻辑，不必等重启/重载。
        config_fallback = self._companion_fallback_raw()
        runtime_in_sync = self._companion_runtime_fallback() == config_fallback
        if not runtime_in_sync:
            logger.warning(
                "[ModelPanel] 陪伴插件运行时备用模型与配置不一致，自动同步: "
                f"运行时={self._companion_runtime_fallback()}, 配置={config_fallback}"
            )
            self._sync_companion_runtime()
            runtime_in_sync = self._companion_runtime_fallback() == config_fallback
        logger.info(
            f"[ModelPanel] /panel/companion/providers: 共 {len(items)} 项, "
            f"已配置 {configured} 项, 模式={config_mode or '无'}, "
            f"备用模型运行时一致={runtime_in_sync}"
        )
        # 总览：每个 key 一行，含主模型 + 备用模型的 provider id 与展示名，
        # 供前端"总览"页直接罗列所有用途与当前模型，并支持未配置项直接下拉配置。
        summary = []
        for key, value in values.items():
            main_id = str(value).strip()
            main_model = model_map.get(main_id, main_id) if main_id else ""
            fb_id = fallback_values.get(key, "")
            fb_model = model_map.get(fb_id, fb_id) if fb_id else ""
            summary.append({
                "key": key,
                "label": COMPANION_KEY_LABELS.get(key, key),
                "main_provider_id": main_id,
                "main_model": main_model,
                "fallback_provider_id": fb_id,
                "fallback_model": fb_model,
                "configured": bool(main_id),
            })

        return {
            "loaded": True,
            "items": items,
            "summary": summary,
            "config_mode": config_mode,
            "configured_count": configured,
            "total_keys": len(COMPANION_PROVIDER_KEYS),
            "runtime_in_sync": runtime_in_sync,
        }

    async def api_companion_replace(self) -> dict:
        payload = await self._json_payload()
        replacements = payload.get("replacements") or []
        if not isinstance(replacements, list) or not replacements:
            return {"ok": False, "error": "replacements 不能为空"}
        cfg = self._companion_config()
        if cfg is None:
            return {"ok": False, "error": f"未找到插件 {COMPANION_PLUGIN_NAME}"}
        # 按模型名匹配替换：old 是"当前 model 名"，new 是"目标 provider id"。
        # 陪伴插件 config 存的是 provider id，故先把每个 key 的当前值翻译成
        # model 名来与 old 比较，命中后写回 provider id（new）。
        # kind: "main" 替换主模型（model_assignment_config[key]），
        #       "fallback" 替换备用模型（model_fallback_overrides[key]）。
        model_map = self._provider_model_map()
        fallback_values = self._companion_fallback_values()
        changed: list[dict] = []
        # kind 语义：
        #   "main"     只替换主模型位置（model_assignment_config[key]）
        #   "fallback" 只替换备用模型位置（model_fallback_overrides[key]）
        #   "any"/"all" 同名模型无论主备都替换（精简配置）
        for item in replacements:
            old = str(item.get("old") or "").strip()
            new = str(item.get("replacement") or "").strip()
            kind = str(item.get("kind") or "any").strip().lower()
            if not old or not new:
                continue
            for key in COMPANION_PROVIDER_KEYS:
                main_cur = str(_flat_get(cfg, key) or "").strip()
                fb_cur = fallback_values.get(key, "")
                if kind == "fallback":
                    if not fb_cur or fb_cur == new:
                        continue
                    if model_map.get(fb_cur, fb_cur) != old and fb_cur != old:
                        continue
                    self._set_companion_fallback(key, new)
                    fallback_values[key] = new
                    changed.append({"key": key, "kind": "fallback", "old": old, "new": new})
                    continue
                if kind == "any" or kind == "all":
                    hit = False
                    # 主模型位置
                    if main_cur and main_cur != new and (
                        model_map.get(main_cur, main_cur) == old or main_cur == old
                    ):
                        # 同步写扁平 + schema 分组（model_assignment_config），
                        # 否则陪伴插件读分组旧值、我们读扁平新值，出现"自欺欺人"。
                        _flat_set(cfg, key, new)
                        changed.append({"key": key, "kind": "main", "old": old, "new": new})
                        hit = True
                    # 备用模型位置
                    if fb_cur and fb_cur != new and (
                        model_map.get(fb_cur, fb_cur) == old or fb_cur == old
                    ):
                        self._set_companion_fallback(key, new)
                        fallback_values[key] = new
                        changed.append({"key": key, "kind": "fallback", "old": old, "new": new})
                        hit = True
                    if not hit:
                        continue
                    continue
                # 主模型（kind == "main" 或默认）
                if not main_cur or main_cur == new:
                    continue
                # 当前值是 provider id -> 转成 model 名再和 old 比；
                # 若 old 本身就是 provider id（旧前端/手动输入），也直接匹配。
                cur_model = model_map.get(main_cur, main_cur)
                if cur_model != old and main_cur != old:
                    continue
                # 同步写扁平 + schema 分组（model_assignment_config），
                # 否则陪伴插件读分组旧值、我们读扁平新值，出现"自欺欺人"。
                _flat_set(cfg, key, new)
                changed.append({"key": key, "kind": "main", "old": old, "new": new})
        try:
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
        except Exception as e:
            logger.warning(f"[ModelPanel] 保存陪伴插件配置失败: {e}")
            return {"ok": False, "error": f"保存失败: {e}", "changed": changed}
        # 把新 config 值同步到陪伴插件的运行时实例属性（关键：备用模型若不在这里
        # 同步，陪伴插件 WebUI/运行逻辑会继续用 bootstrap 时的旧值）。
        self._sync_companion_runtime()
        main_count = sum(1 for c in changed if c.get("kind") != "fallback")
        fb_count = sum(1 for c in changed if c.get("kind") == "fallback")
        logger.info(
            f"[ModelPanel] /panel/companion/replace: 替换 {len(changed)} 处"
            f"（主模型 {main_count} / 备用 {fb_count}）"
        )
        return {
            "ok": True,
            "changed_count": len(changed),
            "changed": changed,
            "main_count": main_count,
            "fallback_count": fb_count,
        }

    async def api_companion_set(self) -> dict:
        """直接设置某个 provider key 的模型（主模型 / 备用模型），无需按模型名匹配。

        用于"总览"页：未配置项也能直接在下拉里选模型并保存。
        - kind="main"：写回主模型位置（扁平 + model_assignment_config 分组同步）；
        - kind="fallback"：写回 model_fallback_overrides（顶层 legacy flat key）；
        - provider_id 为空字符串表示清除（置为未配置）。
        """
        payload = await self._json_payload()
        items = payload.get("items") or []
        if not isinstance(items, list):
            return {"ok": False, "error": "items 必须为数组"}
        cfg = self._companion_config()
        if cfg is None:
            return {"ok": False, "error": f"未找到插件 {COMPANION_PLUGIN_NAME}"}
        changed = []
        for it in items:
            if not isinstance(it, dict):
                continue
            key = str(it.get("key") or "").strip()
            if key not in COMPANION_PROVIDER_KEYS:
                continue
            pid = str(it.get("provider_id") or "").strip()
            kind = str(it.get("kind") or "main").strip().lower()
            if kind == "fallback":
                self._set_companion_fallback(key, pid)
                changed.append({"key": key, "kind": "fallback", "provider_id": pid})
            else:
                _flat_set(cfg, key, pid)
                changed.append({"key": key, "kind": "main", "provider_id": pid})
        try:
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
        except Exception as e:
            logger.warning(f"[ModelPanel] 保存陪伴插件配置失败: {e}")
            return {"ok": False, "error": f"保存失败: {e}", "changed": changed}
        # 同步运行时实例属性，保证陪伴插件页面/运行逻辑立刻生效
        self._sync_companion_runtime()
        main_count = sum(1 for c in changed if c.get("kind") != "fallback")
        fb_count = sum(1 for c in changed if c.get("kind") == "fallback")
        logger.info(
            f"[ModelPanel] /panel/companion/set: 设置 {len(changed)} 处"
            f"（主模型 {main_count} / 备用 {fb_count}）"
        )
        return {
            "ok": True,
            "changed_count": len(changed),
            "changed": changed,
            "main_count": main_count,
            "fallback_count": fb_count,
        }

    # ---------------- 全插件模型配置（扫描 / 改写） ----------------
    def _scanner(self) -> PluginModelScanner:
        """惰性创建扫描器，并把 companion 专用读写语义注入进去复用。

        陪伴插件把 provider key 同时写在顶层扁平副本与 model_assignment_config
        分组里，只有 _flat_set 语义能保证两处一起改；这个函数是 main.py 与
        plugin_models.py 之间的唯一耦合点。
        """
        if self._plugin_scanner is None:
            self._plugin_scanner = PluginModelScanner(
                self,
                flat_set=_flat_set,
                flat_set_existing=_flat_set_existing,
            )
        return self._plugin_scanner

    async def api_plugin_models(self) -> dict:
        """扫描所有已安装插件，列出其中的模型相关配置。

        只读接口；识别规则（schema `_special` / 值命中 provider id 或模型名 /
        键名提示）见 plugin_models.py 模块文档。
        """
        try:
            return self._scanner().scan()
        except Exception as e:
            logger.error(f"[ModelPanel] 插件模型扫描失败: {e}", exc_info=True)
            return {
                "ok": False,
                "error": str(e),
                "providers": [],
                "plugins": [],
                "stats": {},
            }

    async def api_plugin_models_set(self) -> dict:
        """按 (plugin, path) 精确改写插件配置里的模型。

        body: {
          "items": [
            {"plugin": "astrbot_plugin_x", "path": ["a","b"], "value": "provider_id"}
          ]
        }
        - path 支持字符串键与整数下标（列表元素）；
        - JSON 字符串映射（如陪伴插件 model_fallback_overrides）传 container 指定容器路径；
        - value 为空字符串表示清除该处配置。
        """
        payload = await self._json_payload()
        items = payload.get("items")
        try:
            return self._scanner().set_values(items)
        except Exception as e:
            logger.error(f"[ModelPanel] 插件模型写入失败: {e}", exc_info=True)
            return {"ok": False, "error": str(e), "changed": []}

    # ---------------- 内部 ----------------
    async def _json_payload(self) -> dict:
        try:
            return await request.get_json(silent=True) or {}
        except Exception:
            return {}

    async def _test_one(self, provider: Any, timeout: float, cfg: dict, mode: str = "non_stream") -> dict:
        """检测单个模型，支持重试 + 错误归一化。

        Args:
            mode: ``non_stream``（默认，走核心 provider.test()）/
                ``stream``（流式，测首字延迟）/ ``both``。

        Returns:
            ``{ok, latency_ms, ttft_ms, error_code, error, retry_count}``
        """
        retry_count = max(0, int(cfg.get("test_retry_count") or 0))
        backoff = max(0.0, float(cfg.get("test_retry_backoff") or 0.0))
        attempts = retry_count + 1  # 至少一次
        last: dict = {"ok": False, "latency_ms": None, "ttft_ms": None,
                      "error_code": "unknown", "error": ""}
        for i in range(attempts):
            r = await self._probe_once(provider, timeout, mode)
            if r["ok"]:
                r["retry_count"] = i
                return r
            last = r
            if i < attempts - 1 and backoff > 0:
                try:
                    await asyncio.sleep(backoff * (i + 1))
                except Exception:
                    pass
        last["retry_count"] = max(0, attempts - 1)
        return last

    async def _probe_once(self, provider: Any, timeout: float, mode: str) -> dict:
        """跑一次探测。``both`` 模式下非流式与流式都必须成功才算成功。"""
        base = {"ok": False, "latency_ms": None, "ttft_ms": None, "error_code": "", "error": ""}
        ns_ok: Optional[bool] = None
        if mode in ("non_stream", "both"):
            ns = await self._probe_non_stream(provider, timeout)
            ns_ok = bool(ns["ok"])
            base.update({"latency_ms": ns.get("latency_ms"), "error_code": ns.get("error_code", ""),
                         "error": ns.get("error", "")})
            if not ns_ok:
                return base
            if mode == "non_stream":
                base["ok"] = True
                return base
        if mode in ("stream", "both"):
            st = await self._probe_stream(provider, timeout)
            st_ok = bool(st.get("ok"))
            base["ttft_ms"] = st.get("ttft_ms")
            # 用 ns_ok 这个显式局部量判断，不能读 base["ok"]——它初始就是 False，
            # 写成 `st_ok and base.get("ok", True)` 会让 stream / both 两种模式永远失败。
            base["ok"] = st_ok and (True if ns_ok is None else ns_ok)
            if not st_ok:
                # 流式失败不覆盖非流式已拿到的 latency_ms，但整体算失败
                base["error_code"] = st.get("error_code") or "stream"
                base["error"] = st.get("error") or "stream probe failed"
                return base
            if mode == "stream":
                base["latency_ms"] = st.get("latency_ms")
            else:
                # 两路都通了，清掉中途可能留下的错误文案
                base["error_code"] = ""
                base["error"] = ""
        return base

    async def _probe_non_stream(self, provider: Any, timeout: float) -> dict:
        """核心 provider.test()：一句 PONG 的非流式往返。"""
        start = time.monotonic()
        try:
            await asyncio.wait_for(provider.test(timeout=timeout), timeout=timeout + 5)
            return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000, 1),
                    "error_code": "", "error": ""}
        except asyncio.TimeoutError:
            code, msg = _normalize_error("timeout")
        except Exception as e:
            code, msg = _normalize_error(e)
        return {"ok": False, "latency_ms": None, "error_code": code, "error": msg}

    async def _probe_stream(self, provider: Any, timeout: float) -> dict:
        """流式探测：测首字延迟，并覆盖「只有流式才挂」的网关故障。

        TTFT 的口径刻意与核心一致——**第一个 ``is_chunk`` 分片的到达耗时**
        （见 ``tool_loop_agent_runner`` 记 ``time_to_first_token`` 的写法），
        这样探测值和 provider_stats 里的真实对话值才放在同一把尺子上。

        拿到首字就走，不等生成结束：核心在流末尾还会再 yield 一次完整结果，
        继续读等于让模型把整段话讲完，白花钱。
        而且必须显式 ``aclose()``——异步生成器的收尾靠 GC 回调，不保证时机，
        只靠 break 会让底层 HTTP 连接悬着不释放；这条链路每几分钟就跑一轮 × N 个模型，
        漏一次就攒一个僵尸连接。
        """
        start = time.monotonic()
        gen = None
        try:
            # 探测结果已经写进 model_test_results 了；不在这里关掉逐次埋点的话，
            # 每轮探测会再往 llm_calls 记一行，今日调用次数和成本都会虚高。
            with suppress_call_recording():
                gen = provider.text_chat_stream(prompt=_STREAM_PROBE_PROMPT)
            if not hasattr(gen, "__anext__"):
                # 个别实现不是异步生成器（返回协程），这里不猜，直接判不可用。
                # 注意协程的 close() 是同步方法，await 它反而会抛 TypeError。
                close = getattr(gen, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                return {"ok": False, "latency_ms": None, "ttft_ms": None,
                        "error_code": "stream_unsupported", "error": "not an async generator"}

            async def consume():
                ttft = None
                chunks = 0
                async for resp in gen:
                    if getattr(resp, "is_chunk", False):
                        if ttft is None:
                            ttft = (time.monotonic() - start) * 1000.0
                        chunks += 1
                        if chunks >= _STREAM_PROBE_CHUNKS:
                            break
                    elif ttft is None:
                        # 不发 chunk、直接给完整结果的 provider：不算流式失败，
                        # 但首字延迟确实没测到，别记成 0ms 骗人说模型很快
                        ttft = (time.monotonic() - start) * 1000.0
                        break
                return ttft, chunks

            ttft, chunks = await asyncio.wait_for(consume(), timeout=timeout + 5)
            total = round((time.monotonic() - start) * 1000, 1)
            if ttft is None:
                return {"ok": False, "latency_ms": total, "ttft_ms": None,
                        "error_code": "empty_stream",
                        "error": f"stream opened but produced no chunks ({chunks})"}
            return {"ok": True, "latency_ms": total, "ttft_ms": round(ttft, 1),
                    "error_code": "", "error": ""}
        except asyncio.TimeoutError:
            code, msg = _normalize_error("timeout")
        except Exception as e:
            code, msg = _normalize_error(e)
        finally:
            # 三条退出路径（正常 break / 超时 / 抛异常）都必须关流
            aclose = getattr(gen, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    pass
        return {"ok": False, "latency_ms": None, "ttft_ms": None, "error_code": code, "error": msg}
    # ================= 卡片输出与查询指令 =================
    _BILLING_LABELS = {
        "unknown": "",
        "free": "免费",
        "temp_free": "限时免费",
        "trial": "试用额度",
        "paid_overage": "额度+超额付费",
        "paid": "付费",
        "subscription": "订阅内含",
    }

    def _billing_label(self, billing: dict) -> str:
        """计费摘要文本，限时免费的剩余天数写在里面。"""
        btype = str(billing.get("type") or "unknown")
        label = self._BILLING_LABELS.get(btype, "")
        left = billing.get("free_days_left")
        if btype == "temp_free" and left is not None:
            label = f"{label} {'已到期' if left < 0 else f'剩 {left} 天'}" if label else ""
        return label

    def _row_note(self, item: dict) -> str:
        """总览卡的行副标题只留给「需要解释的话」。

        这张卡要列**全部**模型，每多一行装饰就多一分不可读，所以角色、计费、备注
        这些档案信息都不进来，只有故障原因、静音、限时免费到期才值得占第二行。
        「默认模型」也不在这里 —— 它已经由行内那个胶囊标签表达（更显眼，且不占第二行）。
        """
        parts = []
        billing = item.get("billing") or {}
        if str(billing.get("type") or "") == "temp_free":
            bl = self._billing_label(billing)
            if bl:
                parts.append(bl)
        if item.get("muted"):
            parts.append("告警静音中")
        if str(item.get("state") or "") in ("down", "degraded") and item.get("reason"):
            parts.append(str(item["reason"]))
        return " · ".join(parts)

    def _row_sub(self, item: dict) -> str:
        """行副标题：角色 · 计费 · 备注 · （窗口内无调用时的）最近一次结果。"""
        billing = item.get("billing") or {}
        w = item.get("window") or {}
        last = item.get("last") or {}
        parts = []
        role = str(billing.get("role") or "unknown")
        if role not in ("unknown", ""):
            parts.append({"primary": "主力", "backup": "备用", "fallback": "兜底",
                          "dedicated": "专用", "watch": "观察", "retired": "弃用"}.get(role, role))
        bl = self._billing_label(billing)
        if bl:
            parts.append(bl)
        if billing.get("note"):
            parts.append(str(billing["note"])[:24])
        if not int(w.get("counted") or 0) and last:
            if last.get("ok"):
                verdict = f"最近 {_fmt_ms(last.get('latency_ms'))}"
            else:
                verdict = f"最近失败 {_error_label(last.get('error_code'))}"
            parts.append(verdict.strip())
        if item.get("is_default"):
            parts.insert(0, "默认模型")
        if not parts and item.get("reason"):
            parts.append(str(item["reason"]))
        return " · ".join(p for p in parts if p)

    def _command_scope(self, view: dict) -> dict:
        """把视图裁到「指令检测」通道开放的模型 —— 聊天里能看到的就该是指令里能测的。

        两边对不齐会让人困惑：卡片列了 12 个模型，`/检测模型` 却说其中 9 个没开放通道。
        顶部计数也跟着重算，否则汇总说「故障 2」而列表里只剩一个故障模型。
        """
        items = [it for it in (view.get("items") or []) if (it.get("scope") or {}).get("command")]
        counts = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}
        for it in items:
            counts[it.get("state") or "unknown"] = counts.get(it.get("state") or "unknown", 0) + 1
        out = dict(view)
        out["items"] = items
        out["counts"] = counts
        out["samples"] = sum(int((it.get("window") or {}).get("total") or 0) for it in items)
        return out

    def _sorted_vendor_groups(self, items: list) -> list:
        """按供应商分组、组内按模型名排序 —— **一律按名字**，不按状态。

        旧版把「组内最坏状态」浮到最上面：看着聪明，但每次刷新顺序都可能变，
        而这张卡现在是「先看序号、再回数字」的入口 —— 顺序稳定比故障优先重要得多。
        """
        by_vendor: dict[str, list] = {}
        for it in items:
            by_vendor.setdefault(str(it.get("name") or "未归组供应商"), []).append(it)
        return [
            (vendor, sorted(by_vendor[vendor],
                            key=lambda it: _name_sort_key(it.get("model")
                                                          or it.get("display_model")
                                                          or it.get("id"))))
            for vendor in sorted(by_vendor, key=_name_sort_key)
        ]

    def _card_row_for(self, item: dict, index: int, detailed: bool = False,
                      probe: Optional[dict] = None) -> dict:
        """单个模型 → 卡片行（含右对齐数值列）。

        数值口径与面板一致：有逐次埋点就以它为准 —— 核心 provider_stats 在
        「主模型失败、备用救回来」时会把整轮记到备用模型头上，按它算就报 100% 成功。
        """
        w = item.get("window") or {}
        last = item.get("last") or {}
        c = item.get("calls") or {}
        c_counted = int(c.get("counted") or 0)
        lat_ts = 0
        if c_counted:
            rate = c.get("fail_rate")
            cl = c.get("last") or {}
            lat = cl.get("ttft_ms") or cl.get("latency_ms") or c.get("avg_latency_ms")
            ok_flag = bool(cl.get("ok"))
            fail_n, total_n = int(c.get("fail") or 0), c_counted
            lat_ts = int(cl.get("ts") or 0)
        else:
            rate = w.get("fail_rate")
            lat = last.get("ttft_ms") or last.get("latency_ms")
            ok_flag = bool(last.get("ok")) if last else True
            fail_n = int(w.get("fail") or 0)
            total_n = int(w.get("counted") or 0)
            lat_ts = int(last.get("ts") or 0)
        row = {
            "index": index,
            "state": str(item.get("state") or "unknown"),
            "label": str(item.get("model") or item.get("display_model")
                         or item.get("id") or "(未知)"),
            # 「默认」做成行内胶囊而不是第二行小字：切模型时最要紧的就是认清当前那个
            "tag": "默认" if item.get("is_default") else ("静音" if item.get("muted") else ""),
            "highlight": bool(item.get("is_default")),
        }
        if probe is not None:
            # 刚跑完一次检测时：行底状态是**对面状态机现在的判定**，而这个标签是
            # 「刚刚这一轮过没过」——两件事都要说，而且必须分得清（用户提过
            # 「红的却有毫秒、成功率 2.3%，看不出最新这次到底过没过」）。
            ok = bool(probe.get("ok"))
            row["probe_label"] = "本次通过" if ok else "本次失败"
            row["probe_tone"] = "ok" if ok else "bad"
        # 「更新于」列：延迟/成功率都是**历史**数据的统计值，光给数字看不出新鲜度。
        # 真实对话和主动探测都会刷新它，所以这一列回答的是「这行数字是什么时候的」。
        # 取的时间戳必须**跟着延迟数字的来源**走（逐次埋点优先，其次合并台账），
        # 否则会出现「延迟取自 A、时间取自 B」这种对不上的组合。
        # 一条记录都没有时给 "-"，而不是「刚刚」那种「看着很新其实没数据」的假象。
        fresh = _fmt_ago(lat_ts or int((last or {}).get("ts") or 0))
        if detailed:
            row["note"] = self._row_sub(item)
            row["cells"] = [
                _fmt_ms(c.get("avg_ttft_ms") if c_counted else w.get("avg_ttft_ms")),
                _fmt_ms(w.get("p95_ttft_ms")),
                _fmt_ms(c.get("avg_latency_ms") if c_counted else w.get("avg_latency_ms")),
                _fmt_success(rate) if total_n else "-",
                f"{fail_n}/{total_n}",
                fresh,
            ]
            return row
        note = self._row_note(item)
        # 失败次数直接写进副标题：这正是旧版看不见的那个数
        if fail_n:
            note = (f"失败 {fail_n} 次" + (f" · {note}" if note else ""))
        elif not ok_flag and last:
            note = (f"最近一次失败 · {note}" if note else "最近一次失败")
        row["note"] = note
        row["cells"] = [
            _fmt_ms(lat) if (lat or c_counted or last) else "-",
            _fmt_success(rate) if total_n else "-",
            fresh,
        ]
        return row

    def _grouped_card_rows(self, view: dict, *, numbered: bool = True,
                           detailed: bool = False,
                           probe_of: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
        """生成「卡片行表」与「选单选项表」——两者**共用同一份序号**。

        Returns:
            ``(rows, options)``：``rows`` 直接喂 ``render_card``；
            ``options`` 喂 ``_picker_arm``，且 ``options[i]["index"] == i + 1``。
            模型行与分组行在同一个数字空间里连续编号（回组号 = 选整组）。
        """
        rows: list[dict] = []
        options: list[dict] = []
        n = 0
        for vendor, group in self._sorted_vendor_groups(list(view.get("items") or [])):
            n += 1
            rows.append({"kind": "group", "index": n, "label": vendor,
                         "note": f"{len(group)} 个模型"})
            options.append({
                "index": n, "group": True, "label": vendor, "state": "",
                "pids": [str(it.get("id")) for it in group if it.get("id")],
            })
            for it in group:
                n += 1
                rows.append(self._card_row_for(
                    it, n, detailed,
                    (probe_of or {}).get(str(it.get("id") or ""))))
                options.append({
                    "index": n, "group": False,
                    "label": str(it.get("model") or it.get("display_model")
                                 or it.get("id") or ""),
                    "state": str(it.get("state") or "unknown"),
                    "pids": [str(it.get("id"))] if it.get("id") else [],
                })
        if not numbered:
            for r in rows:
                r.pop("index", None)
        return rows, options

    def _counts_stats(self, view: dict) -> list[dict]:
        """头部那行汇总点：正常 / 降级 / 故障 / 无数据。"""
        counts = view.get("counts") or {}
        return [
            {"label": "正常", "value": str(int(counts.get("healthy") or 0)), "state": "healthy"},
            {"label": "降级", "value": str(int(counts.get("degraded") or 0)), "state": "degraded"},
            {"label": "故障", "value": str(int(counts.get("down") or 0)), "state": "down"},
            {"label": "无数据", "value": str(int(counts.get("unknown") or 0)), "state": "unknown"},
        ]

    def _card_meta(self, scope: str = "今天", extra: str = "") -> str:
        """头部副信息：统计范围 · 附加说明 · 统计时刻。

        时间戳放头部而不是脚注：脚注现在是「怎么用这张卡」的提示位，
        把口径和「回几」挤在一行会两边都读不清。
        """
        return " · ".join(p for p in (scope, extra, time.strftime("%m-%d %H:%M")) if p)

    async def _card_png(self, *, title: str, badge: str = "", stats: Optional[list] = None,
                        columns: Optional[list] = None, rows: Optional[list] = None,
                        notes: Optional[list] = None, headline: str = "", meta: str = "",
                        numbered: bool = False, width: int = 0,
                        tone: str = "") -> Optional[bytes]:
        """渲染卡片。返回 PNG bytes；字体不可用时返回 None，调用方降级发文本。

        Pillow 是 CPU 密集的，必须 ``to_thread`` —— 直接在事件循环里画会卡住整条消息管线。
        字体路径与主题色都从插件配置读：``card_font_path`` / ``card_theme``，
        主题名不认识时渲染器自己回落到默认主题（配置写错不该让卡片画不出来）。

        ``tone``（``error`` / ``alert`` / ``ok``）会**强制**换成固定主题（见 ``TONE_THEMES``）：
        报错红、告警橙、恢复绿 —— 与用户的主题偏好无关。区分一张卡是「提醒」还是「出错了」，
        靠的该是颜色本身，而不是让人去读小字。
        """
        cfg = getattr(self, "config", None)
        font = str(cfg.get("card_font_path") or "") if hasattr(cfg, "get") else ""
        theme = str(cfg.get("card_theme") or "") if hasattr(cfg, "get") else ""
        forced = TONE_THEMES.get(str(tone or "").strip().lower(), "")
        return await asyncio.to_thread(
            render_card, title, badge, stats or [], columns or [], rows or [], notes or [],
            font, headline=headline, meta=meta, numbered=numbered, width=width,
            theme=forced or theme,
        )

    def _rows_text(self, title: str, rows: list, *, stats: Optional[list] = None,
                   notes: Optional[list] = None, headline: str = "") -> str:
        """没有可用中文字体时的纯文本降级。丑，但比发一张豆腐块图或报错好。

        序号照旧带上：用户要回的就是它，图片发不出来时更不能丢。
        """
        head = str(title or "")
        if headline:
            head += f"｜{headline}"
        if stats:
            head += "  " + " ".join(f"{s.get('label')}{s.get('value')}" for s in stats)
        lines = [head]
        for r in rows:
            if r.get("kind") == "group":
                lines.append(f"— {r.get('label')} —")
                continue
            idx = f"{r.get('index')}. " if r.get("index") else ""
            # 图上状态是行底渐变的颜色，纯文本里没有颜色，得把状态词补回来（否则看不出谁坏了）
            state = _STATE_LABELS.get(str(r.get("state") or ""), "")
            mark = f"[{state}] " if state else ""
            # 「本次通过 / 失败」同理：图上是彩色胶囊，纯文本里只能靠文字
            if r.get("probe_label"):
                mark = f"[{r['probe_label']}] " + mark
            tail = " / ".join(str(x) for x in (r.get("cells") or []))
            lines.append(f"{idx}{mark}{r.get('label')}  {tail}".rstrip())
            if r.get("note"):
                lines.append(f"    {r['note']}")
        lines.extend(str(x) for x in (notes or []) if x)
        return "\n".join(lines)

    # ------------------------- 直发（绕开「回复时 @ 发送者」） -------------------------
    #
    # 为什么不用 ``yield event.plain_result(...)`` / ``yield event.chain_result(...)``：
    # AstrBot 的 ``ResultDecorateStage`` 会给「**只含 Plain / Image** 的结果链」在最前面插一个
    # ``At(发送者)``（开关是全局的 ``platform_settings.reply_with_mention``，群聊里开了就会这样）。
    # 而我们的卡片恰好就是一张 Image —— 于是每张卡片都自带一个 @ 用户：
    # 群里刷屏、私聊又多余，且用户明确要求「直接发图片」。
    # **直发**（``event.send``）不进结果链，因此不会被装饰、也不会被改写。
    # 顺带的好处：``_has_send_oper`` 置位后本轮不会再触发一次 LLM 回复。
    #
    # 代价：结果链上的「长文本转图 / 超长转合并转发」等装饰也不再生效 ——
    # 对卡片无所谓（本来就是图），对纯文本回执反而是好事（我们要的就是原样送达）。

    @staticmethod
    async def _reply(event: AstrMessageEvent, text: str) -> bool:
        """直发一条文本。返回是否成功；失败只记日志，不回抛（别让回执失败带崩整轮流程）。"""
        try:
            await event.send(MessageChain([Plain(str(text))]))
            return True
        except Exception as e:
            logger.warning(f"[ModelPanel] 发送文本失败（忽略）: {e}")
            return False

    async def _notice_card(self, event: AstrMessageEvent, *, title: str, headline: str,
                           note: str = "", tone: str = "alert") -> None:
        """发一张「只有头 + 脚」的提示卡（检测中 / 撞车 / 报错），渲染不出来退回文本。

        ``tone``：``alert``（橙）= 需要你处理的通知，``error``（红）= 真出错了。
        与纯文本相比，卡片的好处是**性质写在颜色里**：同一个群里几条指令下去，
        哪条是「等一会儿」哪条是「去看日志」一眼就能分。
        """
        png = await self._card_png(
            title=title, headline=headline, notes=[note] if note else [],
            meta=self._card_meta("此刻"), tone=tone, width=1040,
        )
        if png is not None and await self._reply_chain(event, MessageChain([Image.fromBytes(png)])):
            return
        await self._reply(event, f"{title}｜{headline}" + (f"\n{note}" if note else ""))

    @staticmethod
    async def _reply_chain(event: AstrMessageEvent, chain) -> bool:
        """直发一个消息链（卡片图片用）。"""
        try:
            await event.send(chain)
            return True
        except Exception as e:
            logger.warning(f"[ModelPanel] 发送卡片失败（忽略）: {e}")
            return False

    # ================= 巡检与告警 =================
    _ALERT_TITLES = {
        KIND_FAIL: "模型故障告警",
        KIND_RECOVER: "模型恢复通知",
        KIND_FREE_EXPIRING: "限时免费即将到期",
    }

    async def _monitor_loop(self) -> None:
        """后台巡检循环。

        单轮异常绝不能终止循环——否则功能会静默停摆到下次重启，
        而「没告警」看起来永远像「一切正常」。
        """
        while getattr(self, "_monitor_running", False):
            interval = 180
            try:
                cfg = MonitorConfig.from_config(self.config)
                interval = cfg.interval_sec
                if cfg.enabled:
                    await self._monitor_tick(cfg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[ModelPanel] 巡检轮次异常，跳过本轮: {e}", exc_info=True)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    async def _monitor_tick(self, cfg: MonitorConfig) -> None:
        # 开关在 tick 内部也判一次：只有循环检查的话，任何其它调用点
        # （手动触发、未来的接口）都能绕过这个 kill switch。
        if not cfg.enabled:
            return
        if not self.storage:
            return
        now = int(time.time())
        # 告警数据源优先用逐次调用（本插件自己埋的），因为它才看得见「主模型失败、
        # 备用模型救回来」那一类 —— 核心 provider_stats 会把那一轮记到备用模型头上，
        # 结果就是日志里失败一堆、告警一条不发。逐次表还是空的（刚装上、或埋点没挂上）
        # 时才退回核心表，至少不丢告警能力。
        rows: list = []
        new_cursor = 0
        use_calls = False
        try:
            use_calls = (await self.storage.calls_count_all()) > 0
        except Exception as e:
            logger.warning(f"[ModelPanel] 检查逐次调用表失败: {e}")
        if use_calls:
            cursor_raw = await self.storage.get_state_value("calls_cursor", "")
            if cursor_raw == "":
                head = await self.storage.calls_latest_id()
                await self.storage.set_state_value("calls_cursor", str(head))
                logger.info(f"[ModelPanel] 逐次巡检游标已初始化（跳过历史 {head} 条）")
                return
            try:
                cursor = int(cursor_raw)
            except (TypeError, ValueError):
                cursor = 0
            rows, new_cursor = await self.storage.calls_since(cursor, limit=2000)
        else:
            cursor_raw = await self.storage.get_state_value("stats_cursor", "")
            if cursor_raw == "":
                # 首轮：把游标直接放到表尾。否则刚装上就会因为陈年历史里的失败立刻告警。
                head = await self.live_stats.latest_id()
                if head is None:
                    return
                await self.storage.set_state_value("stats_cursor", str(head))
                logger.info(f"[ModelPanel] 巡检游标已初始化（跳过历史 {head} 条）")
                return
            try:
                cursor = int(cursor_raw)
            except (TypeError, ValueError):
                cursor = 0
            rows, new_cursor = await self.live_stats.fetch_since(cursor, limit=2000)
        states = await self.storage.get_model_states()
        notified = await self.storage.last_notified_map()

        def cooling(pid: str, kind: str) -> bool:
            return notified.get((pid, kind), 0) + cfg.cooldown_sec > now

        names: dict[str, str] = {}
        for p in self._chat_providers():
            d = self._provider_display(p)
            if d.get("id"):
                names[d["id"]] = d.get("display_model") or d.get("model") or d["id"]
        profiles = await self.storage.get_all_profiles()
        decisions = evaluate_monitor(rows, states, cfg, now, cooling)
        decisions += free_expiry_decisions(profiles, cfg, now, cooling, names)

        # 状态照常落库（面板要显示真实状态），投递失败靠 alert_events 里
        # notified_at 为空的 open 事件在下一轮重试，见 _dispatch_alerts。
        for d in decisions:
            prev = states.get(d.provider_id) or {}
            await self.storage.save_model_state(d.provider_id, {
                "state": d.state,
                "consecutive_fail": d.consecutive_fail,
                "consecutive_ok": d.consecutive_ok,
                "last_error_code": d.last_error_code,
                "last_change_at": now if d.action else int(prev.get("last_change_at") or 0),
                "muted_until": int(prev.get("muted_until") or 0),
                "samples_total": int(prev.get("samples_total") or 0) + d.samples,
            })
        # 游标必须写回本次实际读的那个源，写错键的话下一轮会从头重读、告警重发
        await self.storage.set_state_value(
            "calls_cursor" if use_calls else "stats_cursor", str(new_cursor))
        await self._dispatch_alerts(decisions, names, now, cfg)
        pruned = await self.storage.cleanup_alerts(cfg.alert_retention_days)
        if pruned:
            logger.info(f"[ModelPanel] 清理已结告警事件 {pruned} 条")
        # 探测放在最后：它可能耗时几分钟（串行 × 超时），别拖累实时监测的判定节奏
        await self._maybe_probe(cfg, now)

    async def _dispatch_alerts(self, decisions, names, now: int, cfg: MonitorConfig) -> None:
        """按告警类型合并投递，并重试上一轮没送达的事件。

        合并的理由：一家供应商挂 10 个模型时，1 条列了 10 行的卡片比 10 条卡片有用。
        重试的理由：刚装上时 ``admins_id`` 往往还是默认占位值，若不重试，
        第一批告警会被静默吞掉，而「没收到告警」看起来永远像「一切正常」。
        """
        # 全局静音（/模型静音）时一条都不发，**补发也一并停掉**：
        # 只堵新增、放任补发的话，用户按了静音仍然会被历史事件刷屏 —— 那等于没静音。
        # 静音期间不写 notified_at，所以取消静音后 pending_alerts 会把它们补上，不丢。
        if not cfg.notify_enabled:
            logger.debug("[ModelPanel] 告警推送已全局关闭（/模型静音），本轮不投递")
            return
        groups: dict[str, list] = {}
        for d in [x for x in decisions if x.should_notify]:
            groups.setdefault(d.kind, []).append(d)
        for kind, group in groups.items():
            events = []
            for d in group:
                # 存进事件里的是「原因 + 为什么坏」：补发那条纯文本消息直接读它，
                # 只存一句「连续失败 3 次」的话，补发出去的消息同样说不清问题。
                detail = redact(d.reason)
                cause = _alert_cause(d)
                if kind == KIND_FAIL and cause:
                    detail = f"{detail}（{cause}）"
                events.append((d, await self.storage.open_alert(
                    d.provider_id, kind, detail, now, now + cfg.cooldown_sec)))
            chain = await self._alert_chain(kind, group, names, max(1, cfg.cooldown_sec // 60))
            if chain is None or not await self._notify_admins(chain):
                # 不写 notified_at → 冷却不启动，且该事件会被下一轮 pending_alerts 捞出来
                logger.warning(f"[ModelPanel] {self._ALERT_TITLES.get(kind, kind)} 未能送达管理员，下一轮重试")
                continue
            for d, aid in events:
                await self.storage.mark_alert_notified(aid, now)
                if kind == KIND_RECOVER:
                    # 恢复要把对应的故障事件关掉，否则它会一直挂着像没处理
                    await self.storage.resolve_alerts(d.provider_id, KIND_FAIL, now)
            logger.info(f"[ModelPanel] 已推送 {self._ALERT_TITLES.get(kind, kind)} ×{len(group)}")

        pending = await self.storage.pending_alerts()
        if not pending:
            return
        lines = ["之前有告警没能送达，现在补发："]
        for ev in pending[:10]:
            pid = str(ev.get("provider_id") or "")
            title = self._ALERT_TITLES.get(str(ev.get("kind") or ""), "模型告警")
            lines.append(f"· [{title}] {names.get(pid) or pid} {redact(ev.get('detail'), 60)}")
        ok = await self._notify_admins(MessageChain([Plain("\n".join(lines))]))
        if ok:
            for ev in pending:
                await self.storage.mark_alert_notified(int(ev["id"]), now)
            logger.info(f"[ModelPanel] 补发历史未送达告警 ×{len(pending)}")

    async def _alert_chain(self, kind: str, group, names: dict[str, str], cooldown_min: int):
        """把一组同类告警拼成一条消息：能出图就出图，没字体就退回文本。

        故障告警必须带上**失败原因**：只写「连续失败 3 次」等于让收到的人自己去翻日志，
        而这条消息的唯一用途就是让他决定「要不要现在处理」。错误码单独占一列、
        原始短文本跟在原因后面，一行就能看明白是中转站 5xx、限流还是模型名写错了。
        """
        title = self._ALERT_TITLES.get(kind, "模型告警")
        if kind == KIND_FAIL:
            columns, stats = ["连续失败", "成功/样本", "错误码"], [
                {"label": "故障", "value": str(len(group)), "state": "down"}]
        elif kind == KIND_RECOVER:
            columns, stats = ["连续成功", "成功/样本"], [
                {"label": "恢复", "value": str(len(group)), "state": "healthy"}]
        else:
            columns, stats = ["剩余天数"], [
                {"label": "临期", "value": str(len(group)), "state": "degraded"}]
        rows = []
        unknown_cause = 0
        for d in group:
            det = d.detail or {}
            ok_n, fail_n = int(det.get("ok") or 0), int(det.get("fail") or 0)
            ratio = f"{ok_n}/{ok_n + fail_n + int(det.get('aborted') or 0)}"
            cause = _alert_cause(d)
            if kind == KIND_FAIL:
                cells = [str(d.consecutive_fail), ratio, _alert_code(d)]
                if not cause:
                    unknown_cause += 1
                note = "｜".join(x for x in (redact(d.reason), cause or "原因未知") if x)
            elif kind == KIND_RECOVER:
                cells = [str(d.consecutive_ok), ratio]
                note = redact(d.reason)
            else:
                cells = [str(det.get("days_left", "?"))]
                note = redact(d.reason)
            rows.append({
                "state": "down" if kind == KIND_FAIL else ("degraded" if kind == KIND_FREE_EXPIRING else "healthy"),
                "label": names.get(d.provider_id) or d.provider_id,
                "note": note,
                "cells": cells,
            })
        notes = []
        if kind == KIND_FAIL:
            dist = _alert_distribution(group)
            if dist:
                notes.append(f"错误分布：{dist}")
            # 拿不到原因时要说清「不是没查，是这张表本来不记」，并给出下一步动作；
            # 「一个都拿不到」和「只差一两个」是两回事，别用同一句话糊过去
            if not unknown_cause:
                notes.append("错误文本已脱敏截断，完整堆栈见 AstrBot 日志")
            elif unknown_cause >= len(group):
                notes.append("核心统计表不记失败原因；发 /模型检测 可复测拿到具体报错")
            else:
                notes.append(f"{unknown_cause} 个模型拿不到原因（核心表不记）；发 /模型检测 可复测")
            notes.append(f"冷却 {cooldown_min} 分钟内不重复推送 · /模型静音 可关掉全部告警")
        elif kind == KIND_RECOVER:
            notes.append("恢复通知与故障告警共用同一冷却，避免抖动时来回发")
        elif kind == KIND_FREE_EXPIRING:
            notes.append("到期后是否转付费请人工确认")
        badge = {KIND_FAIL: "告警", KIND_RECOVER: "恢复", KIND_FREE_EXPIRING: "提醒"}.get(kind, "通知")
        # 这三张卡的颜色**固定**（不跟 card_theme）：橙 = 需要你处理，
        # 绿 = 已经好了。角标那两个字（告警 / 恢复 / 提醒）与颜色说的是同一件事，
        # 看颜色认得快、看字确认得准。
        tone = {KIND_FAIL: "alert", KIND_RECOVER: "ok", KIND_FREE_EXPIRING: "alert"}.get(kind, "")
        png = await self._card_png(
            title=title, badge=badge, stats=stats, columns=columns, rows=rows, notes=notes,
            headline=f"{len(group)} 个模型", meta=self._card_meta("此刻"), width=1040,
            tone=tone,
        )
        if png is not None:
            return MessageChain([Image.fromBytes(png)])
        lines = [title]
        for d in group:
            cause = _alert_cause(d)
            lines.append(f"{names.get(d.provider_id) or d.provider_id} · {redact(d.reason)}"
                         + (f"（{cause}）" if cause else ""))
        lines.extend(notes[:2])
        return MessageChain([Plain("\n".join(lines))])

    async def _maybe_probe(self, cfg: MonitorConfig, now: int) -> None:
        """定时探测的调度闸门：开了、到点、没撞上手动手测，才真跑。"""
        if not cfg.probe_enabled:
            return
        try:
            nxt = int(await self.storage.get_state_value("probe_next_at", "0") or 0)
        except (TypeError, ValueError):
            nxt = 0
        if now < nxt:
            return
        # 手动检测优先。撞车时定时让路并顺延，不排队——
        # 排着队等来的探测结果已经没有时效价值，还白占一次额度。
        if self._test_all_lock.locked() or self.sessions.is_busy():
            await self.storage.set_state_value("probe_next_at", str(now + 120))
            return
        await self.storage.set_state_value("probe_next_at", str(now + cfg.probe_interval_min * 60))
        await self._probe_round(cfg, now)

    async def _probe_round(self, cfg: MonitorConfig, now: int) -> None:
        """跑一轮定时探测，并把结果喂进与实时监测同一套状态机与告警链路。

        探测结果刻意复用 ``monitor.evaluate``：把探测输出包成 CallRecord 再走同一个引擎，
        这样「连续失败几次算故障」「冷却」「恢复需连续成功」只有一份实现，
        不会出现手动检测和定时检测两套判定互相打脸。
        """
        day = time.strftime("%Y%m%d", time.localtime(now))
        used_key = f"probe_used_{day}"
        try:
            used = int(await self.storage.get_state_value(used_key, "0") or 0)
        except (TypeError, ValueError):
            used = 0
        scopes = await self.storage.get_detect_scopes()
        profiles = await self.storage.get_all_profiles()
        targets = []
        for p in self._chat_providers():
            d = self._provider_display(p)
            pid = str(d.get("id") or "")
            if not pid or not (scopes.get(pid) or {}).get("scheduled"):
                continue
            billing = str((profiles.get(pid) or {}).get("billing_type") or "unknown")
            targets.append((pid, d, billing, p))
        if not targets:
            return
        if cfg.probe_daily_budget > 0:
            left = max(0, cfg.probe_daily_budget - used)
            if left <= 0:
                logger.info(f"[ModelPanel] 定时探测已达当日预算（{cfg.probe_daily_budget} 次），本轮跳过")
                return
            if left < len(targets):
                logger.info(f"[ModelPanel] 当日预算剩余 {left}，本轮只测前 {left}/{len(targets)} 个")
                targets = targets[:left]

        tcfg = self._test_config()
        timeout = float(tcfg["test_timeout"])
        sem = asyncio.Semaphore(max(1, cfg.probe_concurrency))

        async def one(item):
            pid, d, billing, provider = item
            async with sem:
                t = dict(tcfg)
                # 付费/试用/超额类不重试：重试等于双倍花钱，而探测一次就足够定性
                if billing in ("paid", "paid_overage", "trial"):
                    t["test_retry_count"] = 0
                mode = str((profiles.get(pid) or {}).get("probe_mode") or "non_stream")
                try:
                    r = await self._test_one(provider, timeout, t, mode)
                except Exception as e:
                    code, msg = _normalize_error(e)
                    r = {"ok": False, "latency_ms": None, "error_code": code,
                         "error": msg, "retry_count": 0}
                r.update({"id": pid, "name": d.get("name") or "", "model": d.get("model") or "",
                          "skipped": False, "checked_at": int(time.time())})
                return r

        started = time.monotonic()
        results = list(await asyncio.gather(*[one(x) for x in targets]))
        ok_n = sum(1 for r in results if r.get("ok"))
        logger.info(
            f"[ModelPanel] 定时探测完成：{len(results)} 个模型，成功 {ok_n}，"
            f"耗时 {time.monotonic() - started:.1f}s"
        )
        try:
            session = await self.storage.create_session("scheduled", len(results))
            for r in results:
                await self.storage.insert_result(session["id"], r)
            await self.storage.finish_session(session["id"], ok_n, len(results) - ok_n, 0)
        except Exception as e:
            logger.warning(f"[ModelPanel] 定时探测写历史失败: {e}")

        # 告警卡片上的名字用「供应商/模型」全名（与面板一致），不是裸模型名
        names = {pid: (d.get("display_model") or d.get("model") or pid)
                 for pid, d, _b, _p in targets}
        await self._apply_probe_results(results, names, now, cfg)
        try:
            await self.storage.set_state_value(used_key, str(used + len(results)))
        except Exception as e:
            logger.warning(f"[ModelPanel] 记录探测预算失败: {e}")

    async def _apply_probe_results(self, results: list[dict], names: dict[str, str],
                                   now: int, cfg: MonitorConfig) -> None:
        """把一批探测结果喂进**状态机与告警链路**。

        为什么所有探测入口都必须走这里：探测结果如果不进 ``model_state``，
        面板就会继续显示上一次的结论 —— 用户刚点完「检测」看到某模型挂了，
        回头打开面板却还是绿的（v1.3.15 之前外部检测正是这个毛病）。
        复用 ``monitor.evaluate`` 也保证了「连续失败几次算故障」「冷却」「恢复需连续成功」
        在手动 / 定时 / 外部三条路上只有一份实现。
        """
        if not results:
            return
        records = [
            CallRecord(
                id=i, provider_id=str(r.get("id") or ""), provider_model=str(r.get("model") or ""),
                status="completed" if r.get("ok") else "error",
                started_at=float(r.get("checked_at") or now),
                latency_ms=r.get("latency_ms"), ttft_ms=None,
                token_input=0, token_cached=0, token_output=0,
            )
            for i, r in enumerate(results)
        ]
        states = await self.storage.get_model_states()
        notified = await self.storage.last_notified_map()

        def cooling(pid: str, kind: str) -> bool:
            return notified.get((pid, kind), 0) + cfg.cooldown_sec > int(now)

        decisions = evaluate_monitor(records, states, cfg, int(now), cooling)
        # 探测路才知道 error_code 与错误原文（上面那批 CallRecord 是硬凑出来的，没有这两个字段），
        # 所以这里把它们补进 decision 的 detail —— 告警卡片的「错误码」列与「失败原因」都读它。
        err_of = {str(r.get("id")): (str(r.get("error_code") or ""), str(r.get("error") or ""))
                  for r in results}
        for d in decisions:
            code, msg = err_of.get(d.provider_id) or ("", "")
            if code or msg:
                d.detail["error_codes"] = {code or "unknown": 1}
                d.detail["last_error_code"] = code
                d.detail["last_error_message"] = redact(msg, 90)
                if d.state == "down" and code:
                    d.last_error_code = code
        code_of = {str(r.get("id")): str(r.get("error_code") or "") for r in results}
        for d in decisions:
            prev = states.get(d.provider_id) or {}
            await self.storage.save_model_state(d.provider_id, {
                "state": d.state,
                "consecutive_fail": d.consecutive_fail,
                "consecutive_ok": d.consecutive_ok,
                "last_error_code": code_of.get(d.provider_id, "") if d.state == "down" else "",
                "last_change_at": now if d.action else int(prev.get("last_change_at") or 0),
                "muted_until": int(prev.get("muted_until") or 0),
                "samples_total": int(prev.get("samples_total") or 0) + d.samples,
            })
        await self._dispatch_alerts(decisions, names, int(now), cfg)

    async def _notify_admins(self, chain) -> int:
        """主动投递给管理员。

        用 ``context.send_message`` 而不是 ``event.send``：后者只往事件结果链追加、
        不抛错，事件结束后会静默丢弃——本工作区的 cosyvoice 为此踩过坑。
        """
        sent = 0
        try:
            cfg = self.context.get_config() or {}
            admins = [str(a).strip() for a in (cfg.get("admins_id") or []) if str(a).strip()]
        except Exception as e:
            logger.warning(f"[ModelPanel] 读取 admins_id 失败: {e}")
            return 0
        platforms: list[str] = []
        try:
            platforms = [str(p.meta().id) for p in self.context.platform_manager.platform_insts]
        except Exception:
            platforms = []
        if not platforms:
            platforms = ["aiocqhttp"]
        for uid in admins:
            if uid.lower() == "astrbot":
                # 默认占位值不是真实 UID，发出去只会刷失败日志
                continue
            ok = False
            for plat in platforms:
                try:
                    if await self.context.send_message(f"{plat}:FriendMessage:{uid}", chain):
                        ok = True
                        break
                except Exception as e:
                    logger.debug(f"[ModelPanel] 告警投递失败 {plat}/{uid}: {e}")
            sent += 1 if ok else 0
        if not sent:
            logger.warning("[ModelPanel] 告警未送达任何管理员：检查 WebUI 平台设置里的「管理员 ID」是否已添加")
        return sent

    # ================= 指令检测（真打模型） =================
    _PROBE_MAX_TARGETS = 3

    async def _match_models_async(self, keywords: list[str]):
        """按关键词匹配模型，返回 (命中, 未开放指令检测)。

        一个关键词可能命中多个模型（同名模型在不同渠道各一个 provider），
        所以命中集按 provider id 去重，而不是取第一个。
        """
        scopes = {}
        if self.storage:
            try:
                scopes = await self.storage.get_detect_scopes()
            except Exception as e:
                logger.warning(f"[ModelPanel] 读取指令检测名单失败: {e}")
        return self._match_models_with(scopes, keywords)

    def _match_models_with(self, scopes: dict, keywords: list[str]):
        hits, denied, matched_ids = [], [], set()
        pats = [k.lower() for k in keywords if k]
        for p in self._chat_providers():
            d = self._provider_display(p)
            pid = str(d.get("id") or "")
            if not pid or pid in matched_ids:
                continue
            hay = " ".join([str(d.get("display_model") or ""), str(d.get("model") or ""),
                            str(d.get("name") or ""), pid]).lower()
            if not any(pat in hay for pat in pats):
                continue
            matched_ids.add(pid)
            if not (scopes.get(pid) or {}).get("command", True):
                denied.append(d)
                continue
            hits.append((pid, d, p))
        return hits, denied

    async def _probe_result_chain(self, results: list[dict], names: dict[str, str], context_line: str):
        """把一次指令探测的结果拼成卡片；没有可用字体时退回文本。"""
        down = sum(1 for r in results if not r.get("ok"))
        stats = [
            {"label": "成功", "value": str(len(results) - down), "state": "healthy"},
            {"label": "失败", "value": str(down), "state": "down" if down else "healthy"},
        ]
        rows = []
        for i, r in enumerate(results, start=1):
            pid = str(r.get("id") or "")
            ok = bool(r.get("ok"))
            if ok:
                note = f"重试 {r.get('retry_count')} 次" if r.get("retry_count") else ""
            else:
                # 失败的行必须写清「怎么坏的」：具体报错比错误码更能直接定位问题
                note = " · ".join(x for x in (_error_label(r.get("error_code")),
                                              redact(r.get("error"), 90)) if x)
            rows.append({
                "index": i,
                "state": "healthy" if ok else "down",
                "label": names.get(pid) or pid,
                "note": note,
                "cells": [_fmt_ms(r.get("latency_ms")),
                          time.strftime("%H:%M:%S",
                                        time.localtime(int(r.get("checked_at") or time.time())))],
            })
        notes = ["探测延迟为单次往返耗时", context_line]
        headline = f"成功 {len(results) - down} / 失败 {down}"
        png = await self._card_png(
            title="模型检测结果", badge="指令", stats=stats, columns=["探测延迟", "时间"],
            rows=rows, notes=notes, headline=headline, meta=self._card_meta("此刻"),
            numbered=True,
        )
        if png is not None:
            return MessageChain([Image.fromBytes(png)])
        lines = ["模型检测结果"]
        for r in results:
            pid = str(r.get("id") or "")
            lines.append(f"{'OK ' if r.get('ok') else 'FAIL'} {names.get(pid) or pid} "
                         f"{_fmt_ms(r.get('latency_ms'))} {r.get('error_code') or ''}".rstrip())
        lines.append(notes[0])
        return MessageChain([Plain("\n".join(lines))])

    async def _probe_batch(self, targets: list, timeout: float) -> tuple[list[dict], dict[str, str]]:
        """并发探测一批模型，返回 ``(results, names)``。

        **只跑探测**：不写历史、不发消息、不占全局锁 —— 指令检测、定时巡检与
        「同伴插件（user_gateway）的切换模型检测」三条路都复用这一份实现，
        免得同一个「怎么算探测成功」的规则被抄成三份、慢慢长歪。
        """
        results: list[dict] = []
        names: dict[str, str] = {}
        tcfg = self._test_config()
        modes = await self._probe_modes()
        sem = asyncio.Semaphore(self.probe_parallelism())

        async def one(item):
            pid, d, provider = item
            names[pid] = d.get("display_model") or d.get("model") or pid
            async with sem:
                try:
                    r = await self._test_one(provider, timeout, tcfg, modes.get(pid, "non_stream"))
                except Exception as e:
                    code, msg = _normalize_error(e)
                    r = {"ok": False, "latency_ms": None, "error_code": code,
                         "error": msg, "retry_count": 0}
                r.update({"id": pid, "name": d.get("name") or "", "model": d.get("model") or "",
                          "skipped": False, "checked_at": int(time.time())})
                return r

        results = list(await asyncio.gather(*[one(x) for x in targets]))
        return results, names

    async def _record_probe_history(self, trigger: str, results: list[dict]) -> None:
        """把一次探测的结果写进历史（来源标记决定它在面板上算哪一种）。"""
        if not self.storage or not results:
            return
        try:
            ok_n = sum(1 for r in results if r.get("ok"))
            session = await self.storage.create_session(trigger, len(results))
            for r in results:
                await self.storage.insert_result(session["id"], r)
            await self.storage.finish_session(session["id"], ok_n, len(results) - ok_n, 0)
        except Exception as e:
            logger.warning(f"[ModelPanel] 探测写历史失败（{trigger}）: {e}")

    async def _run_command_probe(self, targets: list, umo: str, timeout: float) -> None:
        """后台跑指令探测并把结果推回原会话。

        不阻塞指令回执：3 个模型串起来最坏是 3 × 超时，直接在 handler 里等会让用户以为 bot 挂了。
        """
        try:
            results, names = await self._probe_batch(targets, timeout)
            await self._record_probe_history("command", results)
            tcfg = self._test_config()
            # 每行已经带各自时刻，底部再放一个总时间戳会看着像两处不一致
            chain = await self._probe_result_chain(
                results, names,
                f"超时上限 {int(timeout)}s · 单模型最多重试 {tcfg.get('test_retry_count', 0)} 次")
            if not await self.context.send_message(umo, chain):
                logger.warning("[ModelPanel] 指令探测结果未能送回原会话")
        except Exception as e:
            logger.warning(f"[ModelPanel] 指令探测异常: {e}", exc_info=True)
        finally:
            self._test_all_lock.release()

    # ================= 对外接口（供其它插件联动） =================
    # 这两个方法被 astrbot_plugin_user_gateway 的「切换模型检测」调用。
    # 它们**只此一份**实现对的探测与统计口径，别的插件不该自己再打一遍模型、
    # 也不该自己算成功率 —— 否则同一个模型在两边的数字会对不上。
    # 改动这两个方法的返回结构前，先看 user_gateway 的 detect.py 怎么读。
    EXTERNAL_TRIGGER = "gateway"

    def _external_item(self, item: dict) -> dict:
        """把内部视图项裁成对外快照（只留别的插件真正会用的字段）。"""
        calls = item.get("calls") or {}
        last = item.get("last") or {}
        window = item.get("window") or {}
        counted = int(calls.get("counted") or 0)
        if counted:
            fail_rate = calls.get("fail_rate")
            samples = counted
        else:
            fail_rate = window.get("fail_rate")
            samples = int(window.get("counted") or 0)
        return {
            "provider_id": str(item.get("id") or ""),
            "model": str(item.get("model") or ""),
            "vendor": str(item.get("name") or ""),
            "label": str(item.get("display_model") or item.get("model") or item.get("id") or ""),
            "state": str(item.get("state") or "unknown"),
            # 「可用」= 没被判成故障。unknown（没数据）也算可用：
            # 没测过的模型不该在别人家里被标成不可用
            "available": str(item.get("state") or "unknown") != "down",
            "latency_ms": (last.get("ttft_ms") or last.get("latency_ms")
                           if last else None),
            "avg_latency_ms": (calls.get("avg_latency_ms") if counted
                               else window.get("avg_latency_ms")),
            "success_rate": (None if not samples
                             else round(1.0 - float(fail_rate or 0.0), 4)),
            "samples": samples,
            "last_ts": int((last or {}).get("ts") or 0),
            "last_ok": (bool(last.get("ok")) if last else None),
            "last_error_code": str((last or {}).get("error_code") or ""),
            "last_source": str((last or {}).get("source") or ""),
            "is_default": bool(item.get("is_default")),
            "reason": str(item.get("reason") or ""),
        }

    async def _external_lookup(self, provider_ids) -> dict[str, dict]:
        """provider_id → 探测目标 ``(pid, display, provider)``，按传入顺序。"""
        want = None if provider_ids is None else {str(p) for p in provider_ids}
        out: dict[str, tuple] = {}
        for p in self._chat_providers():
            d = self._provider_display(p)
            pid = str(d.get("id") or "")
            if not pid or (want is not None and pid not in want):
                continue
            out[pid] = (pid, d, p)
        return out

    async def external_snapshot(self, provider_ids=None, days: float = 0.0) -> dict:
        """给别的插件读的模型健康快照。**只读已有记录**：不请求模型、不产生费用。

        Args:
            provider_ids: 只关心这些 provider id；``None`` 表示当前加载的全部对话模型。
            days: 统计窗口天数；``<= 0`` 表示「今天」（与本插件面板的默认口径一致）。

        Returns:
            ``{"ok": True, "days": float, "items": {pid: {...}}, "missing": [pid, ...]}``。
            单个模型的字段见 :meth:`_external_item`；``last_ts`` 是**最近一次有数据的时间**
            （真实对话或探测都会更新它），``last_probe_ts`` 才是最近一次**探测**的时间。
        """
        try:
            view = await self._health_view(days=float(days) if days and days > 0 else _today_days())
            wanted = None if provider_ids is None else [str(p) for p in provider_ids]
            items = {str(it.get("id") or ""): self._external_item(it)
                     for it in (view.get("items") or [])}
            if wanted is not None:
                items = {pid: items[pid] for pid in dict.fromkeys(wanted) if pid in items}
            probes = await self._last_probe_times(list(items))
            for pid, ts in probes.items():
                if pid in items:
                    items[pid]["last_probe_ts"] = ts
            for pid in items:
                items[pid].setdefault("last_probe_ts", 0)
            missing = [str(p) for p in (provider_ids or []) if str(p) not in items]
            return {"ok": True, "days": float(view.get("days") or 0),
                    "items": items, "missing": missing,
                    "live_available": bool(view.get("live_available"))}
        except Exception as e:
            logger.warning(f"[ModelPanel] external_snapshot 失败: {e}")
            return {"ok": False, "error": str(e), "items": {}, "missing": []}

    async def _last_probe_times(self, provider_ids: list) -> dict[str, int]:
        """每个 provider 最近一次**探测**的时间（模型级冷却就是按它算的）。"""
        if not self.storage or not provider_ids:
            return {}
        try:
            rows = await self.storage.probe_window(days=30.0, limit=5000)
        except Exception as e:
            logger.warning(f"[ModelPanel] 读取探测历史失败: {e}")
            return {}
        out: dict[str, int] = {}
        for r in rows:
            pid = str(r.get("provider_id") or "")
            ts = int(r.get("checked_at") or 0)
            if pid and ts > out.get(pid, 0):
                out[pid] = ts
        return {pid: out.get(pid, 0) for pid in provider_ids}

    async def external_detect(self, provider_ids, timeout: float = 0.0) -> dict:
        """真打一次模型（**会消耗额度**），结果写进本插件历史，并返回最新快照。

        与手动/指令/定时探测**共用一把全局锁**：撞车时返回 ``ok=False, busy=True``，
        由调用方决定是提示用户稍后再试还是直接跳过 —— 排队等待只会让两边都超时。

        Args:
            provider_ids: 要检测的 provider id 列表（顺序即回执顺序）。
            timeout: 单模型超时秒数；``<= 0`` 表示用插件配置里的值。

        Returns:
            ``{"ok": bool, "results": [...], "items": {pid: {...}}, "error": str, "busy": bool}``；
            ``results`` 是本次探测的原始结果（含 ``ok`` / ``latency_ms`` / ``error``），
            可能与 ``items``（读库后的最新快照）不完全一致 —— 后者才是「现在面板上显示的」。
        """
        pids = [str(p) for p in (provider_ids or []) if str(p)]
        if not pids:
            return {"ok": False, "error": "没有要检测的模型", "results": [], "items": {}}
        try:
            timeout = float(timeout) if timeout and timeout > 0 else float(self._test_config()["test_timeout"])
        except Exception:
            timeout = 45.0
        if self._test_all_lock.locked():
            return {"ok": False, "busy": True, "results": [], "items": {},
                    "error": "已经有一轮检测在跑了"}
        await self._test_all_lock.acquire()
        try:
            lookup = await self._external_lookup(pids)
            targets = [lookup[pid] for pid in pids if pid in lookup]
            missing = [pid for pid in pids if pid not in lookup]
            if not targets:
                return {"ok": False, "error": "模型都不在了（可能已被删除或改名）",
                        "results": [], "items": {}, "missing": missing}
            results, names = await self._probe_batch(targets, timeout)
            await self._record_probe_history(self.EXTERNAL_TRIGGER, results)
            # 结果同样要喂进状态机与告警链路：否则面板继续显示上一次的结论 ——
            # 联动方刚打完一轮、用户一打开面板却还是绿的（这正是必须共用一份实现的原因）。
            await self._apply_probe_results(
                results, names, int(time.time()), MonitorConfig.from_config(self.config))
            snap = await self.external_snapshot(pids)
            logger.info(
                f"[ModelPanel] 外部检测完成：{len(results)} 个模型，"
                f"成功 {sum(1 for r in results if r.get('ok'))}"
            )
            return {"ok": True, "results": results, "items": snap.get("items") or {},
                    "missing": missing, "days": snap.get("days")}
        except Exception as e:
            logger.warning(f"[ModelPanel] external_detect 异常: {e}", exc_info=True)
            return {"ok": False, "error": str(e), "results": [], "items": {}}
        finally:
            self._test_all_lock.release()

    def external_available(self) -> dict:
        """给联动方探活用的信息（只读自己的配置，不碰数据库、不碰网络）。

        ``probe_concurrency`` / ``probe_timeout`` 是给联动方**估时**用的：
        联动方一次可能要点十几个模型（它默认不限量），得能告诉用户「最坏等多久」，
        而并发数与超时都是本插件定的，它猜不到也不该猜 —— 两边猜的数字迟早对不上。
        """
        return {"plugin": "astrbot_plugin_model_panel", "api": 3,
                "trigger": self.EXTERNAL_TRIGGER,
                "probe_concurrency": self.probe_parallelism(),
                "probe_timeout": self.probe_timeout_default(),
                # 此刻有没有检测在跑：联动方在调 external_detect **之前**就能知道，
                # 于是可以干脆不发「检测中」那张卡（发完再说「其实没跑」是自相矛盾的）。
                # 这只是一个瞬时快照，真正开跑时仍可能撞车 —— 所以对面还得处理 busy 返回值。
                "busy": self._test_all_lock.locked()}

    def probe_parallelism(self) -> int:
        """一次探测同时打几个模型。

        指令 / 定时 / 联动三条路都走 ``_probe_batch``，并发数只有这一个来源 ——
        自己再算一遍的地方迟早会和配置脱节。上限 3 是刻意压着的：
        中转站对并发很敏感，一次打太多容易被判成滥用。
        """
        try:
            n = int(MonitorConfig.from_config(self.config).probe_concurrency)
        except Exception:
            n = 3
        return max(1, min(3, n))

    def probe_timeout_default(self) -> float:
        """单模型探测的默认超时（秒）。给联动方估时用。"""
        try:
            return float(self._test_config()["test_timeout"])
        except Exception:
            return 45.0

    # ================= 序号选单与通用卡片 =================
    # 所有聊天指令共用一张「模型总览卡」：按供应商分组、统一编号。
    # 编号在同一张卡里**连续**（① 供应商、② 它下面的模型、③ …），
    # 所以「回数字」不必区分自己回的是组还是模型 —— 回组号就是选整组。
    _PICK_PROBE = "probe"    # 模型检测
    _PICK_SWITCH = "switch"  # 切换系统默认模型
    _PICK_STATS = "stats"    # 模型统计（看明细）
    # 回序号多选时一次最多测几个。v1.3.19 从 10 提到 30：用户明确说过
    # 「应该直接检测该分组所有可选模型一起测」—— 回一个分组序号却被告知「太多、少选几个」，
    # 与「整组一起测」是矛盾的。真管成本的是每日预算与并发上限（见 monitor 的护栏）。
    _PROBE_MAX_PICK = 30

    def _arm_picker(self, event: AstrMessageEvent, kind: str, options: list,
                    multi: bool = False) -> None:
        """挂上序号选单；``options`` 就是卡片上那份编号（含分组项）。"""
        _picker_arm(str(event.unified_msg_origin or ""), _actor_key(event), kind,
                    list(options or []), multi=multi)

    def _take_picker(self, event: AstrMessageEvent, index: int):
        return _picker_take(str(event.unified_msg_origin or ""), _actor_key(event), int(index))

    def _take_picker_multi(self, event: AstrMessageEvent, indices: list):
        return _picker_take_multi(str(event.unified_msg_origin or ""), _actor_key(event),
                                  list(indices or []))

    def _picker_state(self, event: AstrMessageEvent) -> Optional[dict]:
        """当前挂着的选单（领取前想先看看「能不能多选」时用），不作废。"""
        return _picker_entry(str(event.unified_msg_origin or ""), _actor_key(event))

    @staticmethod
    def _pick_hint(kind: str, multi: bool = False) -> str:
        """选单卡的脚注：把「怎么回」和「多久有效」写进最显眼那句。

        刻意压到一行放得下的长度：这行会被渲染器按宽度折行，一折就吃掉两行脚部空间。
        """
        ttl = _PICKER_TTL_SEC // 60
        if multi:
            return f"回复序号即检测（可多选，如 2 4；回组号测整组）· {ttl} 分钟内有效"
        if kind == "stats":
            return f"回复序号看该模型的明细卡 · {ttl} 分钟内有效"
        return f"回复序号即切换默认模型 · {ttl} 分钟内有效"

    async def _overview_chain(self, event: Optional[AstrMessageEvent], view: dict, *, title: str,
                              badge: str = "", footer: Optional[list] = None,
                              kind: str = "", multi: bool = False,
                              probe_of: Optional[dict] = None) -> MessageChain:
        """``event`` 允许为 None（后台任务发卡时没有事件）：那时 ``kind`` 必须留空。"""
        """发「模型总览卡」—— 状态 / 统计 / 检测 / 切换四个指令的公共入口。

        卡片行与选单选项由 :meth:`_grouped_card_rows` **一次性同时产出**，
        所以卡片上的号与「回数字」认的号天然一致；分两处各算一遍迟早就错位。
        """
        items = list(view.get("items") or [])
        rows, options = self._grouped_card_rows(view, numbered=True, probe_of=probe_of)
        stats = self._counts_stats(view)
        headline = f"{len(items)} 个模型"
        png = await self._card_png(
            title=title, badge=badge, stats=stats, columns=["延迟", "成功率", "更新"],
            rows=rows, notes=list(footer or []), headline=headline,
            meta=self._card_meta(_scope_label(view.get("days"))), numbered=True,
        )
        if kind:
            self._arm_picker(event, kind, options, multi=multi)
        if png is not None:
            return MessageChain([Image.fromBytes(png)])
        return MessageChain([Plain(self._rows_text(title, rows, stats=stats, notes=footer,
                                                   headline=headline))])

    async def _detail_chain(self, items: list, *, title: str = "模型明细",
                            live_available: bool = True) -> MessageChain:
        """明细卡：单个（或几个）模型，多给首字 P95、整轮、失败次数。"""
        rows, _options = self._grouped_card_rows({"items": list(items)}, numbered=False,
                                                 detailed=True)
        notes = ["首字与整轮为逐次埋点均值；P95、成功率按今天窗口统计",
                 "探测延迟不等于真实对话延迟"]
        if not live_available:
            notes[0] = "部分数据暂不可用（核心统计表读不到）"
        headline = f"{len(items)} 个模型"
        png = await self._card_png(
            title=title, badge="详情",
            columns=["首字", "首字P95", "整轮", "成功率", "失败", "更新"],
            rows=rows, notes=notes, headline=headline, meta=self._card_meta("今天"),
            # 明细卡有六个数值列，比总览卡还要挤；给足宽度，别把模型名截成「…-mini」
            width=1160,
        )
        if png is not None:
            return MessageChain([Image.fromBytes(png)])
        return MessageChain([Plain(self._rows_text(title, rows, notes=notes, headline=headline))])

    def _view_items_matching(self, view: dict, keyword: str) -> list:
        """按关键词匹配视图项（供应商名 / 模型名 / provider id 都算）。"""
        kw = str(keyword or "").strip().lower()
        if not kw:
            return []
        return [
            it for it in (view.get("items") or [])
            if kw in " ".join([
                str(it.get("display_model") or ""), str(it.get("model") or ""),
                str(it.get("name") or ""), str(it.get("id") or ""),
            ]).lower()
        ]

    def _probe_targets_of(self, pids: list) -> list:
        """provider id 表 → ``[(pid, display, provider)]``，顺序跟传入的 pid 表走。

        顺序必须跟着用户回的序号：探测是并发跑的，但回执卡片里的先后
        要和用户点的顺序对得上，否则「第一个是我点的那个吗」只能靠猜。
        """
        found: dict[str, tuple] = {}
        for p in self._chat_providers():
            d = self._provider_display(p)
            pid = str(d.get("id") or "")
            if pid and pid not in found:
                found[pid] = (pid, d, p)
        out, seen = [], set()
        for pid in [str(x) for x in (pids or [])]:
            if pid in found and pid not in seen:
                seen.add(pid)
                out.append(found[pid])
        return out

    async def _start_probe(self, event: AstrMessageEvent, targets: list):
        """开工一次指令探测：占锁 → 后台跑 → 先回执。撞车时礼貌拒绝（不排队）。"""
        if not targets:
            await self._reply(event, "那些模型已经不在了（可能被删除或改过 id），重新发一次吧。")
            return
        if len(targets) > self._PROBE_MAX_PICK:
            names = "、".join(str(d.get("model") or pid) for pid, d, _p in targets[:5])
            await self._reply(event,
                f"一次最多检测 {self._PROBE_MAX_PICK} 个模型，这次选了 {len(targets)} 个"
                f"（{names} …），少选几个再来～")
            return
        if self._test_all_lock.locked():
            await self._reply(event,
                "已经有一轮检测在跑了（手动 / 定时 / 上一条指令），等它结束再发一次～")
            return
        # 先占锁再回执：避免回执到真正开跑之间插进来一次手动一键检测
        await self._test_all_lock.acquire()
        timeout = float(self._test_config()["test_timeout"])
        asyncio.create_task(self._run_command_probe(targets, event.unified_msg_origin, timeout))
        # 估时按**轮数**算：探测是并发跑的（`_probe_batch`），
        # 早先写成 `timeout × 个数` 会给出 10 个模型「最坏 450s」这种把人吓退的数字。
        conc = self.probe_parallelism()
        rounds = -(-len(targets) // conc)
        await self._reply(event,
            f"已开始检测 {len(targets)} 个模型："
            + "、".join(str(d.get("model") or pid) for pid, d, _p in targets)
            + f"\n{conc} 个并发，最坏约 {int(round(rounds * timeout))}s 后把结果卡片发回这里。")

    @astr_filter.permission_type(astr_filter.PermissionType.ADMIN)
    @astr_filter.command("模型检测", alias={"检测模型"})
    async def cmd_model_probe(self, event: AstrMessageEvent):
        """指令检测：不带参数发带序号的卡片（可多选、可选分组），带关键词直接测。

        会**真实调用模型、消耗额度**，所以默认只对开放了「指令检测」通道的模型生效。
        旧名「检测模型」保留为别名，老用户不用改习惯。
        """
        keywords = _cmd_args(event).split()
        if not keywords:
            try:
                view = self._command_scope(await self._health_view(days=_today_days()))
            except Exception as e:
                logger.warning(f"[ModelPanel] /模型检测 取数失败: {e}")
                await self._reply(event, f"读取模型监测数据失败：{e}")
                return
            if not view.get("items"):
                await self._reply(event, _NO_COMMAND_SCOPE_HINT)
                return
            chain = await self._overview_chain(
                event, view, title="模型检测", badge="选单", kind=self._PICK_PROBE, multi=True,
                footer=[self._pick_hint(self._PICK_PROBE, multi=True),
                        f"会真实调用模型、产生额度消耗；一次最多 {self._PROBE_MAX_PICK} 个模型",
                        "延迟为最近一次调用耗时；成功率为今天窗口内统计"],
            )
            await self._reply_chain(event, chain)
            return
        hits, denied = await self._match_models_async(keywords)
        if denied:
            await self._reply(event,
                "这些模型没开放指令检测（去模型管理页勾选「指令检测」通道）："
                + "、".join(str(d.get("display_model") or d.get("id")) for d in denied[:5]))
            return
        if not hits:
            await self._reply(event, "没找到匹配「" + " ".join(keywords) + "」的模型")
            return
        if len(hits) > self._PROBE_MAX_TARGETS:
            await self._reply(event,
                f"匹配到 {len(hits)} 个，单次最多 {self._PROBE_MAX_TARGETS} 个，关键词再具体一点：\n"
                + "\n".join(str(d.get("display_model") or pid) for pid, d, _p in hits[:6])
                + "\n也可以直接发 /模型检测 看编号列表。")
            return
        await self._start_probe(event, hits)

    @astr_filter.permission_type(astr_filter.PermissionType.ADMIN)
    @astr_filter.command("全部模型检测", alias={"模型全检"})
    async def cmd_probe_all(self, event: AstrMessageEvent):
        """一键把**全部**对话模型打一遍（不受「指令检测」通道过滤），跑完发最新状态卡。

        与 `/模型检测` 的分工（两个指令都留着，因为「我配了通道」和
        「我现在想把机器上的模型全测一遍」是两个不同的意图）：

        - `/模型检测`：只测开放了「指令检测」通道的那批，可带关键词、可回序号选；
        - `/全部模型检测`：**不挑**，加载中的对话模型全测一遍 —— 用户原话
          「怎么用指令一次性检测全部模型」。

        三重防重入（用户明确要求「避免任务没跑完就加新的检测任务」）：

        1. ``_test_all_lock``（与手动 / 定时 / 联动**共用一把**）：撞车直接明说，不排队；
        2. **先占锁再回执**：回执与真正开跑之间不留空档，别人插不进来；
        3. 开跑前发一张「检测中」卡片（带并发数与最坏耗时）—— 否则用户不知道已经开始了，
           会再点一次，然后被 ① 拦住，看起来像「没反应」。
        """
        title = "全部模型检测"
        if self._test_all_lock.locked():
            await self._notice_card(
                event, title=title, headline="已经有一轮检测在跑了",
                note="同一时间只跑一轮（手动 / 定时 / 另一条指令都算）· "
                     "等它跑完再发一次这个指令",
            )
            return
        targets = self._probe_targets_of(
            [str(self._provider_display(p).get("id") or "") for p in self._chat_providers()])
        if not targets:
            await self._notice_card(
                event, title=title, headline="没有可检测的对话模型",
                note="先去「服务提供商」里加一个模型，或确认它已经加载",
                tone="error",
            )
            return
        await self._test_all_lock.acquire()  # 先占锁再回执
        timeout = float(self._test_config()["test_timeout"])
        conc = self.probe_parallelism()
        rounds = -(-len(targets) // conc)
        await self._notice_card(
            event, title=title, headline=f"正在检测全部 {len(targets)} 个模型",
            note=f"{conc} 个并发 · 最坏约 {int(round(rounds * timeout))} 秒 · "
                 "跑完自动发一张最新的模型状态卡片",
        )
        asyncio.create_task(self._run_all_probe(targets, event.unified_msg_origin, timeout))

    async def _run_all_probe(self, targets: list, umo: str, timeout: float) -> None:
        """后台：全测一遍 → 喂状态机与告警 → 把**最新的模型状态卡**发回原会话。"""
        try:
            results, names = await self._probe_batch(targets, timeout)
            # 记历史用 "command"：面板的来源标签只认那几个既有值，
            # 为这一条指令新造一个来源会让它显示成「未知」。
            await self._record_probe_history("command", results)
            await self._apply_probe_results(
                results, names, int(time.time()), MonitorConfig.from_config(self.config))
            view = await self._health_view(days=_today_days())
            probe_of = {str(r.get("id")): r for r in results if r.get("id")}
            ok_n = sum(1 for r in results if r.get("ok"))
            chain = await self._overview_chain(
                None, view, title="模型状态", badge="全部检测", probe_of=probe_of,
                footer=[f"刚刚全测了 {len(results)} 个模型：{ok_n} 通过 / "
                        f"{len(results) - ok_n} 失败（行内标签是本次结果）",
                        "延迟为最近一次调用耗时；成功率按今天窗口统计",
                        "「更新」为这一行最近一次记录的时间"],
            )
            if not await self.context.send_message(umo, chain):
                logger.warning("[ModelPanel] 全部模型检测的状态卡未能送回原会话")
        except Exception as e:
            logger.warning(f"[ModelPanel] 全部模型检测异常: {e}", exc_info=True)
        finally:
            self._test_all_lock.release()

    @astr_filter.permission_type(astr_filter.PermissionType.ADMIN)
    @astr_filter.command("切换系统模型")
    async def cmd_switch_model(self, event: AstrMessageEvent):
        """切换 AstrBot 的默认对话模型：不带参数列出候选并编号，回数字即切换。"""
        args = _cmd_args(event).split()
        providers = self._chat_providers()
        if args and args[0] in ("取消", "cancel"):
            _picker_disarm(str(event.unified_msg_origin or ""), _actor_key(event))
            await self._reply(event, "已取消，没有改动默认模型。")
            return
        if not args:
            try:
                # 这里刻意**不**按「指令检测」通道裁剪：换哪个模型本来就是全量决策，
                # 而卡片会顺便标出当前默认那行，正好用来看「现在走的是谁」。
                view = await self._health_view(days=_today_days())
            except Exception as e:
                logger.warning(f"[ModelPanel] /切换系统模型 取数失败: {e}")
                await self._reply(event, f"读取模型监测数据失败：{e}")
                return
            if not (view.get("items") or []):
                await self._reply(event, "当前没有加载任何对话模型。")
                return
            chain = await self._overview_chain(
                event, view, title="切换系统默认模型", badge="选单",
                kind=self._PICK_SWITCH,
                footer=[self._pick_hint(self._PICK_SWITCH),
                        "列表为全部有效对话模型（不按检测通道过滤）",
                        "/切换系统模型 取消 可关掉挂着的选单"],
            )
            await self._reply_chain(event, chain)
            return
        # 带参数时按关键词匹配，命中唯一才切，避免猜错
        hits = []
        pats = [k.lower() for k in args]
        for p in providers:
            d = self._provider_display(p)
            hay = " ".join([str(d.get("display_model") or ""), str(d.get("model") or ""),
                            str(d.get("name") or ""), str(d.get("id") or "")]).lower()
            if all(pat in hay for pat in pats):
                hits.append(d)
        if not hits:
            await self._reply(event,
                f"没找到匹配「{' '.join(args)}」的模型，发 /切换系统模型 可以看编号列表")
            return
        if len(hits) > 1:
            await self._reply(event,
                f"「{' '.join(args)}」匹配到 {len(hits)} 个，说具体一点：\n"
                + "\n".join(str(h.get("display_model") or h.get("id")) for h in hits[:6]))
            return
        await self._apply_default_model(event, str(hits[0].get("id")))

    async def _apply_default_model(self, event: AstrMessageEvent, pid: str):
        """把 pid 写成 AstrBot 的默认对话模型并回执。"""
        known = set(self._provider_ids())
        if known and pid not in known:
            await self._reply(event, "那个序号对应的模型已经不在了（可能被删除或改过 id），重新列一次吧。")
            return
        cfg = self._astrbot_config()
        if not isinstance(cfg, dict):
            await self._reply(event, "读不到 AstrBot 主配置，切换失败。")
            return
        before = await self._default_provider_id()
        if not self._set_chat_provider_id(cfg, pid):
            await self._reply(event, "配置里没有可写的默认模型字段，未做改动。")
            return
        try:
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
        except Exception as e:
            await self._reply(event, f"写入配置失败，已放弃切换：{e}")
            return
        self._sync_default_chat_runtime(pid)
        label = pid
        for p in self._chat_providers():
            d = self._provider_display(p)
            if str(d.get("id") or "") == pid:
                label = str(d.get("display_model") or d.get("model") or pid)
                break
        logger.info(f"[ModelPanel] 默认对话模型已由指令切换：{before or '未设置'} -> {pid}")
        await self._reply(event,
            f"已切换系统默认模型：{before or '未设置'} → {label}\n"
            "下一条对话就会走新模型。")

    @astr_filter.custom_filter(NumberPickerFilter)
    async def on_number_pick(self, event: AstrMessageEvent):
        """回「序号」执行选单（支持 ``1 3 5`` 这种多选）。

        NumberPickerFilter 已经确认过「整条消息就是一串序号」且这个 (会话, 人)
        挂着未过期选单，所以没挂选单的日常数字消息根本不会进到这里 ——
        它们照常走 LLM，不被插件吞。
        """
        indices = _parse_index_list(event.message_str)
        if not indices:
            return
        entry = self._picker_state(event)
        if not entry:
            return
        if entry.get("multi"):
            got = self._take_picker_multi(event, indices)
        else:
            # 单选类选单（切换模型 / 看明细）收到多选时**不作废**，让用户重回一个就好
            if len(indices) > 1:
                await self._reply(event, "这个选单一次只能选一个序号，请只回一个数字～")
                return
            one = self._take_picker(event, indices[0])
            got = None if one is None else (one[0], [one[1]])
        if not got:
            await self._reply(event, "序号超出范围，按卡片上的数字重新回一次就好～")
            return
        kind, options = got
        if kind == self._PICK_SWITCH:
            pids = _flatten_pids(options)
            if not pids:
                await self._reply(event, "那个模型已经不在了（可能被删除或改过 id）。")
                return
            await self._apply_default_model(event, pids[0])
            return
        if kind == self._PICK_STATS:
            pids = set(_flatten_pids(options))
            try:
                view = self._command_scope(await self._health_view(days=_today_days()))
            except Exception as e:
                await self._reply(event, f"读取模型监测数据失败：{e}")
                return
            items = [it for it in (view.get("items") or []) if str(it.get("id")) in pids]
            if not items:
                await self._reply(event, "那个模型已经不在了（可能被删除或改过 id）。")
                return
            chain = await self._detail_chain(items, live_available=bool(view.get("live_available")))
            await self._reply_chain(event, chain)
            return
        if kind == self._PICK_PROBE:
            targets = self._probe_targets_of(_flatten_pids(options))
            await self._start_probe(event, targets)
            return
        await self._reply(event, "这个选单不支持该操作。")

    @astr_filter.permission_type(astr_filter.PermissionType.ADMIN)
    @astr_filter.command("模型静音")
    async def cmd_model_mute(self, event: AstrMessageEvent):
        """告警推送的**全局**开关：``/模型静音`` 切换，``/模型静音 开|关`` 明确指定。

        v1.3.10 起改口径：不再针对单个模型，而是「所有模型的告警推送」一起开关。
        理由是同一个人被多家供应商的告警刷屏时，真正想按的是总闸；
        而关掉之后状态照常采集、面板照常显示 —— 静音的是通知，不是监测。
        开关落回插件配置 ``alert_notify_enabled``（与 WebUI 上那一项是同一个值），
        所以指令与 WebUI 互相看得见，重启也不会丢。
        """
        args = _cmd_args(event).split()
        cfg = getattr(self, "config", None)
        if not hasattr(cfg, "get"):
            await self._reply(event, "读不到插件配置，改不了告警开关。")
            return
        current = bool(cfg.get("alert_notify_enabled", True))
        if args and str(args[0]).strip().lower() in ("状态", "查询", "status"):
            await self._reply(event, self._mute_status(current))
            return
        word = str(args[0]).strip().lower() if args else ""
        if len(args) > 1 or (word and word not in (_MUTE_ON_WORDS | _MUTE_OFF_WORDS)):
            await self._reply(event,
                "用法：/模型静音 —— 切换全部模型的告警推送（再发一次即反向）\n"
                "也可以明确指定：/模型静音 关、/模型静音 开、/模型静音 状态")
            return
        want = (True if word in _MUTE_ON_WORDS
                else (False if word in _MUTE_OFF_WORDS else not current))
        if want == current:
            await self._reply(event, self._mute_status(current) + "\n（没有改动）")
            return
        try:
            cfg["alert_notify_enabled"] = bool(want)
        except Exception as e:
            await self._reply(event, f"写入配置失败：{e}")
            return
        save = getattr(cfg, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception as e:
                # 回滚内存里的值：否则「配置写失败但本进程确实静音了」，
                # 与磁盘、与下次重启后的行为都不一致，最难查。
                try:
                    cfg["alert_notify_enabled"] = bool(current)
                except Exception:
                    pass
                await self._reply(event, f"保存配置失败，已放弃改动：{e}")
                return
        logger.info(
            f"[ModelPanel] 告警推送全局开关：{'开' if current else '关'} -> "
            f"{'开' if want else '关'}（由指令切换）"
        )
        await self._reply(event, self._mute_status(bool(want)))

    @staticmethod
    def _mute_status(on: bool) -> str:
        """给用户看的开关状态 + 下一步怎么说。"""
        if on:
            return ("告警推送：已开启（模型故障 / 恢复 / 限时免费到期都会发给管理员）\n"
                    "发 /模型静音 可关掉全部告警推送。")
        return ("告警推送：已关闭（所有模型的告警都不再推送，状态照常采集、面板照常显示）\n"
                "再发一次 /模型静音 即可打开。")

    @astr_filter.permission_type(astr_filter.PermissionType.ADMIN)
    @astr_filter.command("模型状态")
    async def cmd_model_status(self, event: AstrMessageEvent):
        """模型健康与延迟总览：**只读**，不请求模型、不产生费用，也不挂选单。

        这是「通用卡片」本身：分组 + 序号 + 延迟 + 成功率，只给人看。
        别的指令（统计 / 检测 / 切换）发的是同一张卡，只是顺手挂上选单。
        """
        try:
            view = self._command_scope(await self._health_view(days=_today_days()))
        except Exception as e:
            logger.warning(f"[ModelPanel] /模型状态 取数失败: {e}")
            await self._reply(event, f"读取模型监测数据失败：{e}")
            return
        if not view.get("items"):
            await self._reply(event, _NO_COMMAND_SCOPE_HINT)
            return
        chain = await self._overview_chain(
            event, view, title="模型状态", badge="只读",
            # 口径必须写在卡片上：这张卡读的是**已有记录**，不是此刻去探测模型。
            # 标成「实时」会让人以为数字是刚打出来的（用户就是这么指出的），
            # 于是「模型明明挂了，卡上却是绿的」会变成一场没必要的排查。
            footer=["数据来自真实调用的记录，不是此刻探测",
                    "延迟为最近一次调用耗时；成功率按今天窗口统计；"
                    "「更新」为这一行最近一次记录的时间",
                    "/模型统计 可看单个模型的明细"],
        )
        await self._reply_chain(event, chain)

    @astr_filter.permission_type(astr_filter.PermissionType.ADMIN)
    @astr_filter.command("模型统计")
    async def cmd_model_stats(self, event: AstrMessageEvent):
        """查明细：不带参数先发带序号的总览卡（回序号看那个模型的明细），也可直接带关键词。"""
        keyword = _cmd_args(event)
        try:
            view = self._command_scope(await self._health_view(days=_today_days()))
        except Exception as e:
            logger.warning(f"[ModelPanel] /模型统计 取数失败: {e}")
            await self._reply(event, f"读取模型监测数据失败：{e}")
            return
        if not keyword:
            if not view.get("items"):
                await self._reply(event, _NO_COMMAND_SCOPE_HINT)
                return
            chain = await self._overview_chain(
                event, view, title="模型统计", badge="选单", kind=self._PICK_STATS,
                footer=[self._pick_hint(self._PICK_STATS),
                        "延迟为最近一次调用耗时；成功率为今天窗口内统计",
                        "也可以直接 /模型统计 <关键词> 一步到位"],
            )
            await self._reply_chain(event, chain)
            return
        hits = self._view_items_matching(view, keyword)
        if not hits:
            await self._reply(event, f"没找到名字里含「{keyword}」的模型～")
            return
        if len(hits) > 6:
            names = "\n".join(str(h.get("display_model") or h.get("id")) for h in hits[:6])
            await self._reply(event,
                f"「{keyword}」匹配到 {len(hits)} 个，关键词再具体一点：\n{names}\n"
                "也可以发 /模型统计 看编号列表。")
            return
        chain = await self._detail_chain(hits, live_available=bool(view.get("live_available")))
        await self._reply_chain(event, chain)

    async def terminate(self):
        # 必须显式停循环：热重载后旧任务若残留，会出现同一份巡检双份跑、告警双发，
        # 而且旧任务闭包里抓的是上一版的 self（存储实例可能已经关掉了）
        self._monitor_running = False
        task = getattr(self, "_monitor_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._monitor_task = None
        try:
            if self.storage:
                await self.storage.close()
        except Exception:
            pass
