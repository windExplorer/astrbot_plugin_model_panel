# 更新日志

## v1.2.7

- 修复「分组一键测试」不遵守勾选规则：分组内未勾选的模型也会被真实检测（既浪费额度，
  也违反"勾选 = 参与检测"的约定）
  - 根因：前端 `testMany(ids)` 在分组模式下把 `skip` 直接置空（`ids ? [] : …`），
    后端 `api_test_all_stream` 又只按 `ids` 缩小范围、不剔除未勾选项 ——
    于是"取消勾选"实际只对一键检测生效
  - 修复（前端 + 后端双保险）：
    - `DetectView.testMany` 无论全测还是分组测都传完整 skip；判定语义与复选框一致
      （`enabled[k] === false` 才算未勾选，undefined 视为已勾选）
    - 新增 `ModelPanelPlugin._skip_ids()`：以 storage 里的检测开关偏好兜底合并 skip，
      `api_test_all_providers` / `api_test_all_stream` 都改用，保证任何调用路径下
      「没勾选就不测」
  - 分组内被跳过的模型仍会出现在结果里（标「已跳过」），只是不再发真实请求
- 修复「插件模型」页把插件自己的业务模型当成模型配置列出
  （如 comfyui-anima 的 LoRA 文件名 `anima_lacrimosa.safetensors`、工作流底模 `illustrious`）
  - 根因：键名提示（`model_name` / `base_model` 命中 "model"）对 `template_list`
    模板内的字段同样生效；而模板条目是用户自定义的列表项，里面的模型字段属于
    该插件自己的业务（绘图模型、LoRA 文件），并不是 AstrBot 的 provider
  - 修复：新增 `_is_template_field()`，`template_list` 模板内的字段**只认**
    schema `_special` 声明与值命中，不做键名提示；该方法同时兼容具体配置路径
    （`loras.0.model_name`）与 schema 占位路径（`loras.None.model_name`）
  - 前端「低可信项」默认改为**不显示**（此前默认显示）：这类条目绝大多数就是
    插件自己的业务模型字段（如 `nai_model`），需要排查时再勾选显示
- 修复 provider 目录不含重排序模型，并补上"默认 provider"信息
  - AstrBot 支持的 provider 有 5 类：chat / tts / stt / embedding / rerank，
    少一类就会出现「插件里明明配了这类模型，却被标成未匹配、下拉里也选不到」
  - `rerank` 在 AstrBot 4.27.4 的 `Context` 上**没有 getter**，改为直接取
    `provider_manager.rerank_provider_insts`
  - 非对话 provider 的模型名可能写在 `embedding_model` / `rerank_model` /
    `tts_model` / `stt_model` 等字段，补进取值兜底
  - provider 目录新增 `is_default`：chat 取 `provider_settings.default_provider_id`
    （及 `default_chat_provider_id` 快照），tts / stt 取
    `provider_tts_settings.provider_id` / `provider_stt_settings.provider_id`，
    前端下拉里标「（默认）」。embedding / rerank 在 AstrBot 主配置里**没有**
    全局默认项（由使用它们的插件各自指定），故不标

## v1.2.6

- 新增「插件模型」页：自动扫描**所有已安装插件**的配置，把其中的模型项列出来，
  可直接单项更换，也可按「旧模型 → 新模型」批量替换
  - 需求：换模型时不该只盯着陪伴插件——别的插件（视觉模型、TTS、向量模型…）
    也各自配了一份模型，全靠人工去每个插件的配置页翻找，容易漏
  - 识别来源按可信度分三级，前端逐条标注：
    1. **schema 声明（high）**：`_conf_schema.json` 里 `_special` 为
       `select_provider` / `select_provider_tts` / `select_provider_stt` 的配置项。
       这是 AstrBot 官方约定，值一定是 provider id，识别最准
    2. **值命中（high / medium）**：配置树里任意字符串值等于某个已加载 provider 的 id
       （high）或模型名（medium）；JSON 字符串里套着 `{key: provider_id}` 映射的
       （如陪伴插件 `model_fallback_overrides`）会展开成多条
    3. **键名提示（low）**：键名含 `provider` / `model`、值为非空短字符串，
       但既不是已知 id 也不是已知模型名（典型场景：这个模型已经被删掉了）。
       标注「低可信」，可一键隐藏
  - 后端新增模块 `plugin_models.py`：
    - 拍平 `_conf_schema.json`（含 `object` 分组与 `template_list` 模板）得到「路径级 schema 表」
    - 递归遍历插件 config；跳过 `__` 前缀的 AstrBot 内部字段（如 `__template_key`）
    - **镜像去重**：同一个 key 既在顶层扁平副本又在分组嵌套里（陪伴插件就是这种结构），
      值相同时只保留最深的一条并标注「镜像」；值不同则两条都列并标「配置不一致」
    - **写回**：按 path 精确写回（支持 dict 键、list 下标、JSON 映射内层）；
      陪伴插件走 `_flat_set` 语义全量同步（否则「改了配置、插件读到的还是旧值」）；
      写前校验原值必须是字符串，路径失效则报错且不改
    - **运行时同步**：插件实例属性名 == 配置键名、且属性当前值 == 我们看到的旧值时，
      才 `setattr` 同步（很多插件 bootstrap 时把配置读进实例属性、之后不再读 config）；
      其余情况由前端提示「可能需要重载插件」
    - 误报防护：schema 带 `options` 的枚举项、非字符串类型不参与「键名提示」
      （否则 `provider_config_mode=quick` 这类会被当成模型）；
      JSON 映射内层只信「值确实命中」，避免把 `task_prompt_overrides` 里的提示词正文
      当成模型（它的内层键名同样带 provider）
  - 前端新增 `webui-src/src/views/PluginsView.vue`（导航「插件模型」）：
    - 按插件折叠展示：用途（优先取 schema description）/ 配置键 / 当前模型 / 更换为 / 状态，
      条目里可展开看 schema `hint`
    - 顶部支持搜索、显示低可信项、隐藏无模型配置的插件，并统计「N / M 个插件含模型配置」
    - **批量替换**：选一个「旧模型」→「新模型」，只作用于当前筛选出的列表，弹窗确认后再写
    - 状态标签：未匹配 / 配置不一致 / 低可信 / 镜像；TTS、STT、向量模型单独标注类型；
      更换下拉按 provider 类型分组，同名模型附加 provider id 后缀区分渠道
  - 新增接口：`GET /panel/plugin_models`（扫描，只读）、
    `POST /panel/plugin_models/set`（按 `{plugin, path, value}` 批量写回）
  - 与「陪伴插件」页的关系：陪伴页保留主模型 / 备用模型的专用语义（按模型名批量替换、
    同步陪伴插件实例属性）；本页是通用视图。两者读写同一份配置，改完互相可见

## v1.2.5

- 修复陪伴插件页模型选择在**同名模型**场景下只显示一个提供商的问题：
  不同 provider 渠道配置同一个模型名（如两个 openai 中转都用 `gpt-4o`）时，
  后端 `/panel/providers` 构建 `provider_models` 按 model 名去重，
  只保留第一个渠道，其余渠道在下拉里消失（默认模型页不受影响，
  它直接使用全量 provider 列表）
  - 后端：`provider_models` 改为每个 provider 一条、全量返回（不按 model 去重）
  - 前端：下拉对重复的 label（vendor · model 相同）附加 provider id 后缀，
    保证每个渠道都可区分；写回值仍是 provider id，与陪伴插件配置直接匹配

## v1.2.4

- 修复多语言缺 key 导致界面显示原始 key 的问题：
  - 模型检测「查看详情」按钮悬浮提示与详情弹窗的 11 个字段
    （`detect.detail.*`）在四个语言文件里写成了带点号的扁平 key，
    vue-i18n 按 `.` 拆嵌套路径查不到，回退显示 `detect.detail.id` 之类的原文；
    现已全部改为嵌套结构（zh / en / ja / ko）
  - en.json 文件末尾存在重复的顶层 `nav` 对象（仅含 settings），
    JSON 同名 key 相互覆盖，导致英文界面导航丢失 4 个翻译回退显示中文；已合并
- 修复陪伴插件页面高度异常：scoped 样式里本意控制弹窗的
  `:deep(.n-card)` 限高规则实际命中的是页面主体卡片
  （弹窗 teleport 到 body 后 scoped 根本管不到），
  导致主体卡片底边超出可视区、需滚动父元素才能看全，且内容区双层滚动；
  现已移除该规则，主体卡片恢复自然高度，弹窗尺寸仍由全局样式正确控制
- 打包脚本 `_bump_version.ps1` 版本正则从写死的 `v0.x.y` 改为通用
  `v{major}.{minor}.{patch}`，进入 1.x 后自动 bump 不再误判为"无版本号"

## v1.2.3

- metadata 信息修正：
  - `author` 改为「涟漪」（原 `local`）
  - `category` 改为 `utilities`（工具类）。AstrBot 市场分类使用英文 key 枚举
    （ai_tools / utilities / productivity / integrations / entertainment / other），
    中文标签由 Dashboard 映射；原先填写的中文「AI 增强」无法被市场分类筛选正确归并

## v1.2.1

- 回退对话模型列表视觉与交互重做（改为卡片化，解决"不够醒目、小家子气"）：
  - 每个模型独占一张卡片：主标题显示模型名、副标题显示供应商，
    序号改为醒目的圆形徽标，内边距与字号整体加大
  - 右侧操作按钮加大到 30×30，删除键与上下移之间加分隔线，缓解误触
  - 拖拽更易发现：整张卡片即可拖动（光标变抓手），手柄改为点阵抓手图标，
    右栏标题下常驻「按住卡片可上下拖拽调整顺序」提示，拖拽落位显示插入指示线
  - 左右两栏等高对齐：列表区用 flex 撑满，右栏补一行与搜索框等高的提示行，
    列表最小高度提升到 288px
- 左侧模型改为**选中态切换**：点击后保持显示并高亮（角标显示回退序号），
  同时加入右侧；再点一次取消选中并从右侧移除；移除原来的加号图标

## v1.2.0

- 回退对话模型列表改为**拖拽排序**（原来的多选下拉无法表达回退优先级）：
  - 左侧「可选模型」支持关键字搜索，点击即加入右侧
  - 右侧「回退顺序」列表可拖拽调整，序号即优先级（1 为最先尝试）
  - 拖拽时目标项显示插入指示线；拖到列表空白处则移到末尾
  - 保留上移 / 下移 / 删除按钮，方便触屏与无鼠标场景
  - 已选模型不再出现在左侧，避免重复添加
- 拖拽用原生 HTML5 拖放实现，未引入 sortablejs 等额外依赖（不增加包体积）

## v1.1.0

- 首页控制台改版，新增用量统计：
  - 顶部新增「今日 Token」统计卡片（与模型数 / 默认模型 / 陪伴插件并列）
  - 新增 Token 用量区块：今日用量、累计用量、今日请求次数，
    并拆分展示输入 / 输出 / 缓存 token
  - 新增近 7 天用量柱状趋势图（纯 CSS 实现，不引入图表库）
  - 新增今日模型用量 Top 排行（按模型聚合，附带请求次数）
- 用量采集：新增 `on_llm_response` 钩子，从 `LLMResponse.usage`
  （input_other / input_cached / output）记录每次调用，写入新表 `llm_usage`；
  流式分片与 usage 为空的调用跳过，避免重复计数
- 模型归属：按响应里的模型名反查 provider id，带缓存，避免每次调用都枚举 provider
- 用量数据保留 90 天，插件启动时自动清理过期记录
- 默认模型展示改为「供应商 · 模型」友好名，并保留 provider id 便于核对
- 卡片样式优化：圆角、悬浮抬升、强调色数字、等宽数字对齐；
  数字超过千/百万自动缩写为 k / M

## v1.0.0

- 默认模型页升级为**可配置**（此前只是只读展示），支持三项：
  1. 默认对话模型：下拉选择，留空则由 AstrBot 使用第一个可用模型
  2. 回退对话模型列表：多选并按顺序保存，主模型请求失败时依次切换
  3. 默认图片转述模型：下拉选择，留空表示不使用图片转述
- 兼容新旧两套 AstrBot 配置结构：新版写入
  `agent_runner.config.model.provider_id` / `fallback_provider_ids`，
  同时镜像旧键 `provider_settings.default_provider_id` / `fallback_chat_models`，
  旧版 AstrBot 也能正常生效；读取时同样新旧择优
- 保存后同步 provider_manager 的 `default_chat_provider_id` 运行时快照
  （该属性是加载时快照，不随配置改动刷新），改完即时生效
- 页面展示「当前实际生效」的对话模型，便于核对配置是否真的落到了运行时
- 版本进入 1.0.0

## v0.6.12

- 陪伴插件新增「总览」页：列出所有用途（key）与当前主模型 / 备用模型，一眼看清每个场景配了什么模型
- 未配置项现在可直接在总览页的主模型 / 备用模型下拉里选择并即时保存（新增 `POST /panel/companion/set` 接口，单 key 直接设 / 清 provider_id，并同步陪伴插件运行时实例属性，改完即时生效）
- 升级版本至 v0.6.12

## v0.6.11

- 修复陪伴插件「私读视觉模型」（`PRIVATE_READING_VISION_PROVIDER_ID`）在替换页显示成
  `<object object at 0x...>` 的 bug：根因是 `_flat_get` 找不到配置项时返回哨兵 `_MISSING`
  （一个 `object` 实例），而取值处用 `if raw` 误判其为真值并 `str()` 成怪异字符串；
  现已显式排除哨兵，未配置项正确归到「未配置」区

## v0.6.10

- 打开替换页时若发现陪伴插件内存里的备用模型落后于配置，自动纠正一次
- 修复陪伴插件「备用模型」替换后不生效：只 save_config() 写文件不会刷新陪伴插件
  运行时的 `model_fallback_overrides` 实例属性（其 WebUI 优先读实例属性），
  现在替换后会用陪伴插件自带的归一化方法同步该属性，改完即时可见
- 备用模型写回改为保留未收录的 provider key，避免陪伴插件升级新增 key 后
  被我们静默抹掉
- 备用模型配置不再写入 `model_assignment_config` 分组（它是 legacy 顶层配置）
- 补齐陪伴插件新增的 provider key `REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID`
- 替换结果区分主模型 / 备用模型数量；陪伴插件内存值仍不一致时给出提示

## v0.6.8

- 安全审查：API 鉴权、输入校验、文件读写范围均符合规范，无已知安全问题
- 例行发布，功能与 v0.6.7 一致

## v0.6.7

- 新增更新日志 CHANGELOG.md（插件市场可查看版本记录）
- 规范 metadata.yaml：补充 `documentation` 更新日志引用与 `short_desc` 短描述
- 模型检测：分组单独一键测试、每行展示最近检测时间
- 陪伴插件模型替换：支持主模型 / 备用模型一键批量替换（精简配置 + 主次区分双 Tab）
- 检测记录持久化到 SQLite（WAL），为历史记录与统计图预留
- WebUI 萌化风格、新增图标、支持中 / 英 / 日 / 韩四语
- 各页面增加刷新按钮

## v0.6.0

- 插件更名「萌萌模型控制台」，定位自用：快速更换「我会永远陪着你」陪伴插件的模型
- 新增模型检测（延迟 + 存活）、默认模型查看、陪伴插件精准模型替换
