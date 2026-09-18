"""扫描 AstrBot 所有已安装插件的「模型相关配置」，支持批量查看 / 替换。

## 为什么单独一个模块

`main.py` 已接近 2000 行，「全插件模型配置扫描」是一块相对独立的逻辑
（配置树遍历 + `_conf_schema.json` 解析 + 启发式识别 + 按路径写回），
单独成模块便于维护与测试。

## 如何判定「某个配置项是模型配置」

按可信度从高到低三种来源，扫描时全部收集、逐条标注可信度：

1. **schema 声明（high）**：插件 `_conf_schema.json` 里 `_special` 为
   `select_provider` / `select_provider_tts` / `select_provider_stt` 的配置项。
   这是 AstrBot 官方约定，值一定是 provider id，识别最准。
2. **值命中（high / medium）**：配置树里任意字符串值等于某个**已加载 provider 的 id**
   （high）或等于某个**已加载模型名**（medium）。
   额外支持「JSON 字符串里套着 `{key: provider_id}` 映射」的形态
   （陪伴插件的 `model_fallback_overrides` 就是这样存的），会被展开成多条。
3. **键名提示（low）**：键名含 `provider` / `model`，值是非空短字符串，
   但既不是已知 provider id 也不是已知模型名（典型场景：这个模型已经被删掉了，
   或插件用了自定义字段名）。低可信项在前端有明确标记，默认隐藏、可一键显示。
   两类情况**刻意排除**，否则会把插件自己的业务模型当成 AstrBot provider：
   - `template_list` 模板内的字段（LoRA 列表的 `model_name`、工作流卡片的
     `base_model` 等，值是 `xxx.safetensors` 这种绘图模型文件名）
   - 带 `options` 的枚举项与非字符串类型（如 `provider_config_mode=quick`）

## provider 目录覆盖范围

命中判定与"更换为"下拉都基于 `provider_catalog()`，它覆盖 AstrBot 支持的**全部**
provider 类型：`chat`（get_all_providers）、`tts`、`stt`、`embedding`
以及 `rerank`（4.27.4 的 Context 没有 rerank getter，直接取
`provider_manager.rerank_provider_insts`）。少一类就会出现"插件里明明配了
TTS/向量模型，却被标成未匹配、下拉里也选不到"。

## 写回

- 通用：按 path 精确写回插件 config（dict / list），最后 `save_config()`。
- 陪伴插件特例：它把 provider key 同时存在**顶层扁平副本**与
  `model_assignment_config` 分组里，必须用 main.py 的 `_flat_set` 语义全量同步，
  否则「我们改了一处、插件读另一处」，出现改了不生效的假象。
- JSON 字符串映射（`model_fallback_overrides`）：读出 → 改内层 key → 重新序列化写回。

## 需要注意的坑

- 插件实例与 `StarMetadata.config` 是**同一个 AstrBotConfig 对象**（AstrBot 4.27.4
  `star_manager` 里的 `plugin_config` 同时给了 `metadata.config` 和 `__init__(config=)`），
  所以改 `star.config` 后 `save_config()` 对插件是即时可见的。
- 但不少插件会把配置**缓存到实例属性**（bootstrap 时读一次）。对这类插件，
  我们只能做尽力而为的属性同步：路径是顶层单键、且实例属性当前值与旧值完全相同时，
  才 `setattr` 同步；其余情况由前端提示「可能需要重载插件」。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional

from astrbot.api import logger

# 本插件自身：没有任何"模型配置"，扫描时跳过，避免页面出现噪音
OWN_PLUGIN_NAME = "astrbot_plugin_model_panel"

# 陪伴插件：provider key 同时存在顶层扁平副本与 model_assignment_config 分组
COMPANION_PLUGIN_NAME = "astrbot_plugin_private_companion"
COMPANION_PROVIDER_GROUP = "model_assignment_config"

# schema 里 _special 的取值 → provider 类型
SPECIAL_KIND_MAP: dict[str, str] = {
    "select_provider": "chat",
    "select_provider_tts": "tts",
    "select_provider_stt": "stt",
}

# 键名提示：命中则认为"疑似模型配置项"
_KEY_HINT = re.compile(r"(provider|model)", re.I)
# 键名排除：这些字段名里虽然带 model/provider，但几乎都是提示词 / 密钥 / 路径 / 模式枚举等
# 注意 `(^|[^a-z0-9])mode(s)?($|[^a-z0-9])` 是为了匹配 `provider_config_mode`
# 又不误伤 `model` / `model_name`（`mode` 正好是 `model` 的前缀）。
_KEY_HINT_EXCLUDE = re.compile(
    r"(prompt|template|persona|api_?key|secret|password|token|url|endpoint|"
    r"path|dir|proxy|host|port|header|regex|keyword|blacklist|whitelist|"
    r"max_attempts|limit|temperature|top_p|timeout|"
    r"(^|[^a-z0-9])mode(s)?($|[^a-z0-9]))",
    re.I,
)

# 单条候选值的最大长度（超过基本是正文，不是模型标识）
_MAX_VALUE_LEN = 120
# 尝试当 JSON 解析的最大长度（防止把一篇长文本拿去 json.loads）
_MAX_JSON_LEN = 20000
# 配置树最大递归深度
_MAX_DEPTH = 8


def _path_display(path: list) -> str:
    """把一个路径列表渲染成 `a.b[0].c` 形式，供前端展示。"""
    out = ""
    for seg in path:
        if isinstance(seg, int):
            out += f"[{seg}]"
        elif out:
            out += f".{seg}"
        else:
            out = str(seg)
    return out


def _is_strict_suffix(shorter: list, longer: list) -> bool:
    """shorter 是否是 longer 的严格后缀（用于识别"顶层扁平副本 + 分组嵌套"镜像）。"""
    if len(shorter) >= len(longer):
        return False
    return list(longer[-len(shorter):]) == list(shorter)


class PluginModelScanner:
    """扫描 / 改写所有已安装插件里的模型相关配置。"""

    def __init__(
        self,
        host: Any,
        *,
        flat_set: Callable[[Any, str, Any], None],
        flat_set_existing: Callable[[Any, str, Any], None],
    ) -> None:
        # host 即 ModelPanelPlugin 实例：借它的 context 与我们自己的
        # companion 专用读写（_flat_set / _flat_set_existing）复用逻辑。
        self.host = host
        self.context = getattr(host, "context", None)
        self._flat_set = flat_set
        self._flat_set_existing = flat_set_existing

    # ================= provider 目录 =================
    @staticmethod
    def _safe_list(getter: Callable[[], Any]) -> list[Any]:
        try:
            return list(getter() or [])
        except Exception as e:
            logger.debug(f"[ModelPanel] 枚举 provider 失败: {e}")
            return []

    def provider_catalog(self) -> list[dict]:
        """所有已加载 provider（chat / tts / stt / embedding / rerank）的扁平目录。

        前端"更换为"下拉、值命中判定都依赖它。必须覆盖 AstrBot 支持的全部
        provider 类型，否则插件配置里引用的 TTS / STT / 向量 / 重排序模型
        会因为"查不到这个 id"而被标成「未匹配」，下拉里也选不到。
        同一个 provider id 只登记一次（AstrBot 的 inst_map 可能让同一实例
        出现在多个列表里）。
        """
        out: list[dict] = []
        seen: set[str] = set()
        ctx = self.context
        if ctx is None:
            return out

        pairs: list[tuple[str, str]] = [
            ("chat", "get_all_providers"),
            ("tts", "get_all_tts_providers"),
            ("stt", "get_all_stt_providers"),
            ("embedding", "get_all_embedding_providers"),
        ]
        pending: list[tuple[str, list[Any]]] = []
        for kind, method in pairs:
            getter = getattr(ctx, method, None)
            if callable(getter):
                pending.append((kind, self._safe_list(getter)))
        # rerank：AstrBot 4.27.4 的 Context 没有 getter 方法，直接从
        # provider_manager 拿（manager 里确实维护了 rerank_provider_insts）。
        pm = getattr(ctx, "provider_manager", None)
        rerank = getattr(pm, "rerank_provider_insts", None)
        if isinstance(rerank, (list, tuple)):
            pending.append(("rerank", list(rerank)))

        default_ids = self._default_provider_ids()
        for kind, providers in pending:
            for provider in providers:
                entry = self._provider_entry(provider, kind, default_ids)
                if not entry:
                    continue
                pid = entry["id"]
                if pid in seen:
                    continue
                seen.add(pid)
                out.append(entry)
        return out

    def _default_provider_ids(self) -> dict[str, str]:
        """各类别"当前默认 provider id"。

        - chat：`provider_settings.default_provider_id`（旧键）/ provider_manager
          上的 `default_chat_provider_id` 快照
        - tts：`provider_tts_settings.provider_id`
        - stt：`provider_stt_settings.provider_id`
        - embedding / rerank：AstrBot 主配置里**没有**全局默认项，
          由使用它们的插件自己指定，故不标默认。
        """
        out = {"chat": "", "tts": "", "stt": ""}
        try:
            pm = getattr(self.context, "provider_manager", None)
            ps = getattr(pm, "provider_settings", None) or {}
            out["chat"] = str(
                getattr(pm, "default_chat_provider_id", "") or ps.get("default_provider_id") or ""
            )
            tts = getattr(pm, "provider_tts_settings", None) or {}
            out["tts"] = str(tts.get("provider_id") or "")
            stt = getattr(pm, "provider_stt_settings", None) or {}
            out["stt"] = str(stt.get("provider_id") or "")
        except Exception as e:
            logger.debug(f"[ModelPanel] 读取默认 provider 失败: {e}")
        return out

    def _provider_entry(
        self, provider: Any, kind: str, default_ids: Optional[dict] = None
    ) -> Optional[dict]:
        cfg = getattr(provider, "provider_config", None)
        cfg = cfg if isinstance(cfg, dict) else {}
        pid = str(cfg.get("id") or "").strip()
        if not pid:
            return None
        ptype = str(cfg.get("type") or "")
        model = ""
        try:
            model = str(provider.get_model() or "").strip()
        except Exception:
            model = ""
        if not model:
            # 不同 provider 类型把模型名写在不同的配置字段里
            for key in (
                "model",
                "default_model",
                "selected_model",
                "embedding_model",
                "rerank_model",
                "tts_model",
                "stt_model",
            ):
                value = cfg.get(key)
                if isinstance(value, str) and value.strip():
                    model = value.strip()
                    break
        vendor = str(
            cfg.get("provider_source_id")
            or cfg.get("name")
            or cfg.get("provider")
            or ptype
            or pid
        )
        if model.endswith(" (model unknown)"):
            model = ""
        if model and vendor and vendor != model:
            label = f"{vendor} · {model}"
        else:
            label = model or vendor or pid
        is_default = bool(
            default_ids and str(default_ids.get(kind) or "").strip() == pid
        )
        return {
            "id": pid,
            "kind": kind,
            "type": ptype,
            "model": model,
            "vendor": vendor,
            "label": label,
            "is_default": is_default,
        }

    # ================= 扫描 =================
    def _stars(self) -> list[Any]:
        if self.context is None:
            return []
        getter = getattr(self.context, "get_all_stars", None)
        if not callable(getter):
            return []
        try:
            stars = list(getter() or [])
        except Exception as e:
            logger.warning(f"[ModelPanel] 遍历插件失败: {e}")
            return []
        out: list[Any] = []
        seen: set[str] = set()
        for star in stars:
            name = str(
                getattr(star, "root_dir_name", "") or getattr(star, "name", "") or ""
            )
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(star)
        return out

    def scan(self) -> dict:
        """扫描全部插件，返回 {providers, plugins, stats}。"""
        catalog = self.provider_catalog()
        by_id = {p["id"]: p for p in catalog}
        by_model: dict[str, dict] = {}
        for p in catalog:
            model = p.get("model") or ""
            if model and model not in by_model:
                by_model[model] = p

        plugins: list[dict] = []
        entries_total = 0
        for star in self._stars():
            info = self._scan_plugin(star, by_id, by_model)
            if info is None:
                continue
            plugins.append(info)
            entries_total += len(info["entries"])
        # 有模型配置的插件排前面，其次按插件名
        plugins.sort(key=lambda x: (0 if x["entries"] else 1, x["name"]))
        configured = sum(1 for p in plugins if p["entries"])
        logger.info(
            f"[ModelPanel] 插件模型扫描完成：{len(plugins)} 个插件，"
            f"{configured} 个含模型配置，共 {entries_total} 处；"
            f"provider 目录 {len(catalog)} 条"
        )
        return {
            "ok": True,
            "providers": catalog,
            "plugins": plugins,
            "stats": {
                "plugins_total": len(plugins),
                "plugins_configured": configured,
                "entries_total": entries_total,
                "providers_total": len(catalog),
            },
        }

    def _scan_plugin(
        self,
        star: Any,
        by_id: dict[str, dict],
        by_model: dict[str, dict],
    ) -> Optional[dict]:
        name = str(
            getattr(star, "root_dir_name", "") or getattr(star, "name", "") or ""
        )
        if not name or name == OWN_PLUGIN_NAME:
            return None
        display_name = str(
            getattr(star, "display_name", "") or getattr(star, "name", "") or name
        )
        cfg = getattr(star, "config", None)
        plugin_dir = self._plugin_dir(star, name)
        schema_map: dict[tuple, dict] = {}
        if plugin_dir:
            schema_map = self._schema_map(self._load_schema(plugin_dir))
        base = {
            "name": name,
            "display_name": display_name,
            "version": str(getattr(star, "version", "") or ""),
            "author": str(getattr(star, "author", "") or ""),
            "dir": plugin_dir,
            "config_path": str(getattr(cfg, "config_path", "") or "")
            if cfg is not None
            else "",
            "has_schema": bool(schema_map),
            "activated": bool(getattr(star, "activated", True)),
        }
        if not isinstance(cfg, dict):
            # 插件没有 _conf_schema.json（AstrBot 不会为它创建 config 对象）
            return {**base, "has_config": False, "entries": []}

        entries: list[dict] = []
        seen_paths: set[tuple] = set()

        # ---- pass 1：schema 声明 _special 的 provider 选择项（最准）----
        for path, node in schema_map.items():
            special = str(node.get("_special") or "")
            kind = SPECIAL_KIND_MAP.get(special, "")
            if not kind:
                continue
            for concrete in self._expand_paths(cfg, path):
                value = self._get_value(cfg, concrete)
                if not isinstance(value, str) or not value.strip():
                    continue
                if concrete in seen_paths:
                    continue
                seen_paths.add(concrete)
                entries.append(
                    self._make_entry(
                        path=concrete,
                        raw=value.strip(),
                        match="schema_special",
                        confidence="high",
                        node=node,
                        special=special,
                        default_kind=kind,
                        by_id=by_id,
                        by_model=by_model,
                    )
                )

        # ---- pass 2 & 3：遍历真实配置树（值命中 + 键名提示）----
        self._walk(
            cfg,
            (),
            entries=entries,
            seen_paths=seen_paths,
            schema_map=schema_map,
            by_id=by_id,
            by_model=by_model,
        )

        entries = self._dedupe(entries)
        return {**base, "has_config": True, "entries": entries}

    # ---------- 配置树遍历 ----------
    def _walk(
        self,
        node: Any,
        path: tuple,
        *,
        entries: list[dict],
        seen_paths: set[tuple],
        schema_map: dict[tuple, dict],
        by_id: dict[str, dict],
        by_model: dict[str, dict],
    ) -> None:
        if len(path) > _MAX_DEPTH:
            return
        if isinstance(node, str):
            self._consider_value(
                node,
                path,
                entries=entries,
                seen_paths=seen_paths,
                schema_map=schema_map,
                by_id=by_id,
                by_model=by_model,
            )
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                self._walk(
                    item,
                    path + (index,),
                    entries=entries,
                    seen_paths=seen_paths,
                    schema_map=schema_map,
                    by_id=by_id,
                    by_model=by_model,
                )
            return
        if isinstance(node, dict):
            for key, value in node.items():
                # AstrBot 内部字段（template_list 的 __template_key 等）不是用户配置
                if str(key).startswith("__"):
                    continue
                self._walk(
                    value,
                    path + (str(key),),
                    entries=entries,
                    seen_paths=seen_paths,
                    schema_map=schema_map,
                    by_id=by_id,
                    by_model=by_model,
                )

    def _consider_value(
        self,
        raw: str,
        path: tuple,
        *,
        entries: list[dict],
        seen_paths: set[tuple],
        schema_map: dict[tuple, dict],
        by_id: dict[str, dict],
        by_model: dict[str, dict],
    ) -> None:
        text = raw.strip()
        if not text or len(text) > _MAX_JSON_LEN:
            return
        # JSON 字符串映射（如陪伴插件 model_fallback_overrides="{\"KEY\":\"pid\"}"）：
        # 展开成多条内层条目；容器本身不再单独作为一条。
        # 注意：JSON 映射内层**不做键名提示**——像 task_prompt_overrides 这种
        # "provider key → 提示词正文" 的映射，内层键名同样带 provider，
        # 靠键名猜会把提示词当模型；这里只信"值确实命中了 provider/模型"。
        inner = self._try_json_map(text)
        if inner is not None:
            for inner_key, inner_value in inner.items():
                if not isinstance(inner_value, str) or not inner_value.strip():
                    continue
                inner_path = path + (str(inner_key),)
                self._add_if_matched(
                    inner_path,
                    inner_value.strip(),
                    container=path,
                    entries=entries,
                    seen_paths=seen_paths,
                    schema_map=schema_map,
                    by_id=by_id,
                    by_model=by_model,
                    allow_key_hint=False,
                )
            return
        if path in seen_paths:
            return
        self._add_if_matched(
            path,
            text,
            container=None,
            entries=entries,
            seen_paths=seen_paths,
            schema_map=schema_map,
            by_id=by_id,
            by_model=by_model,
        )

    def _add_if_matched(
        self,
        path: tuple,
        value: str,
        *,
        container: Optional[tuple],
        entries: list[dict],
        seen_paths: set[tuple],
        schema_map: dict[tuple, dict],
        by_id: dict[str, dict],
        by_model: dict[str, dict],
        allow_key_hint: bool = True,
    ) -> None:
        if value in by_id:
            entries.append(
                self._make_entry(
                    path=path,
                    raw=value,
                    match="provider_id",
                    confidence="high",
                    node=self._lookup_schema(schema_map, path),
                    container=container,
                    by_id=by_id,
                    by_model=by_model,
                )
            )
            return
        if value in by_model:
            entries.append(
                self._make_entry(
                    path=path,
                    raw=value,
                    match="model",
                    confidence="medium",
                    node=self._lookup_schema(schema_map, path),
                    container=container,
                    by_id=by_id,
                    by_model=by_model,
                )
            )
            return
        # 值既不是已知 provider id 也不是已知模型名 → 只有键名像模型配置才收
        if not allow_key_hint:
            return
        if len(value) > _MAX_VALUE_LEN or "\n" in value:
            return
        key = str(path[-1]) if path else ""
        if not key or not _KEY_HINT.search(key) or _KEY_HINT_EXCLUDE.search(key):
            return
        # template_list 模板内的字段（LoRA 列表的 model_name、工作流卡片的
        # base_model…）是插件自己的业务模型，不是 AstrBot provider，靠键名猜
        # 只会产生噪音（如 comfyui-anima 的 LoRA 文件名）。这里只认 pass1 的
        # schema `_special` 或值命中，不做键名提示。
        if self._is_template_field(schema_map, path):
            return
        node = self._lookup_schema(schema_map, path)
        if node is not None:
            # schema 已声明：枚举项 / 非字符串类型一律不是模型配置
            if node.get("options"):
                return
            if str(node.get("type") or "") not in ("string", ""):
                return
        entries.append(
            self._make_entry(
                path=path,
                raw=value,
                match="key_hint",
                # schema 明确声明过这个键（类型 string、不是枚举）→ 可信度中等；
                # 完全靠键名猜的（配置里多出来的历史键）→ 低可信
                confidence="medium" if node is not None else "low",
                node=node,
                container=container,
                by_id=by_id,
                by_model=by_model,
            )
        )

    @staticmethod
    def _try_json_map(text: str) -> Optional[dict]:
        """把 `{"k": "v"}` 形态的字符串解析成 dict；不是对象就返回 None。"""
        if not text.startswith("{"):
            return None
        try:
            data = json.loads(text)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    # ---------- schema ----------
    @staticmethod
    def _load_schema(plugin_dir: str) -> dict:
        path = os.path.join(plugin_dir, "_conf_schema.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:
            logger.debug(f"[ModelPanel] 读取 schema 失败 {path}: {e}")
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _schema_map(
        cls,
        schema: dict,
        prefix: tuple = (),
    ) -> dict[tuple, dict]:
        """把嵌套 schema 拍平成 {path: node}；template_list 的字段用 None 占位索引。"""
        out: dict[tuple, dict] = {}
        if not isinstance(schema, dict):
            return out
        for key, node in schema.items():
            if not isinstance(node, dict):
                continue
            path = prefix + (str(key),)
            node_type = str(node.get("type") or "")
            if node_type == "object" and isinstance(node.get("items"), dict):
                out.update(cls._schema_map(node["items"], path))
                continue
            if node_type == "template_list" and isinstance(node.get("templates"), dict):
                for tpl in node["templates"].values():
                    if isinstance(tpl, dict) and isinstance(tpl.get("items"), dict):
                        out.update(cls._schema_map(tpl["items"], path + (None,)))
                continue
            out[path] = node
        return out

    @staticmethod
    def _is_template_field(schema_map: dict[tuple, dict], path: tuple) -> bool:
        """该路径是否落在 template_list 模板内（schema 里用 None 占位索引）。

        两种入参都要支持：具体配置路径（`loras.0.model_name`）与 schema 表里的
        占位路径（`loras.None.model_name`）。
        """
        if any(seg is None for seg in path):
            return tuple(path) in schema_map
        if tuple(path) in schema_map:
            return False
        alt = tuple(None if isinstance(seg, int) else seg for seg in path)
        return alt != tuple(path) and alt in schema_map

    @staticmethod
    def _lookup_schema(schema_map: dict[tuple, dict], path: tuple) -> Optional[dict]:
        node = schema_map.get(tuple(path))
        if node is not None:
            return node
        # template_list：索引段回退成 None 再查一次
        if any(isinstance(seg, int) for seg in path):
            alt = tuple(None if isinstance(seg, int) else seg for seg in path)
            return schema_map.get(alt)
        return None

    # ---------- 取值 / 构造条目 ----------
    @staticmethod
    def _expand_paths(cfg: Any, path: tuple) -> list[tuple]:
        """把可能含 None（template_list 占位）的 schema 路径展开成具体路径。"""
        results: list[tuple] = [()]
        for seg in path:
            new_results: list[tuple] = []
            for prefix in results:
                if seg is None:
                    node = PluginModelScanner._get_value(cfg, prefix, missing=None)
                    if not isinstance(node, list):
                        continue
                    for index in range(len(node)):
                        new_results.append(prefix + (index,))
                else:
                    new_results.append(prefix + (seg,))
            results = new_results
            if not results:
                return []
        return results

    @staticmethod
    def _get_value(cfg: Any, path: tuple, missing: Any = None) -> Any:
        node = cfg
        for seg in path:
            if isinstance(seg, int):
                if not isinstance(node, list) or not (0 <= seg < len(node)):
                    return missing
            else:
                if not isinstance(node, dict) or seg not in node:
                    return missing
            node = node[seg]
        return node

    def _make_entry(
        self,
        *,
        path: tuple,
        raw: str,
        match: str,
        confidence: str,
        node: Optional[dict],
        by_id: dict[str, dict],
        by_model: dict[str, dict],
        special: str = "",
        default_kind: str = "",
        container: Optional[tuple] = None,
    ) -> dict:
        node = node or {}
        hit: dict = by_id.get(raw) or by_model.get(raw) or {}
        provider_kind = str(hit.get("kind") or "") or default_kind
        model = str(hit.get("model") or "")
        vendor = str(hit.get("vendor") or "")
        if str(hit.get("id") or "") == raw:
            display = str(hit.get("label") or raw)
            provider_id = raw
        elif hit:
            display = str(hit.get("label") or raw)
            provider_id = str(hit.get("id") or "")
        else:
            display = raw
            provider_id = ""
        display_path = _path_display(list(path))
        if container:
            container_display = _path_display(list(container))
            display_path = f'{container_display}["{path[-1]}"]'
        label = str(node.get("description") or "") or str(path[-1])
        hint = str(node.get("hint") or "")
        options = node.get("options")
        return {
            "path": list(path),
            "container": list(container) if container else None,
            "key": str(path[-1]),
            "path_display": display_path,
            "label": label,
            "hint": hint[:400],
            "value": raw,
            "provider_id": provider_id,
            "resolved": bool(hit),
            "match": match,
            "confidence": confidence,
            "provider_kind": provider_kind,
            "model": model,
            "vendor": vendor,
            "display": display,
            "special": special,
            "schema_options": list(options) if isinstance(options, list) else None,
            "conflict": False,
            "mirrored": False,
        }

    def _dedupe(self, entries: list[dict]) -> list[dict]:
        """识别「顶层扁平副本 + 分组嵌套」这类镜像配置，保留信息最全的那条。

        只对「普通 dict 键」生效：JSON 映射内层与列表元素本来就可能重名，
        不参与合并。值不一致时不做合并，而是双双标记 conflict，提示用户去看。
        """
        plain: list[dict] = []
        keep_always: list[dict] = []
        for entry in entries:
            if entry.get("container") or any(
                isinstance(seg, int) for seg in entry["path"]
            ):
                keep_always.append(entry)
            else:
                plain.append(entry)

        groups: dict[str, list[int]] = {}
        for index, entry in enumerate(plain):
            groups.setdefault(entry["key"], []).append(index)

        drop: set[int] = set()
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            values = {plain[i]["value"] for i in indexes}
            if len(values) > 1:
                for i in indexes:
                    plain[i]["conflict"] = True
                continue
            for i in indexes:
                for j in indexes:
                    if i == j or i in drop:
                        continue
                    if _is_strict_suffix(plain[i]["path"], plain[j]["path"]):
                        drop.add(i)
            survivors = [i for i in indexes if i not in drop]
            if len(survivors) < len(indexes):
                for i in survivors:
                    plain[i]["mirrored"] = True
        return [e for i, e in enumerate(plain) if i not in drop] + keep_always

    # ---------- 插件目录 ----------
    @staticmethod
    def _plugin_dir(star: Any, name: str) -> str:
        module = getattr(star, "module", None)
        file_path = getattr(module, "__file__", None)
        if isinstance(file_path, str) and file_path:
            try:
                return os.path.dirname(os.path.abspath(file_path))
            except Exception:
                pass
        return os.path.join("data", "plugins", name)

    # ================= 写回 =================
    def set_values(self, items: Any) -> dict:
        """按 {plugin, path, value} 批量写回插件配置。

        参数 items 结构：
            [{"plugin": "astrbot_plugin_x", "path": ["a", "b"], "value": "provider_id"}]
        path 支持字符串键与整数下标；value 为空字符串表示清除该处配置。
        """
        if not isinstance(items, list) or not items:
            return {"ok": False, "error": "items 不能为空", "changed": []}

        stars = {
            str(
                getattr(s, "root_dir_name", "") or getattr(s, "name", "") or ""
            ): s
            for s in self._stars()
        }
        known_ids = {p["id"] for p in self.provider_catalog()}

        changed: list[dict] = []
        errors: list[dict] = []
        unknown_values: list[str] = []
        touched: dict[str, dict] = {}

        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            plugin_name = str(raw_item.get("plugin") or "").strip()
            value = str(raw_item.get("value") or "").strip()
            path = self._normalize_path(raw_item.get("path"))
            container = self._normalize_path(raw_item.get("container"))
            if not plugin_name or not path:
                errors.append({"plugin": plugin_name, "error": "缺少 plugin 或 path"})
                continue
            star = stars.get(plugin_name)
            if star is None:
                errors.append({"plugin": plugin_name, "error": "插件未加载"})
                continue
            cfg = getattr(star, "config", None)
            if not isinstance(cfg, dict):
                errors.append({"plugin": plugin_name, "error": "插件没有可写配置"})
                continue

            if value and known_ids and value not in known_ids:
                unknown_values.append(value)

            old_value = self._get_value(cfg, tuple(path))
            if old_value is None and container:
                old_value = self._read_json_map_value(cfg, container, str(path[-1]))
            if not isinstance(old_value, str):
                errors.append(
                    {
                        "plugin": plugin_name,
                        "path_display": _path_display(path),
                        "error": "该配置项不存在或不是字符串，暂不支持修改",
                    }
                )
                continue

            if container:
                ok = self._set_json_map_value(cfg, container, str(path[-1]), value, plugin_name)
            elif self._is_companion_slot(plugin_name, path):
                # 陪伴插件：全量同步扁平副本 + 分组嵌套
                self._flat_set(cfg, str(path[-1]), value)
                ok = True
            else:
                ok = self._set_value(cfg, tuple(path), value)
            if not ok:
                errors.append(
                    {
                        "plugin": plugin_name,
                        "path_display": _path_display(path),
                        "error": "写入失败（路径已失效）",
                    }
                )
                continue

            changed.append(
                {
                    "plugin": plugin_name,
                    "plugin_display": str(
                        getattr(star, "display_name", "") or plugin_name
                    ),
                    "path": list(path),
                    "container": list(container) if container else None,
                    "path_display": _path_display(path),
                    "old": old_value,
                    "new": value,
                    "runtime_synced": False,
                }
            )
            touched.setdefault(plugin_name, {"star": star, "changes": []})[
                "changes"
            ].append((list(path), old_value, value))

        if not changed:
            return {
                "ok": False,
                "error": errors[0]["error"] if errors else "没有可写入的改动",
                "changed": [],
                "errors": errors,
            }

        # 落盘：每个被改动的插件保存一次
        saved: list[str] = []
        for plugin_name, info in touched.items():
            cfg = getattr(info["star"], "config", None)
            try:
                save = getattr(cfg, "save_config", None)
                if callable(save):
                    save()
                    saved.append(plugin_name)
            except Exception as e:
                logger.warning(f"[ModelPanel] 保存 {plugin_name} 配置失败: {e}")
                errors.append({"plugin": plugin_name, "error": f"保存失败: {e}"})

        # 运行时同步
        runtime_synced = 0
        for plugin_name, info in touched.items():
            if plugin_name == COMPANION_PLUGIN_NAME:
                sync = getattr(self.host, "_sync_companion_runtime", None)
                if callable(sync):
                    try:
                        sync()
                        runtime_synced += 1
                    except Exception as e:
                        logger.warning(f"[ModelPanel] 同步陪伴插件运行时失败: {e}")
                continue
            runtime_synced += self._sync_plugin_attributes(
                info["star"], info["changes"], changed
            )

        logger.info(
            f"[ModelPanel] 插件模型写入：{len(changed)} 处 / {len(saved)} 个插件，"
            f"运行时同步 {runtime_synced} 处，错误 {len(errors)} 条"
        )
        return {
            "ok": True,
            "changed_count": len(changed),
            "plugins_saved": saved,
            "runtime_synced": runtime_synced,
            "changed": changed,
            "errors": errors,
            "unknown_values": sorted(set(unknown_values)),
        }

    # ---------- 写回细节 ----------
    def _is_companion_slot(self, plugin_name: str, path: list) -> bool:
        """陪伴插件的 provider 位置：顶层 key 或 model_assignment_config.<key>。"""
        if plugin_name != COMPANION_PLUGIN_NAME or not path:
            return False
        if not isinstance(path[-1], str):
            return False
        return len(path) == 1 or str(path[0]) == COMPANION_PROVIDER_GROUP

    @staticmethod
    def _normalize_path(raw: Any) -> list:
        if not isinstance(raw, list):
            return []
        out: list = []
        for seg in raw:
            if isinstance(seg, bool):
                return []
            if isinstance(seg, int):
                out.append(seg)
            elif seg is None:
                return []
            else:
                out.append(str(seg))
        return out

    @classmethod
    def _set_value(cls, cfg: Any, path: tuple, value: str) -> bool:
        if not path:
            return False
        node = cfg
        for seg in path[:-1]:
            if isinstance(seg, int):
                if not isinstance(node, list) or not (0 <= seg < len(node)):
                    return False
            else:
                if not isinstance(node, dict) or seg not in node:
                    return False
            node = node[seg]
        last = path[-1]
        if isinstance(last, int):
            if not isinstance(node, list) or not (0 <= last < len(node)):
                return False
        else:
            if not isinstance(node, dict) or last not in node:
                return False
        node[last] = value
        return True

    def _read_json_map_value(self, cfg: Any, container: list, key: str) -> Any:
        raw = self._get_value(cfg, tuple(container))
        if not isinstance(raw, str):
            return None
        data = self._try_json_map(raw.strip())
        if data is None:
            return None
        return data.get(key)

    def _set_json_map_value(
        self,
        cfg: Any,
        container: list,
        key: str,
        value: str,
        plugin_name: str,
    ) -> bool:
        raw = self._get_value(cfg, tuple(container))
        if not isinstance(raw, str):
            return False
        data = self._try_json_map(raw.strip()) if raw.strip() else {}
        if data is None:
            return False
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        # 陪伴插件：容器是顶层 legacy 键，用 _flat_set_existing 避免被塞进分组
        if plugin_name == COMPANION_PLUGIN_NAME and len(container) == 1:
            self._flat_set_existing(cfg, str(container[0]), encoded)
            return True
        return self._set_value(cfg, tuple(container), encoded)

    def _sync_plugin_attributes(
        self,
        star: Any,
        changes: list[tuple],
        changed: list[dict],
    ) -> int:
        """尽力而为地把顶层单键改动同步到插件实例属性。

        很多插件在 bootstrap 时把配置读进实例属性（`self.xxx = config.get("xxx")`），
        之后只读属性、不再读 config。我们无法通用地知道它缓存了哪些字段，
        所以只在「属性名 == 配置键名 且 属性当前值 == 我们看到的旧值」时同步，
        这样绝不会误改插件内部状态。
        """
        obj = getattr(star, "star_cls", None)
        if obj is None:
            obj = star
        star_name = str(
            getattr(star, "root_dir_name", "") or getattr(star, "name", "") or ""
        )
        synced = 0
        for path, old, new in changes:
            if len(path) != 1 or not isinstance(path[0], str):
                continue
            key = path[0]
            try:
                current = getattr(obj, key, None)
            except Exception:
                continue
            if not isinstance(current, str) or current != old:
                continue
            try:
                setattr(obj, key, new)
                synced += 1
                for item in changed:
                    if item.get("plugin") == star_name and item.get("path") == path:
                        item["runtime_synced"] = True
            except Exception:
                continue
        return synced
