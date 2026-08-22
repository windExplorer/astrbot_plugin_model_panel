from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from quart import request

COMPANION_PLUGIN_NAME = "astrbot_plugin_private_companion"

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


@register("astrbot_plugin_model_panel", "local", "模型管理与检测面板", "0.1.0")
class ModelPanelPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        self._register_routes()
        logger.info("[ModelPanel] 模型控制台插件已初始化")

    # ---------------- 路由注册 ----------------
    def _register_routes(self) -> None:
        self.context.register_web_api(
            "/panel/overview",
            self.api_overview,
            ["GET"],
            "ModelPanel overview",
        )
        self.context.register_web_api(
            "/panel/providers",
            self.api_list_providers,
            ["GET"],
            "ModelPanel providers",
        )
        self.context.register_web_api(
            "/panel/providers/test",
            self.api_test_provider,
            ["POST"],
            "ModelPanel test provider",
        )
        self.context.register_web_api(
            "/panel/providers/test_all",
            self.api_test_all_providers,
            ["POST"],
            "ModelPanel test all providers",
        )
        self.context.register_web_api(
            "/panel/default_model",
            self.api_default_model,
            ["GET"],
            "ModelPanel default model",
        )
        self.context.register_web_api(
            "/panel/companion/providers",
            self.api_companion_providers,
            ["GET"],
            "ModelPanel companion providers",
        )
        self.context.register_web_api(
            "/panel/companion/replace",
            self.api_companion_replace,
            ["POST"],
            "ModelPanel companion replace",
        )

    # ---------------- 工具 ----------------
    def _chat_providers(self) -> list[Any]:
        try:
            return self.context.get_all_providers()
        except Exception as e:
            logger.warning(f"[ModelPanel] get_all_providers 失败: {e}")
            return []

    def _provider_display(self, provider: Any) -> dict:
        cfg = getattr(provider, "provider_config", None)
        cfg = cfg if isinstance(cfg, dict) else {}
        pid = str(cfg.get("id") or "")
        ptype = str(cfg.get("type") or cfg.get("provider_type") or "")
        name = str(cfg.get("name") or pid or "")
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
        return {
            "id": pid,
            "name": name,
            "type": ptype,
            "model": model,
        }

    def _companion_star(self) -> Any:
        # 用 get_all_stars 遍历，按插件目录名/name 匹配，比 get_registered_star 更稳
        try:
            for star in self.context.get_all_stars():
                name = getattr(star, "name", "") or ""
                root = getattr(star, "root_dir_name", "") or ""
                if name == COMPANION_PLUGIN_NAME or root == COMPANION_PLUGIN_NAME:
                    return star
        except Exception:
            pass
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
                raw = cfg.get(key)
            except Exception:
                raw = None
            values[key] = str(raw).strip() if raw else ""
        return values

    def _default_provider_id(self) -> str:
        try:
            pm = self.context.provider_manager
            ps = getattr(pm, "provider_settings", None) or {}
            return str(ps.get("default_provider_id") or "")
        except Exception:
            return ""

    # ---------------- API ----------------
    async def api_overview(self) -> dict:
        providers = self._chat_providers()
        ids = {p["id"] for p in (self._provider_display(p) for p in providers)}
        default_id = self._default_provider_id()
        return {
            "total": len(providers),
            "default_provider_id": default_id,
            "default_set": bool(default_id and default_id in ids),
            "companion_loaded": self._companion_star() is not None,
            "companion_provider_count": sum(1 for v in self._companion_provider_values().values() if v),
        }

    async def api_list_providers(self) -> dict:
        providers = self._chat_providers()
        default_id = self._default_provider_id()
        items = []
        for p in providers:
            d = self._provider_display(p)
            d["is_default"] = bool(d["id"] and d["id"] == default_id)
            items.append(d)
        return {"items": items, "default_provider_id": default_id}

    async def api_test_provider(self) -> dict:
        payload = await self._json_payload()
        provider_id = str(payload.get("id") or "").strip()
        timeout = float(payload.get("timeout") or 45)
        for p in self._chat_providers():
            d = self._provider_display(p)
            if d["id"] == provider_id:
                result = await self._test_one(p, timeout)
                result.update({"id": provider_id, "name": d["name"], "model": d["model"]})
                return result
        return {"id": provider_id, "ok": False, "error": "provider not found", "latency_ms": None}

    async def api_test_all_providers(self) -> dict:
        payload = await self._json_payload()
        skip = set(payload.get("skip") or [])
        timeout = float(payload.get("timeout") or 45)
        providers = self._chat_providers()
        results = []
        for p in providers:
            d = self._provider_display(p)
            if d["id"] in skip:
                results.append({"id": d["id"], "name": d["name"], "model": d["model"], "skipped": True})
                continue
            r = await self._test_one(p, timeout)
            r.update({"id": d["id"], "name": d["name"], "model": d["model"], "skipped": False})
            results.append(r)
        return {"items": results, "total": len(providers)}

    async def api_default_model(self) -> dict:
        return {"default_provider_id": self._default_provider_id()}

    async def api_companion_providers(self) -> dict:
        cfg = self._companion_config()
        if cfg is None:
            return {"loaded": False, "items": []}
        values = self._companion_provider_values()
        items = []
        for key, value in values.items():
            items.append({"key": key, "value": value})
        return {"loaded": True, "items": items, "config_mode": str(cfg.get("provider_config_mode") or "")}

    async def api_companion_replace(self) -> dict:
        payload = await self._json_payload()
        replacements = payload.get("replacements") or []
        if not isinstance(replacements, list) or not replacements:
            return {"ok": False, "error": "replacements 不能为空"}
        cfg = self._companion_config()
        if cfg is None:
            return {"ok": False, "error": f"未找到插件 {COMPANION_PLUGIN_NAME}"}
        changed: list[dict] = []
        for item in replacements:
            old = str(item.get("old") or "").strip()
            new = str(item.get("replacement") or "").strip()
            if not old or not new or old == new:
                continue
            for key in COMPANION_PROVIDER_KEYS:
                cur = str(cfg.get(key) or "").strip()
                if cur == old:
                    cfg[key] = new
                    changed.append({"key": key, "old": old, "new": new})
        try:
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
        except Exception as e:
            logger.warning(f"[ModelPanel] 保存伴侣插件配置失败: {e}")
            return {"ok": False, "error": f"保存失败: {e}", "changed": changed}
        return {"ok": True, "changed_count": len(changed), "changed": changed}

    # ---------------- 内部 ----------------
    async def _json_payload(self) -> dict:
        try:
            return await request.get_json(silent=True) or {}
        except Exception:
            return {}

    async def _test_one(self, provider: Any, timeout: float) -> dict:
        start = time.monotonic()
        try:
            await asyncio.wait_for(provider.test(timeout=timeout), timeout=timeout + 5)
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {"ok": True, "latency_ms": latency_ms, "error": None}
        except asyncio.TimeoutError:
            return {"ok": False, "latency_ms": None, "error": "timeout"}
        except Exception as e:
            return {"ok": False, "latency_ms": None, "error": str(e)[:200]}

    async def terminate(self):
        pass
