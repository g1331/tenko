"""Tenko 第三方插件示例：问候计数。

演示 tenko_api 的主要能力：声明元数据、读取配置、带版本的 JSON 存储，以及
权限检查。写法与内置插件一样，都是 Entari 原生的
``plugin.metadata``、``command.on``。
"""

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import BasicConfModel, Session, command, plugin
from arclet.entari.command import Query

from tenko_api.context import context_from_session
from tenko_api.features import declare
from tenko_api.messages import text_message
from tenko_api.perm import Permission, require_perm
from tenko_api.storage import Store


class ExampleConfig(BasicConfModel):
    """对应 config/tenko.toml 中的 ``[plugin_config.example]``。"""

    greeting: str = "你好"
    """回复使用的问候语。"""


plugin.metadata(
    "示例插件",
    author=["you"],
    version="0.1.0",
    description="记录每位用户使用示例命令的次数，并保存最近一条备注。",
    config=ExampleConfig,
)
# Tenko 专有元数据：按群开关、新群默认开启、每次调用计入限流窗口的权重。
declare(scope="group", default_enabled=True, rate_weight=1)

config = plugin.get_config(ExampleConfig)
# .tenko/plugin-data/example/visits.json；以后改数据结构时提高 version，并提供 migrate。
visits = Store("visits", version=1)

example_command = Alconna(
    "示例",
    Args["note?", str],
    meta=CommandMeta(
        "统计你使用示例命令的次数，可附带一条备注",
        usage="示例 [备注]",
        example="/示例 今天也要加油",
    ),
)


@command.on(example_command)
async def example(session: Session, note: Query[str] = Query("note", "")):
    ctx = context_from_session(session)
    if not await require_perm(ctx, Permission.User):
        return text_message("权限不足")

    # Store 是同步读写：读改写之间没有 await，协程不会交错，所以不需要加锁。
    records = visits.load(default={})
    record = records.setdefault(ctx.user_id, {"count": 0, "note": ""})
    previous = record["note"]
    record["count"] += 1
    if note.result:
        record["note"] = note.result
    visits.save(records)

    reply = f"{config.greeting}，这是你第 {record['count']} 次使用示例命令"
    return text_message(f"{reply}（上次备注：{previous}）" if previous else reply)
