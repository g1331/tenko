# tenko-plugin-example

[Tenko](https://github.com/g1331/tenko) 第三方插件的示例与起步模板。它演示：

- 用 `tenko_api.features.declare` 声明按群开关、默认状态、限流权重和群等级；
- 用 `[plugin_config.example]` 配置插件（Entari 的 `BasicConfModel`）；
- 用 `tenko_api.storage.Store` 保存带版本号的 JSON 数据；
- 用 `tenko_api.perm` 的装饰器（`@require` 等）声明命令权限，用 Entari 原生的 `command.on` 注册命令。

插件命令 `/示例 [备注]` 记录你使用命令的次数和最近一条备注。

## 从模板开始

1. 复制本目录作为新项目（在 GitHub 上可以把它放进自己的仓库）。
2. 改名：分发名用 `tenko-plugin-xxx`，模块名用 `tenko_plugin_xxx`。同步修改
   `pyproject.toml` 的 `name`、`[tool.hatch.build.targets.wheel]`、`src/` 目录名，以及
   入口 `[project.entry-points."tenko.plugins"]` 里的 `<插件 ID> = "<模块>"`。
   插件 ID 匹配 `[a-z][a-z0-9_]*`，全局唯一，不能和内置插件同名；它是功能开关、
   配置节和数据目录的键。
3. **不要声明 `entari.plugin` 入口**：Entari 会无视 Tenko 的启用状态自动加载它。
4. 依赖只写 `tenko-api` 的兼容范围（预览期为 `>=0.1,<0.2`）和插件自己的依赖，
   不要依赖 `tenko` 本体，也不要用 `==` 锁死依赖版本。

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
- 命令权限用 `tenko_api.perm` 的装饰器声明，不要在函数体里重复检查；装饰器在
  `@command.on(...)` 之上或之下都生效。因为装饰器由 Entari 调度，测试里直接调用
  `callable_target` 不会经过它们，应检查登记的要求（见 `tests/test_plugin.py`），
  或像 `tests/tenko` 里那样走真实分发。
- Entari 对标准 dataclass 配置模型不做类型校验，需要时在 `__post_init__` 里检查。

## 在 Tenko 里启用

1. 在运行 Tenko 的环境里安装插件：`uv pip install tenko-plugin-xxx`（本地调试用
   `uv pip install -e /path/to/plugin`）。
2. 打开 WebUI 的插件页，启用该插件（带“第三方”标记）。第三方插件默认关闭，启用后
   写入 `.tenko/plugins/state.json`。
3. 如有配置，在 `config/tenko.toml` 里添加 `[plugin_config.<插件 ID>]`，见仓库中
   `config/tenko.toml.example` 末尾的说明。
4. 重启 Tenko（聊天里的 `/重启` 或重新运行进程），插件才会被加载。

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

## 兼容性

`tenko-api` 遵循语义化版本（1.0 之前为预览，接口可能调整）。插件声明的范围由 pip / uv
的解析器检查：宿主提供的 `tenko-api` 不在范围内时，安装会直接被拒绝。
