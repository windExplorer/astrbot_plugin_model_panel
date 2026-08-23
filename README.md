# 🐱 喵喵模型控制台（astrbot_plugin_model_panel）

> ⚠️ **自用插件**：本插件主要为我个人使用而开发，功能围绕自己的实际需求打磨，非通用商业产品。介意勿用，也欢迎 fork 自改。

## 初心

这个插件诞生的初衷其实很简单——**快速更换「我会永远陪着你」的模型**。

用 AstrBot 跑私人伴侣（[astrbot_plugin_private_companion](https://github.com/menglimi/astrbot_plugin_private_companion)）时，想换一个更聪明 / 更便宜的模型，总要去后台一个个改 provider 配置，麻烦又容易漏。于是做了这个面板：在 WebUI 里一键检测所有模型的延迟和存活、批量把旧模型替换成新模型，几秒钟搞定，不用碰后台。

> 若你也使用「我会永远陪着你」插件，强烈建议同时安装：[https://github.com/menglimi/astrbot_plugin_private_companion](https://github.com/menglimi/astrbot_plugin_private_companion)

## 功能

- **控制台总览**：LLM 模型数、默认模型状态、伴侣插件状态、最近一次检测存活率与累计统计
- **模型检测排序**：
  - 读取全局 LLM（chat）模型，按供应商分组展示，可**分组单独一键测试**
  - 延迟检测 + 存活检测，支持单测 / 全测 / 分组测
  - 一键检测采用「启动 + 轮询」异步任务，逐模型实时刷新
  - 可配置失败重试次数与退避（`_conf_schema.json`）
  - 错误归一化展示（成功/超时/失联/拒绝连接/鉴权失败/请求受限/模型不存在/服务异常/已跳过）
  - 检测开关、结果、上次摘要写入浏览器 localStorage，切换页面不丢失
  - 每行展示最近**检测时间**
- **默认模型**：展示 AstrBot 实际生效的默认模型（与运行时决议一致）
- **伴侣插件模型替换**：读取伴侣插件精准模型配置，一键批量替换主模型 / 备用模型
- **历史存储**：每次检测记录持久化到 SQLite（WAL），跨重启保留，为后续历史记录 / 统计图预留

## 配置项（`_conf_schema.json`）

| 键名 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `test_timeout` | int | 45 | 单次检测超时（秒） |
| `test_retry_count` | int | 1 | 检测失败后自动重试次数（0 = 不重试） |
| `test_retry_backoff` | float | 2.0 | 重试退避（秒），按 (1×, 2×, 3×…) 线性退避 |
| `history_retention_days` | int | 30 | 历史保留天数（0 = 永久保留） |

可在 WebUI「设置」页直接修改并保存，同步写入插件 `_conf_schema`。

## 安装

1. 克隆本仓库到 AstrBot `data/plugins/` 目录
2. 在 AstrBot 面板启用插件
3. 打开「喵喵模型控制台」页面

依赖 AstrBot `>= 4.22.0`、Python 包 `aiosqlite`。

## 开发

- 后端：`main.py`（AstrBot Star 插件，注册 `/panel/*` Web API）、`storage.py`（SQLite + WAL）、`session_manager.py`（一键检测异步任务与轮询状态）
- WebUI：`webui-src/`（Vue3 + Naive UI + Vite + vue-i18n，hash 路由，产物 `pages/model-panel/`）
- 构建 WebUI：`.\build_webui.ps1`
- 打包插件：`.\build_zip.ps1`（自动递增版本号并打包到 `dist/`）

## 多语言

WebUI 支持中 / 英 / 日 / 韩四语，默认中文，左下角可切换。文本在 `webui-src/src/locales/{zh,en,ja,ko}.json`，**中文为源语言**。

## 技术要点

- 模型列表：`context.get_all_providers()`（LLM chat 模型）
- 延迟/存活检测：`await provider.test(timeout)`
- 默认模型：`provider_manager.get_using_provider_async(CHAT_COMPLETION)`（与 AstrBot 运行时决议一致）
- 伴侣插件精准模型：`context.get_registered_star("astrbot_plugin_private_companion").config`
- 历史存储：`aiosqlite` + WAL，位于 `<AstrBot data>/model_panel.db`
- 异步一键检测：后端 `asyncio.create_task` + `SessionManager`；前端 `apiPost` 启动 + `apiGet` 轮询 `/session/{id}`

## 版本号

当前主线 `v0.6.x`。`build_zip.ps1` 会在打包时根据 `metadata.yaml` 的版本自动递增 patch 并写入 `webui-src/src/version.ts`（先升版本再构建，保证前端版本号与插件一致）。
