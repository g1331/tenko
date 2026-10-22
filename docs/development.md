# 开发指南

## 获取与安装

    git clone https://github.com/g1331/tenko.git
    cd tenko
    uv sync

依赖以 pyproject.toml 声明、uv.lock 锁定；依赖变更使用 uv add / uv remove，
不要直接编辑 pyproject.toml 的依赖表。

## 代码约定

- 插件位于 tenko/plugins/，遵循 Entari 插件生命周期，命令统一 / 前缀；
  第三方插件通过 `tenko.plugins` 入口提供，默认关闭，在 WebUI 插件页启用后加载
  （个别需要重启）（规则见 AGENTS.md「第三方插件」）。写第三方插件请从
  [examples/tenko-plugin-example](../examples/tenko-plugin-example) 开始，它只依赖
  [tenko-api](../packages/tenko-api)；
- 数据库访问集中在 tenko/db/，宿主服务在 tenko/host/；
- 提交遵循 Conventional Commits，功能、修复、文档分开提交；
- 开发细节见 AGENTS.md。

## 运行测试

在项目根目录、并使用已安装依赖的环境执行：

    ./.venv-entari/bin/ruff check tenko tests/tenko
    ./.venv-entari/bin/python -m pytest tests/tenko

使用 uv 环境时可以直接执行：

    uv run ruff check tenko tests/tenko
    uv run pytest -q

如果使用已激活的虚拟环境，也可以执行：

    ruff check tenko tests/tenko
    python -m pytest tests/tenko

## 架构

![Tenko architecture](assets/architecture.png)

<details>
<summary>文本版架构图</summary>

```text
OneBot 11 endpoint (e.g. NapCat)
  │
  ▼
Satori adapter ──► Entari runtime ──► command_manager ──► Tenko plugins
                                      │
                                      ├── account / permission / feature services
                                      ├── SQLite database + repositories
                                      └── RenderService
```

</details>

兼容 OneBot 11 的协议端（以下以 NapCat 为例）通过反向 WebSocket 连接 Tenko，
Satori 负责协议对象和动作抽象，Entari 负责事件分发、插件生命周期和命令处理：

- 插件在 Entari 生命周期中加载和卸载；插件通过原生命令注册表接收命令，
  通过宿主服务访问账号路由、权限、功能开关、限流和升级控制平面。
- command_manager 是帮助系统读取命令列表的来源，因此帮助内容会随当前已注册
  插件变化。
- RenderService 由 Tenko runtime 直接注册到 Launart，使用 Playwright 在本地离线
  渲染 HTML/Markdown；服务异常不会
  阻断文本功能。
- 数据层使用 SQLite、SQLAlchemy 和 entari-plugin-database。新 ORM 位于
  tenko/db/models.py，repository 位于 tenko/db/repositories.py；应用启动时会创建
  或迁移所需表，已有 SQLite 数据库文件可以继续使用。