# tenko-plugin-example

[Tenko](https://github.com/g1331/tenko) 扩展插件的示例与起步模板。它演示：

- 用 `tenko_api.features.declare` 声明按群开关、默认状态和限流权重；
- 用 `[plugin_config.example]` 配置插件（Entari 的 `BasicConfModel`）；
- 用 `tenko_api.storage.Store` 保存带版本号的 JSON 数据，用 `Model` 和 `get_session()` 使用插件自己的 SQLite 库；
- 用 `tenko_api.perm` 的装饰器（`@require` 等）声明命令权限，用 Entari 原生的 `command.on` 注册命令。

插件命令 `/示例 [备注]` 记录你使用命令的次数（JSON）和备注历史（SQLite）。

## 从模板开始

1. 在 [tenko-plugin-template](https://github.com/g1331/tenko-plugin-template) 点
   “Use this template” 建自己的仓库并 clone 下来（也可以直接复制本目录）。
2. 改名，一条命令改完分发名、模块名、`src/` 目录、入口、配置节、数据目录、仓库地址和作者：

   ```text
   python3 scripts/rename.py weather --name 天气 --author 你的名字
   ```

   插件 ID（这里的 `weather`）匹配 `[a-z][a-z0-9_]*`，全局唯一，不能和内置插件同名；它是
   功能开关、配置节和数据目录的键。仓库地址默认取 `git remote origin`，没有时用
   `--repo 你/仓库` 指定。脚本会重写 README 并删除自己。
3. `uv sync && uv run pytest -q` 确认通过，然后把示例命令改成你自己的功能，补上
   `plugin.metadata` 的 `description`。

另外注意：

- **不要声明 `entari.plugin` 入口**：Entari 会无视 Tenko 的启用状态自动加载它。
- 依赖只写 `tenko-api` 的兼容范围（预览期为 `>=0.4,<0.5`，用到 `@rate_limit` 时下限写
  `0.4.2`）和插件自己的依赖，不要依赖 `tenko` 本体，也不要用 `==` 锁死依赖版本。
- 开发依赖里的 Tenko 宿主默认跟随 `main`，发布前固定到一个版本标签。

## 开发与测试

```text
uv sync          # 创建环境，开发依赖里包含从 Git 安装的 Tenko 宿主（发布后请固定到版本标签）
uv run pytest -q
uv run ruff check .
```

`tests/conftest.py` 用真实的宿主加载插件：按 `tenko.plugins` 入口发现、启用、传入配置，
然后直接调用命令处理函数。不需要启动 Tenko，也不需要连接聊天平台。

写法要点：

- 宿主能力一律从 `tenko_api` 导入，不要导入 `tenko.*` 内部模块。
- `declare(...)` 必须在插件模块导入期间调用（与 `plugin.metadata(...)` 放在一起）。
- `data_dir()` 和 `Store` 只能在插件自己的模块里使用，数据位于 `.tenko/plugin-data/<插件 ID>/`。
  数据结构变化时提高 `Store` 的版本号并提供 `migrate`，宿主会先把旧文件备份为
  `<名称>.json.bak-v<旧版本>`；插件被降级、读到更新版本的文件时会报错而不是覆盖。
- `Model` 子类在插件模块导入期间定义，表建在 `data.db`；启动时自动建表和增加可空列，
  删除或修改列会被拒绝，需要时写官方的 `register_custom_migration` 脚本。测试里要像宿主
  那样先加载数据库插件、再加载插件、最后初始化并迁移（见 `tests/conftest.py`）。
  版本变化后宿主会先备份库到 `backups/`。
- 命令权限用 `tenko_api.perm` 的装饰器声明，不要在函数体里重复检查；装饰器在
  `@command.on(...)` 之上或之下都生效。因为装饰器由 Entari 调度，测试里直接调用
  `callable_target` 不会经过它们，应检查登记的要求（见 `tests/test_plugin.py`），
  或像 `tests/tenko` 里那样走真实分发。
- Entari 对标准 dataclass 配置模型不做类型校验，需要时在 `__post_init__` 里检查。

## 在 Tenko 里启用

1. 在运行 Tenko 的环境里安装插件：`uv pip install tenko-plugin-xxx`（本地调试用
   `uv pip install -e /path/to/plugin`）。
2. 如有配置，在 `config/tenko.toml` 里添加 `[plugin_config.<插件 ID>]`，见仓库中
   `config/tenko.toml.example` 末尾的说明。运行中的 Tenko 只认启动时读到的配置，改了配置
   要重启。
3. 打开 WebUI 的插件页，启用该插件（带“扩展”标记）。扩展插件默认关闭，启用后写入
   `.tenko/plugins/state.json` 并直接加载，数据库表随即建好。

插件可能在 Tenko 运行中被加载，这时启动、就绪等生命周期事件已经过去，初始化不要只放在
这些事件里。

之后可以在 WebUI 开关页或用 `/开启`、`/关闭` 按群控制，插件命令与内置命令走同一套
维护模式、功能开关和限流。

## 发布

**发布到 PyPI（推荐，使用 Trusted Publishing，不保存 token）**

1. 在 PyPI 上添加 pending publisher：项目名填你的分发名，所有者和仓库填你的 GitHub
   仓库，工作流填 `publish.yml`，环境填 `pypi`。
2. 在 GitHub 仓库设置里创建名为 `pypi` 的 Environment。
3. 把 `pyproject.toml` 的版本改为 `X.Y.Z`，提交后打标签并推送：

   ```text
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

   `.github/workflows/publish.yml` 会检查标签与版本一致、构建并发布。

**只用 GitHub**：不发布到 PyPI 时，使用者可以直接
`uv pip install "tenko-plugin-xxx @ git+https://github.com/<你>/<仓库>@<标签或提交>"`，
请固定到标签或提交哈希。

## 收录到插件目录

发布到 PyPI（或打好 Git 标签）后，在 [tenko-registry](https://github.com/g1331/tenko-registry)
用投稿表单填写来源即可。插件 ID、名称、简介、作者、许可证和兼容范围都由目录的 CI 从
`pyproject.toml` 和 `plugin.metadata(...)` 读取，之后的新版本也会自动追加。

图标可选：在 `plugin.metadata(icon="https://…")` 里给出图片地址，或在投稿表单里上传。要求
透明背景的单色图形（PNG 或 WebP，不接受 JPEG、SVG 和动图），短边至少 64 px；目录会裁成
正方形、缩放到 128×128 PNG 并自行托管，Tenko 只显示目录托管的图标。

## 兼容性

`tenko-api` 遵循语义化版本（1.0 之前为预览，接口可能调整）。插件声明的范围由宿主在
加载前检查：宿主提供的 `tenko-api` 不在范围内时，插件不加载，日志说明原因。注意 `uv pip install`
遇到不兼容的范围不会报错，而是把环境里的 `tenko-api` 换成插件要的版本，所以不能靠安装结果判断兼容性。
