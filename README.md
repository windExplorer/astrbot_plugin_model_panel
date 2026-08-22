# 模型控制台（astrbot_plugin_model_panel）

一个独立的 **AstrBot 模型管理与检测插件**，通过自带 WebUI（模型控制台）统一完成模型延迟/存活检测、排序、默认模型查看、以及伴侣插件精准模型的一键批量替换。

## 功能

- **控制台**：全局总览（LLM 模型数、默认模型状态、伴侣插件状态）
- **模型检测排序**：读取全局 LLM（chat）模型，延迟/存活检测，排序，单独检测/一键检测，检测开关（关闭后一键检测跳过该模型）
- **默认模型**：只读展示 AstrBot 当前默认模型（写入能力后续提供）
- **伴侣插件模型替换**：读取伴侣插件（astrbot_plugin_private_companion）的精准模型配置，一键批量替换，未选择替换的保持原样

## 安装

1. 克隆本仓库到 AstrBot `data/plugins/` 目录
2. 在 AstrBot 面板启用插件
3. 在面板中打开「模型控制台」页面

依赖 AstrBot `>= 4.22.0`。

## 开发

- 后端：`main.py`（AstrBot Star 插件，注册 `/panel/*` Web API）
- WebUI：`webui-src/`（Vue3 + Vite + hash 路由，构建产物输出到 `pages/model-panel/`）
- 构建 WebUI：`.\build_webui.ps1`

## 项目文档

详见 `docs/设计文档.md`（含需求、技术方案、依赖的 AstrBot 核心 API）。

## 技术要点

- 模型列表：`context.get_all_providers()`（LLM chat 模型）
- 延迟/存活检测：`await provider.test(timeout)`
- 默认模型：`provider_manager.provider_settings["default_provider_id"]`
- 伴侣插件精准模型：`context.get_registered_star("astrbot_plugin_private_companion").config`（AstrBotConfig）
