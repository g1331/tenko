# tenko-api

[Tenko](https://github.com/g1331/tenko) 第三方插件使用的稳定宿主接口。

插件依赖它，而不是依赖 `tenko` 本体：`tenko-api` 没有任何运行时依赖，
只是转发到正在运行的 Tenko 宿主，因此不会影响插件自己的依赖解析。插件声明
`tenko-api>=0.1,<0.2` 这样的范围，安装时由 pip / uv 的解析器拒绝不兼容的宿主，
不需要另写兼容检查。

> 当前为预览版（0.x）。1.0 之前接口可能调整，请把上限写到次版本。

## 最小示例

插件是标准 Python 包，写法与 Entari 原生插件相同，宿主能力一律从
`tenko_api` 导入：

```toml
# pyproject.toml
[project]
name = "tenko-plugin-hello"
version = "0.1.0"
dependencies = ["tenko-api>=0.1,<0.2"]

[project.entry-points."tenko.plugins"]
hello = "tenko_plugin_hello"   # 入口名 = 插件 ID
```

```python
# src/tenko_plugin_hello/__init__.py
from arclet.alconna import Alconna
from arclet.entari import Session, command, plugin

from tenko_api.context import context_from_session
from tenko_api.features import declare
from tenko_api.messages import text_message
from tenko_api.perm import Permission, require_perm

plugin.metadata("你好", author=["you"], version="0.1.0", description="打招呼")
declare(scope="group", default_enabled=True, rate_weight=1)


@command.on(Alconna("你好"))
async def hello(session: Session):
    ctx = context_from_session(session)
    if not await require_perm(ctx, Permission.User):
        return text_message("权限不足")
    return text_message("你好！")
```

## 接口一览

| 模块 | 内容 |
|---|---|
| `tenko_api.context` | `MessageContext`、`context_from_session` |
| `tenko_api.perm` | `Permission`、`require_perm`、`get_user_perm`、`get_group_perm` |
| `tenko_api.actions` | 发消息、禁言、踢人、撤回、查询群成员等宿主动作，以及 `ActionServiceError` 系列异常 |
| `tenko_api.accounts` | 只读账号信息：在线账号、某群可响应的账号 |
| `tenko_api.features` | `feature_enabled` 只读查询；`declare` 声明范围、默认开关和限流权重 |
| `tenko_api.render` | `render_or_none`（浏览器不可用时回退文本） |
| `tenko_api.messages` | `text_message`、`report_error` |
| `tenko_api.storage` | 插件独立数据目录 `data_dir()`、带版本号并自动备份迁移的 JSON `Store` |

完整的开发、测试和发布流程见仓库中的示例插件
[`examples/tenko-plugin-example`](https://github.com/g1331/tenko/tree/main/examples/tenko-plugin-example)。

## 版本承诺

版本号独立于 Tenko 本体，遵循语义化版本（1.0 起生效）：

| 变更 | 版本号 |
|---|---|
| 新增能力 | 次版本号 +1 |
| 删除或改变语义 | 主版本号 +1 |

被废弃的接口至少保留一个次版本，并抛出 `DeprecationWarning`。

## 运行环境

需要在 Tenko 宿主进程中使用：开发和测试时把 `tenko` 作为开发依赖安装即可。
Python `>=3.11,<3.14`。
