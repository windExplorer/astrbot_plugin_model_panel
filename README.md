# 模型控制台（astrbot_plugin_model_panel）

一个独立的 **AstrBot 模型管理与检测插件**，通过自带 WebUI（模型控制台）统一完成模型延迟/存活检测、排序、默认模型查看、伴侣插件精准模型一键替换，以及历史检测的持久化与统计。

## 功能

- **控制台**：全局总览（LLM 模型数、默认模型状态、伴侣插件状态、最近一次检测的存活率与累计检测统计）
- **模型检测排序**：
  - 读取全局 LLM（chat）模型，按供应商分组展示
  - 延迟检测 + 存活检测
  - 支持单独检测与一键检测
  - 一键检测采用「启动 + 轮询」异步任务，逐模型返回结果实时刷新（不等全部跑完）
  - 可配置失败重试次数与退避秒数（`_conf_schema.json`）
  - 检测结果按归一化错误码展示（成功 / 超时 / 失联 / 拒绝连接 / 鉴权失败 / 请求受限 / 模型不存在 / 服务异常 / 已跳过），不再把接口堆栈塞进前端
  - 检测开关：取消勾选后一键检测跳过该模型
  - 检测结果、勾选状态、上次摘要写入浏览器 `localStorage`，切换页面不丢失
- **默认模型**：只读展示 AstrBot 当前默认模型（写入能力后续提供）
- **伴侣插件模型替换**：读取伴侣插件（astrbot_plugin_private_companion）的精准模型配置；展示配置模式 / 已配置数量 / 未配置项；支持一键批量替换，未选择替换的保持原样
- **历史存储**：检测历史以 SQLite（WAL 模式）写入 AstrBot 数据目录，跨重启保留；超过保留天数自动清理

## 配置项（`_conf_schema.json`）

| 键名 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `test_timeout` | int | 45 | 单次检测超时（秒） |
| `test_retry_count` | int | 1 | 检测失败后自动重试次数（0 = 不重试） |
| `test_retry_backoff` | float | 2.0 | 重试退避（秒），按 (1×, 2×, 3×…) 线性退避 |
| `history_retention_days` | int | 30 | 历史保留天数（0 = 永久保留） |

## 安装

1. 克隆本仓库到 AstrBot `data/plugins/` 目录
2. 在 AstrBot 面板启用插件
3. 在面板中打开「模型控制台」页面

依赖 AstrBot `>= 4.22.0`、Python 包 `aiosqlite`。

## 开发

- 后端：
  - `main.py`（AstrBot Star 插件，注册 `/panel/*` Web API）
  - `storage.py`（SQLite + WAL 存储层）
  - `session_manager.py`（一键检测异步任务与轮询状态）
- WebUI：`webui-src/`（Vue3 + Naive UI + Vite + vue-i18n，hash 路由，构建产物输出到 `pages/model-panel/`）
- 构建 WebUI：`.\build_webui.ps1`
- 打包插件：`.\build_zip.ps1`（自动递增版本号并打包到 `dist/`）

## 多语言

WebUI 支持中 / 英 / 日 / 韩四语，默认中文，左下角可切换。文本集中在 `webui-src/src/locales/{zh,en,ja,ko}.json`，**中文为源语言**。新增 UI 时务必同步四份翻译。

## 项目文档

详见 `docs/设计文档.md`（含需求、技术方案、依赖的 AstrBot 核心 API）。

## 技术要点

- 模型列表：`context.get_all_providers()`（LLM chat 模型）
- 延迟/存活检测：`await provider.test(timeout)`，可配置重试次数与退避
- 错误归一化：基于关键字（timeout/connect/refused/auth/rate_limit/not_found/server/unknown）把异常映射到 `error_code`，原始消息截断到 80 字符
- 默认模型：`provider_manager.provider_settings["default_provider_id"]`
- 伴侣插件精准模型：`context.get_registered_star("astrbot_plugin_private_companion").config`（AstrBotConfig）
- 历史存储：`aiosqlite` + WAL，文件位于 `<AstrBot data>/model_panel.db`
- 异步一键检测：后端 `asyncio.create_task` + `SessionManager` 内存队列；前端 `apiPost` 启动 + `apiGet` 600ms 轮询 `/session/{id}`，逐项 push 到 UI

## 自动版本号

`build_zip.ps1` 与 `build_webui.ps1` 会在打包前调用 `_bump_version.ps1`，根据 git commit 数（minor）和 dist 中已存在的 patch 自动递增版本号（`v0.{commit_count}.{patch}`），写入 `metadata.yaml` 与 `webui-src/src/version.ts`。每次构建都会得到一个新版本号。
