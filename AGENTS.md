# AGENTS.md - Tenko 开发指南

本文档为参与 Tenko 项目开发的协作者提供约定和必要上下文。

## 项目定位

Tenko 是面向聊天群组的管理 bot，使用 Entari 作为运行时，以 Satori 协议抽象承接
消息、事件和平台动作。业务能力以插件形式组织，覆盖
权限、群管理、账号响应策略、功能开关、状态查询和宿主升级。插件逻辑不应假设
运行在特定聊天平台上；当前通过 OneBot 11 接入 QQ，新增平台通过协议适配层接入。

协议接入应以 OneBot 11 和 Satori 为边界，插件逻辑不要绑定到某一个具体协议端。
持久化数据使用 SQLite；数据库访问集中在 repository 和数据库服务中，避免在插件
里直接管理连接生命周期。

## 目录结构

```text
tenko/
├── __main__.py              # 命令行入口和 dry-run
├── config.py                # TOML 配置模型
├── connection.py            # OneBot 11 / Satori 连接服务
├── runtime.py               # Entari 运行时装配
├── context.py               # 消息上下文和身份转换
├── db/                      # 模型、迁移、数据库启动和 repositories
├── host/                    # 账号、权限、功能、限流和升级服务
├── plugins/                 # Tenko 原生插件
├── templates/               # 离线渲染模板和资源
└── webui/                   # WebUI 后端；static/ 是前端构建产物（勿手改）
packages/tenko-api/          # 第三方插件的稳定接口（uv workspace 成员，发布为 tenko-api）
examples/tenko-plugin-example/  # 第三方插件示例与模板
webui/                        # WebUI 前端工程（Vue 3 + TS + Vite + Element Plus）
tests/tenko/                  # 单元测试和集成式调用点测试
config/tenko.toml.example    # 配置模板
```

## 开发环境与命令

- 支持 Python `>=3.11,<3.14`。
- 依赖管理使用 uv（pyproject.toml 声明依赖，uv.lock 锁定版本）。
  在项目根目录执行 `uv sync` 创建/更新环境，随后用 `uv run <命令>` 执行
  （例如 `uv run pytest -q`、`uv run python -m tenko --dry-run`）。
  依赖变更优先使用 uv 命令，不要直接编辑 pyproject.toml 的依赖表：
  `uv add <包>` / `uv remove <包>` / `uv add "包==版本"`；锁文件由 uv 自动
  维护。
  服务器上的既有环境名为 `.venv-entari`，两种方式并存时以 uv 为准。

- 提交前至少执行：

  ```text
  ./.venv-entari/bin/ruff check tenko tests/tenko
  ./.venv-entari/bin/python -m pytest -q
  ```

- 只检查配置和连接信息、不启动网络服务：

  ```text
  ./.venv-entari/bin/python -m tenko --dry-run
  ```

- WebUI 前端在 `webui/`，用 npm（Node `^22.18` 或 `>=24.12`）：

  ```text
  cd webui && npm ci
  TENKO_BACKEND=http://127.0.0.1:8081 npm run dev   # 开发服务器 http://localhost:5173/webui/，代理 /webui/api
  npm run type-check && npm run lint && npm run build
  ```

  `npm run build` 的产物写入 `tenko/webui/static/`，**该目录提交进仓库**（自更新
  按 Git tag 发布它）。修改 `webui/` 后必须重新构建并一并提交，CI 在产物有差异时
  失败；不要手工编辑 `tenko/webui/static/`。浏览器测试
  （`tests/tenko/test_webui_browser*.py`）加载的是该构建产物。需要 Playwright 的
  Chromium，缺失时自动跳过；可用 `TENKO_WEBUI_STATIC=<目录>` 指向私有构建目录。

- 全量钩子检查使用 `pre-commit run --all-files`。测试默认只收集
  `tests/tenko`，新增测试应放在该目录并保持测试可重复、无真实外部连接。

## 插件开发要点

1. 插件放在 `tenko/plugins/` 下，使用 Entari 原生插件生命周期和命令注册机制，
   并通过 `plugin.metadata` 声明名称、版本、作者和能力。
2. 对外命令统一使用 `/` 前缀。命令解析、帮助内容和权限检查应复用现有的
   command manager、`tenko.context` 以及 `tenko.host` 服务。
   命令权限用 `tenko.host.guards` 的装饰器声明（第三方从 `tenko_api.perm` 导入）：
   `@require(Permission.X)`、`@require_group(...)`、`@group_only()`、`@private_only()`，
   放在 `@command.on(...)` 之下（之上也生效）。要求登记在处理函数的
   `__tenko_requires__` 上，`/帮助 <编号>` 据此显示；回复措辞统一由装饰器给出。
   只有要求取决于运行时数据（按参数、聊天类型或目标插件区分）时才在函数体里调用
   `permission_checker.require_perm`，并写一行注释说明原因。
   插件的群等级（默认 `ActiveGroup`，低于它的群里命令被静默忽略）由 `CommandPolicy`
   集中检查，不要在处理函数里再判断；内置插件需要别的等级时，在
   `plugin.get_plugin().metadata.group_level` 上设置，与 `feature_scope` 的写法一致。
3. 平台动作通过宿主动作服务执行，先确认账号能力、目标可用性和权限，再处理
   失败结果；不要在插件中复制协议层调用或吞掉未知异常。
4. 配置从 `tenko.config` 读取，数据库操作通过 `tenko.db.repositories` 等现有
   抽象完成。新增状态需要明确持久化路径、迁移策略和测试覆盖。
5. 插件加载和卸载都必须释放自己创建的资源。渲染、网络和定时任务应遵循宿主
   生命周期，不在模块导入阶段启动副作用。

## 第三方插件（外部包）

完整方案见 issue #200；示例与模板见 `examples/tenko-plugin-example/`。

- 第三方插件是标准 Python 包，在 `pyproject.toml` 声明 `tenko.plugins` 入口：
  `[project.entry-points."tenko.plugins"]` 下写 `<id> = "<module>"`。`<id>` 匹配
  `[a-z][a-z0-9_]*`，不能与内置插件重名；值必须是纯模块路径，不能带 `:属性`。
  非法、重名的入口会被跳过并记录错误。
- **不要声明 `entari.plugin` 入口**：Entari 会无视 Tenko 的启用状态自动加载它。
- 第三方插件默认关闭。在 WebUI 插件页启用后写入 `.tenko/plugins/state.json`
  （`{"enabled": ["<id>"]}`），首次加载需要重启 Tenko；状态文件缺失或损坏时视为全部未启用。
  `/状态`、总览只统计已加载的插件；`/帮助` 把已加载的第三方插件单列为“第三方”区，
  编号与 `/开启`、`/关闭` 一致。
- 第三方插件的命令与内置插件走同一套维护模式、功能开关和限流，功能开关以插件 ID 为键。
  这套策略（`CommandPolicy`）挂在 Entari 的 `CommandParse` 事件上：Entari 去掉 @bot、前导
  空白、前缀和昵称并匹配出命令后，按 `get_plugin_commands` 把命令映射到所属插件，返回
  `False` 即阻断；不要再按消息原文判断命令归属。限流（冷却或临时拉黑）静默，只记调试日志；
  `GroupSetting.frequency_limitation` 为假的群不限流（`/频率限制` 修改）。
  群等级低于插件要求（`declare(level=...)`，默认 `ActiveGroup`）时同样静默忽略，对内置和第三方
  插件一视同仁；检查在维护、开关和限流之前。不经过命令的监听器（例如响应 @bot 的消息）用
  `tenko_api.features.feature_required` 装饰，检查所属插件的开关和维护模式。
  Entari 里已注册命令、但未登记为内置或第三方的插件，其群聊命令会被拒绝执行。
  第三方插件不能自封“受保护”：`classifier` 里的 `host`/`required` 对它无效。
- **插件只依赖 `tenko-api`**（`packages/tenko-api/`，uv workspace 成员，发布到 PyPI 为
  `tenko-api`，导入名 `tenko_api`），不要导入 `tenko.*` 内部模块。它没有运行时依赖，
  只是转发到宿主；新增接口放在 `tenko_api` 的对应子模块，并写进 `tests/tenko/test_tenko_api.py`
  的接口清单。宿主启动时（`tenko/__init__.py`）把 `packages/tenko-api/src` 放到
  `sys.path` 最前，保证门面与当前宿主代码同版本；`tenko/` 内部不得依赖已安装的
  `tenko-api` 发行版。版本号在 `tenko_api/__init__.py` 的 `__version__`，1.0 之前接口可
  调整；发布推送标签 `tenko-api-v<版本>`（`.github/workflows/publish-api.yml`，Trusted Publishing）。
- 元数据：插件模块导入期间调用 `tenko_api.features.declare(scope, default_enabled,
  rate_weight, level)`，宿主据此设置 `feature_scope`、`default_switch`、限流权重
  （`CommandPolicy` 传给限流器）和 `group_level`（群等级要求），`default_enabled=False`
  表示没有显式开启的群默认关闭。
- 配置：`config/tenko.toml` 的 `[plugin_config.<id>]` 在加载该插件时传给
  `load_plugin(config=...)`（`PluginRuntime.load_all(external_configs=...)`）。不要用
  Entari 的 `[plugins.*]`，它会绕过启用状态自动加载。Entari 对标准 dataclass 配置模型
  不做类型校验。
- 存储：`tenko_api.storage.data_dir()` 返回 `.tenko/plugin-data/<id>/`；`Store(name,
  version, migrate)` 是带版本号的 JSON 文件（原子写入，版本变化先备份再迁移，遇到更新
  版本的文件拒绝读写）。调用方按模块名归属到 `tenko.plugins` 入口，只能在插件自己的
  模块里调用（实现见 `tenko/host/plugin_storage.py`）。插件独立 SQLite 库尚未提供。
- 插件不得删除表、删除列或修改列类型：宿主在官方迁移器上安装了“Schema 只前进”
  保护，遇到这类变更会回滚该模块的结构迁移并拒绝删表（实现见 `tenko/db/migration.py`）。
- 开发第三方插件时可用 `pip install "tenko @ git+https://github.com/g1331/tenko@<tag>"`
  安装宿主本体（`pyproject.toml` 提供 `[build-system]`；生产环境仍用 `uv sync`，
  不安装本项目）。
- 示例插件的测试由 `tests/tenko/test_example_plugin.py` 在子进程里对当前检出运行
  （用临时 dist-info 提供入口），随普通 `pytest` 一起执行。

## 代码与提交规范

- Ruff 使用 88 列、双引号，并启用 E、F、W、UP 规则集；遵循现有命名、异常处理和
  类型标注风格。
- 先理解调用链和已有测试，再做局部修改；不要为了顺手清理而扩大变更范围。
- 提交消息使用 Conventional Commits，例如
  `fix(commands): correct command boundary handling`。
- 功能、修复、文档和清理尽量分成可独立验证的小提交。每个提交前运行 Ruff 和
  pytest，并在交接时说明已验证内容与剩余风险。

## 批次派发纪律（执行 agent 任务书固定要求）

- **文档优先**：涉及 Entari/Satori/Alconna 等 API 时，先读官方文档章节，写出"文档对照结论"（用到哪些机制、与源码/现状的差异点），再动手；不得凭印象实现。
- **开工前设计决策清单**：中等以上批次必须在实现前提交关键设计决策（每项：选择、理由、被否备选、与现状关系），经审查后再实现。
- **交付决策记录**：交付时必须输出实现过程中与预想不一致处、临时新做的决定、发现但未解决的问题；验收以 diff 对照此记录审查，无记录支撑的偏离退回。
- **端到端验证**：涉及生命周期/重启/升级的功能，单测通过不等于完成，必须在简报指定的真实路径上跑一遍完整流程。
- **数据流核验**：统计/计数类功能须核对埋点位置与统计口径（过滤前后、双路径去重），测试断言不能作为行为正确的唯一依据。
- **Schema 只前进**：升级相关批次禁止直接 `DROP` 或改型既有列；需要废弃的列先保留并标记 `deprecated`，至少跨两个版本后才能清理，禁止通过降级 schema 破坏已有数据。
- **禁止无用途包装层**：每次抽象需说明服务的具体边界；优先复用 Entari/Satori/Launart 原生能力。
- **注释统一简体中文**，技术名词、标识符保留原文。

## 配置与安全

使用 `config/tenko.toml.example` 创建本地配置。真实 token、平台用户标识、Git 凭据和
其他敏感信息不得写入版本库；测试和 dry-run 应使用占位值或隔离的临时路径。
