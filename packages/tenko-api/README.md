# tenko-api

[Tenko](https://github.com/g1331/tenko) 扩展插件使用的稳定宿主接口。

插件依赖它，而不是依赖 `tenko` 本体：`tenko-api` 没有任何运行时依赖，
只是转发到正在运行的 Tenko 宿主，因此不会影响插件自己的依赖解析。插件声明
`tenko-api>=0.4,<0.5` 这样的范围；宿主加载插件前按自己提供的 `tenko-api` 版本检查
这个范围，不兼容的插件不加载并在日志中说明原因，插件不需要另写兼容检查。

> 当前为预览版（0.x）。1.0 之前接口可能调整，请把上限写到次版本。

## 最小示例

插件是标准 Python 包，写法与 Entari 原生插件相同，宿主能力一律从
`tenko_api` 导入：

```toml
# pyproject.toml
[project]
name = "tenko-plugin-hello"
version = "0.1.0"
dependencies = ["tenko-api>=0.4,<0.5"]

[project.entry-points."tenko.plugins"]
hello = "tenko_plugin_hello"   # 入口名 = 插件 ID
```

```python
# src/tenko_plugin_hello/__init__.py
from arclet.alconna import Alconna
from arclet.entari import Session, command, plugin

from tenko_api.features import declare
from tenko_api.perm import Permission, require

plugin.metadata("你好", author=["you"], version="0.1.0", description="打招呼")
declare(scope="group", default_enabled=True, rate_weight=1)


@command.on(Alconna("你好"))
@require(Permission.User)
async def hello(session: Session):
    return "你好！"
```

## 返回消息

命令处理函数直接返回 `str`，Entari 原样发送；图片等混合内容返回 `MessageChain`。
宿主强制关闭 Entari 的 `str_as_message`，返回的 `str` 一律按纯文本发送，需要 @、图片等元素时
返回 `MessageChain`。

## 权限与功能开关

限制命令的默认写法是装饰器，不要在函数体里重复写检查（漏写就等于没有权限控制）：

| 装饰器 | 作用 |
|---|---|
| `@require(Permission.X)` | 发送者的成员权限不低于 X；不足时回复“权限不足（当前权限：… / 需要权限：…）” |
| `@group_only()` / `@private_only()` | 只允许群聊 / 私聊，否则回复“该指令只能在群聊/私聊中使用” |

- 装饰器放在 `@command.on(...)` 之下，放在之上也同样生效；同一种装饰器在一个函数上只能用一次。
  它们在命令匹配之后执行，所以只有命中命令的消息才会收到拒绝回复。
- 声明的要求登记在处理函数上，`/帮助 <编号>` 会显示每条命令的“权限”。
- 只有权限取决于运行时数据时（例如按参数区分的权限）才在函数体里调用 `require_perm(session, level)`。
- 权限从低到高是 `User`（成员）、`GroupAdmin`（群管理）、`GroupOwner`（群主）、`BotAdmin`、
  `Master`，只用名字比较，不要写数值。群主、群管理取消息里的实时群身份，另可手动授予本群群管理
  或全局 BotAdmin。
- 黑名单里的人和群的消息在进入插件之前就被丢弃（包括不走命令的监听器），插件不需要判断。
- `declare(controlled=True)` 把插件标为受控插件（例如消耗 token 的 AI 插件）：所有群默认关闭，
  只有 BotAdmin 及以上能在群里打开；私聊里只有 BotAdmin 及以上能用。
- 不走命令的监听器（例如响应 @bot 的消息监听）没有宿主的命令策略保护，用
  `@feature_required()` 检查所属插件的功能开关和维护模式（只在群聊生效，默认静默，
  `notify=True` 时回复提示）。所属插件按被装饰函数的模块确定，规则与 `data_dir()` 相同。

```python
from arclet.entari import MessageCreatedEvent, plugin

from tenko_api.features import feature_required


@plugin.listen(MessageCreatedEvent)
@feature_required()
async def on_message(event: MessageCreatedEvent): ...
```

## 接口一览

| 模块 | 内容 |
|---|---|
| `tenko_api.perm` | `Permission`；装饰器 `require`、`group_only`、`private_only`；函数体内的 `require_perm`、`get_user_perm` |
| `tenko_api.actions` | 发消息、禁言、踢人、撤回、查询群成员等宿主动作，以及 `ActionServiceError` 系列异常 |
| `tenko_api.accounts` | 只读账号信息：在线账号、某群可响应的账号（群在黑名单时为空；非 QQ 群传 `platform=`） |
| `tenko_api.features` | `feature_enabled` 只读查询（非 QQ 群传 `platform=`）；`declare` 声明范围、默认开关、限流权重和是否受控；`feature_required` 装饰非命令监听器 |
| `tenko_api.render` | `RenderService`、`try_render`（浏览器不可用时得到 `None`，回退文本；`render_or_none` 是按方法名分发的旧写法） |
| `tenko_api.messages` | `report_error` |
| `tenko_api.storage` | 插件独立数据目录 `data_dir()`、带版本号并自动备份迁移的 JSON `Store`、插件自己的 SQLite 库 `Model` 与 `get_session()` |
| `tenko_api.sandbox` | `in_sandbox()`：当前处理链是否来自面板的命令沙盒（0.4.1 起） |

## 命令沙盒

面板的命令沙盒会真实执行插件逻辑，宿主在出口处隔离副作用：

- 真实账号的发送、撤回、禁言、踢人、审批等写操作不会发出，沙盒聊天里记一条“[沙盒拦截]”说明；
  发消息得到一个假回执。只读查询照常执行。
- 宿主的全局设置（权限、黑名单、功能开关、通知设置、插件管理、升级等）不会被改；
  只作用于沙盒群和沙盒身份的修改照常生效。
- `tenko_api.storage` 的 `data_dir()`、`Store`、`Model` 读写的是数据副本
  `.tenko/sandbox-data/<id>/`：第一次在沙盒里访问时从真实数据复制，面板上的“重置数据”删除副本。

宿主管不到的地方需要插件自己处理：

- 调用外部服务（例如大模型 API）、扣费、写 `data_dir()` 以外的文件，用 `tenko_api.sandbox.in_sandbox()` 判断。
- `data_dir()` 要在处理函数里调用；在模块顶层取一次并保存下来的路径永远指向真实目录。
- 模块里的内存状态（全局变量、缓存）不会隔离，需要持久的状态放进存储。
- 沙盒标记只随这条消息派生的任务传递；交给事先已经在运行的后台任务（例如自己的发送队列）的工作不带标记，
  会被当成真实调用。

## 插件的 SQLite 库

需要 SQL 的插件继承 `tenko_api.storage.Model`（SQLAlchemy 2.0 声明式），表建在
`.tenko/plugin-data/<插件 ID>/data.db`，不会进入宿主数据库：

```python
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from tenko_api.storage import Model, get_session


class Note(Model):  # 表名默认为 <插件ID>_note
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(200))


async def latest() -> str | None:
    async with get_session() as db:  # 操作 Model 子类时自动使用插件自己的库
        return await db.scalar(select(Note.text).order_by(Note.id.desc()).limit(1))
```

- 模型要在插件模块里定义（导入期间），并在 Tenko 启动时随插件加载；已经在运行的 Tenko 里不能新增模型，
  所以用了模型的插件首次启用后需要重启，没有模型的插件启用即可加载。
- 启动时自动建表，并自动执行新增表、新增可空列和新增索引。删除表、删除列、修改列类型会被拒绝
  （日志报错、数据保持原样）；确需这类变更时用官方的 `register_custom_migration` 写显式迁移脚本。
- 插件版本变化后的第一次启动，宿主先备份插件库（`backups/`，保留最近 3 份）再迁移。
- 插件之间、插件与宿主之间不能跨库查询；原生 `execute(text(...))` 走宿主主库，不要使用。
- 表名默认带插件 ID 前缀；显式写 `__tablename__` 时要自己保证不与其他插件冲突。
- 直接继承 `BaseOrm` 等会绑定到宿主主库的基类，插件会在加载时报错。
- 在 `pyproject.toml` 里声明 `sqlalchemy>=2.0` 依赖（使用宿主已安装的版本）。

完整的开发、测试和发布流程见仓库中的示例插件
[`examples/tenko-plugin-example`](https://github.com/g1331/tenko/tree/main/examples/tenko-plugin-example)。

## 版本承诺

版本号独立于 Tenko 本体，遵循语义化版本（1.0 起生效）。1.0 之前删除或改变语义时次版本号 +1，
例如 0.3.0 移除了 `tenko_api.context` 和 `text_message`，权限函数改为接收 `Session`；
0.4.0 重做权限（#234）：`Permission` 改为 0–4 的五级、删除群等级（`require_group`、
`get_group_perm`、`declare(level=...)`），新增 `declare(controlled=...)`；`feature_enabled`、
`accounts_for_group` 新增仅限关键字的 `platform` 参数（默认 `"qq-client"`），用于区分不同平台的同号群。

| 变更 | 版本号 |
|---|---|
| 新增能力 | 次版本号 +1 |
| 删除或改变语义 | 主版本号 +1 |

被废弃的接口至少保留一个次版本，并抛出 `DeprecationWarning`。

## 运行环境

需要在 Tenko 宿主进程中使用：开发和测试时把 `tenko` 作为开发依赖安装即可。
Python `>=3.11,<3.14`。
