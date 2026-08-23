from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.core.provider.entities import ProviderType
from quart import Response, request

from .storage import Storage
from .session_manager import SessionManager

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
    "NEWS_PROVIDER_ID",
    "WEB_EXPLORATION_PROVIDER_ID",
    "EMOTION_JUDGEMENT_PROVIDER_ID",
]

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

# 错误短消息最长字符数（防止 200 字符堆栈塞满前端）
ERROR_MESSAGE_MAX = 80


def _normalize_error(exc: BaseException | str) -> tuple[str, str]:
    """把异常归一化为 (error_code, error_message)。"""
    raw = str(exc or "")
    for code, pattern in _ERROR_RULES:
        if pattern.search(raw):
            return code, raw[:ERROR_MESSAGE_MAX]
    return "unknown", raw[:ERROR_MESSAGE_MAX]


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


@register("astrbot_plugin_model_panel", "local", "模型管理与检测面板", "0.1.0")
class ModelPanelPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.storage: Optional[Storage] = None
        self.sessions = SessionManager()
        # 全局并发去重：同时只允许一个一键检测任务在跑
        self._test_all_lock = asyncio.Lock()

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
        self._register_routes()
        logger.info(f"[ModelPanel] 模型控制台插件已初始化（db={db_path}）")

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
            ("/panel/preferences", self.api_get_preferences, ["GET"]),
            ("/panel/preferences", self.api_set_preferences, ["POST"]),
            ("/panel/config", self.api_get_config, ["GET"]),
            ("/panel/config", self.api_set_config, ["POST"]),
            ("/panel/default_model", self.api_default_model, ["GET"]),
            ("/panel/companion/providers", self.api_companion_providers, ["GET"]),
            ("/panel/companion/replace", self.api_companion_replace, ["POST"]),
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
            raw = None
            try:
                # 用与陪伴插件一致的 _flat_get：优先 schema 分组嵌套值，
                # 避免读到顶层 legacy 扁平副本与真实值不一致。
                raw = _flat_get(cfg, key)
            except Exception:
                raw = None
            values[key] = str(raw).strip() if raw else ""
        return values

    # 陪伴插件"备用模型"配置：model_fallback_overrides，值为 {provider_key: 备用 provider_id}
    # 的 JSON 字符串（顶层 legacy flat key）。用与陪伴插件一致的 _normalize 语义解析。
    FALLBACK_CONFIG_KEY = "model_fallback_overrides"

    def _companion_fallback_values(self) -> dict[str, str]:
        """返回 {provider_key: 备用 provider_id}。解析 config 里 model_fallback_overrides。"""
        cfg = self._companion_config()
        if cfg is None:
            return {}
        raw = None
        try:
            raw = _flat_get(cfg, self.FALLBACK_CONFIG_KEY)
        except Exception:
            raw = None
        if raw is None:
            return {}
        # config 里可能是 JSON 字符串，也可能是已解析的 dict
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
        for key, provider_id in raw.items():
            k = str(key).strip()
            pid = str(provider_id or "").strip()
            if k in COMPANION_PROVIDER_KEYS and pid:
                out[k] = pid
        return out

    def _set_companion_fallback(self, key: str, provider_id: str) -> None:
        """把某 key 的备用 provider 写入 model_fallback_overrides（扁平 + 分组同步）。"""
        cfg = self._companion_config()
        if cfg is None:
            return
        current = self._companion_fallback_values()
        if provider_id:
            current[key] = provider_id
        else:
            current.pop(key, None)
        encoded = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
        # 写扁平副本 + 可能存在的 schema 分组嵌套（_flat_set 递归同步所有位置）
        _flat_set(cfg, self.FALLBACK_CONFIG_KEY, encoded)

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

    # ---------------- API ----------------
    async def api_overview(self) -> dict:
        providers = self._chat_providers()
        ids = {p["id"] for p in (self._provider_display(p) for p in providers)}
        default_id = await self._default_provider_id()
        stats: dict = {}
        latest_results: dict = {}
        try:
            if self.storage:
                stats = await self.storage.session_stats()
                latest_results = await self.storage.latest_per_provider()
        except Exception as e:
            logger.warning(f"[ModelPanel] overview 统计失败: {e}")
        return {
            "total": len(providers),
            "default_provider_id": default_id,
            "default_set": bool(default_id and default_id in ids),
            "companion_loaded": self._companion_star() is not None,
            "companion_provider_count": sum(1 for v in self._companion_provider_values().values() if v),
            "history": stats,
            "latest_results": latest_results,
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
        # provider_models: 去重后的 {model, vendor, id} 列表。
        # 新前端下拉用 vendor · model 作 label、provider id 作 value，
        # 因为陪伴插件 config 里存的是 provider id，必须写回 provider id 才能匹配上。
        provider_models = [
            {"model": m, "vendor": seen_models[m]["name"], "id": seen_models[m]["id"]}
            for m in models
        ]
        logger.info(
            f"[ModelPanel] /panel/providers 返回 {len(items)} 个 provider, "
            f"{len(models)} 个去重模型, 默认={default_id or '无'}"
        )
        return {
            "items": items,
            "models": models,
            "provider_models": provider_models,
            "default_provider_id": default_id,
        }

    # ---------- 插件配置（检测参数） ----------
    async def api_get_config(self) -> dict:
        """读取插件 _conf_schema 暴露的字段（含默认值），返回当前生效值。"""
        cfg = getattr(self, "config", None)
        defaults = {
            "test_timeout": 45,
            "test_retry_count": 1,
            "test_retry_backoff": 2.0,
            "history_retention_days": 30,
        }
        values = dict(defaults)
        if cfg is not None and hasattr(cfg, "get"):
            for k in defaults:
                v = cfg.get(k)
                if v is None:
                    continue
                if k == "test_retry_backoff":
                    values[k] = float(v)
                else:
                    values[k] = int(v)
        return {"items": values}

    async def api_set_config(self) -> dict:
        """批量设置插件配置字段。仅更新传入的 key，缺省保留原值。"""
        payload = await self._json_payload()
        raw = payload.get("items")
        if not isinstance(raw, dict):
            return {"ok": False, "error": "items 必须是对象 {key: value}"}
        cfg = getattr(self, "config", None)
        if cfg is None or not hasattr(cfg, "__setitem__"):
            return {"ok": False, "error": "插件配置不可写（self.config 缺失）"}
        type_map = {
            "test_timeout": int,
            "test_retry_count": int,
            "test_retry_backoff": float,
            "history_retention_days": int,
        }
        try:
            for k, v in raw.items():
                if k not in type_map:
                    continue
                casted = type_map[k](v)
                if k == "test_timeout":
                    casted = max(1, casted)
                elif k == "test_retry_count":
                    casted = max(0, casted)
                elif k == "test_retry_backoff":
                    casted = max(0.0, casted)
                elif k == "history_retention_days":
                    casted = max(0, casted)
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

    async def api_test_provider(self) -> dict:
        payload = await self._json_payload()
        provider_id = str(payload.get("id") or "").strip()
        cfg = self._test_config()
        timeout = float(payload.get("timeout") or cfg["test_timeout"])
        for p in self._chat_providers():
            d = self._provider_display(p)
            if d["id"] == provider_id:
                result = await self._test_one(p, timeout, cfg)
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
            skip = set(payload.get("skip") or [])
            cfg = self._test_config()
            timeout = float(payload.get("timeout") or cfg["test_timeout"])
            providers = self._chat_providers()
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
                r = await self._test_one(p, timeout, cfg)
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
        skip = set(payload.get("skip") or [])
        cfg = self._test_config()
        timeout = float(payload.get("timeout") or cfg["test_timeout"])
        providers = self._chat_providers()
        # 可选：只测指定的 provider 子集（分组一键测试用）
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
                        r = await self._test_one(p, timeout, cfg)
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

    async def api_default_model(self) -> dict:
        return {"default_provider_id": await self._default_provider_id()}

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
        logger.info(
            f"[ModelPanel] /panel/companion/providers: 共 {len(items)} 项, "
            f"已配置 {configured} 项, 模式={config_mode or '无'}"
        )
        return {
            "loaded": True,
            "items": items,
            "config_mode": config_mode,
            "configured_count": configured,
            "total_keys": len(COMPANION_PROVIDER_KEYS),
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
        # 陪伴插件在 bootstrap 时把 config 值一次性读到实例属性
        # （如 self.fast_response_provider_id），只 save_config() 写了文件但
        # 实例属性不会自动更新。需要调陪伴插件的 _apply_quick_provider_defaults()
        # 把新 config 值同步到运行时实例属性，否则陪伴插件的 WebUI / 运行逻辑
        # 仍然用旧值。
        star = self._companion_star()
        if star is not None:
            plugin_obj = getattr(star, "star_cls", None) or getattr(star, "instance", None) or star
            apply = getattr(plugin_obj, "_apply_quick_provider_defaults", None)
            if callable(apply):
                try:
                    apply()
                    logger.info("[ModelPanel] 已调 _apply_quick_provider_defaults 同步实例属性")
                except Exception as e:
                    logger.warning(f"[ModelPanel] _apply_quick_provider_defaults 调用失败: {e}")
            else:
                logger.warning("[ModelPanel] 陪伴插件未找到 _apply_quick_provider_defaults 方法，可能需要重启插件才能生效")
        logger.info(f"[ModelPanel] /panel/companion/replace: 替换 {len(changed)} 处")
        return {"ok": True, "changed_count": len(changed), "changed": changed}

    # ---------------- 内部 ----------------
    async def _json_payload(self) -> dict:
        try:
            return await request.get_json(silent=True) or {}
        except Exception:
            return {}

    async def _test_one(self, provider: Any, timeout: float, cfg: dict) -> dict:
        """检测单个模型，支持重试 + 错误归一化。

        返回：
        {
          ok: bool,
          latency_ms: float | None,
          error_code: str,
          error: str,
          retry_count: int,
        }
        """
        retry_count = max(0, int(cfg.get("test_retry_count") or 0))
        backoff = max(0.0, float(cfg.get("test_retry_backoff") or 0.0))
        attempts = retry_count + 1  # 至少一次
        last_err_code = "unknown"
        last_err_msg = ""
        for i in range(attempts):
            start = time.monotonic()
            try:
                await asyncio.wait_for(provider.test(timeout=timeout), timeout=timeout + 5)
                latency_ms = round((time.monotonic() - start) * 1000, 1)
                return {
                    "ok": True,
                    "latency_ms": latency_ms,
                    "error_code": "",
                    "error": "",
                    "retry_count": i,
                }
            except asyncio.TimeoutError as e:
                last_err_code, last_err_msg = _normalize_error("timeout")
            except Exception as e:
                last_err_code, last_err_msg = _normalize_error(e)
            if i < attempts - 1 and backoff > 0:
                try:
                    await asyncio.sleep(backoff * (i + 1))
                except Exception:
                    pass
        return {
            "ok": False,
            "latency_ms": None,
            "error_code": last_err_code,
            "error": last_err_msg,
            "retry_count": max(0, attempts - 1),
        }

    async def terminate(self):
        try:
            if self.storage:
                await self.storage.close()
        except Exception:
            pass
