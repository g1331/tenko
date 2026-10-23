"""Tenko 扩展插件示例：问候计数。

演示 tenko_api 的主要能力：声明元数据（按群开关、限流权重）、读取配置、带版本的 JSON
存储、插件自己的 SQLite 库（``Model``），以及声明式权限装饰器。写法与内置插件一样，
都是 Entari 原生的 ``plugin.metadata``、``command.on``。
"""

from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import BasicConfModel, Session, command, plugin
from arclet.entari.command import Query

from tenko_api.features import declare
from tenko_api.perm import Permission, require
from tenko_api.storage import Model, Store, get_session


class ExampleConfig(BasicConfModel):
    """对应 config/tenko.toml 中的 ``[plugin_config.example]``。"""

    greeting: str = "你好"
    """回复使用的问候语。"""


plugin.metadata(
    "示例插件",
    author=["13"],
    version="0.1.0",
    description="记录每位用户使用示例命令的次数，并保存备注。",
    config=ExampleConfig,
    # 可选：插件目录会下载这张图，处理成 128×128 PNG 后自行托管。
    # icon="https://raw.githubusercontent.com/you/tenko-plugin-example/main/icon.png",
)
# Tenko 专有元数据：按群开关、新群默认开启、每次调用计入限流窗口的权重。
# 消耗付费额度的插件可再加 controlled=True：默认关闭，只有 BOT 管理员能在群里打开。
declare(scope="group", default_enabled=True, rate_weight=1)

config = plugin.get_config(ExampleConfig)
# .tenko/plugin-data/example/visits.json；以后改数据结构时提高 version，并提供 migrate。
visits = Store("visits", version=1)


class Note(Model):
    """备注历史。表建在 .tenko/plugin-data/example/data.db，表名默认为 example_note。

    宿主启动时自动建表，并自动执行新增表、新增可空列、新增索引；删除或修改列需要
    显式迁移脚本，否则会被拒绝。
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(String(200))


example_command = Alconna(
    "示例",
    Args["note?", str],
    meta=CommandMeta(
        "统计你使用示例命令的次数，可附带一条备注",
        usage="示例 [备注]",
        example="/示例 今天也要加油",
    ),
)


# 权限用装饰器声明（放在 command.on 之下）：不足时宿主统一回复，/帮助 <编号> 也会显示。
@command.on(example_command)
@require(Permission.User)
async def example(session: Session, note: Query[str] = Query("note", "")):
    user_id = session.user.id
    # Store 是同步读写：读改写之间没有 await，协程不会交错，所以不需要加锁。
    records = visits.load(default={})
    record = records.setdefault(user_id, {"count": 0})
    record["count"] += 1
    visits.save(records)

    # 会话按模型自动路由到插件自己的库；事务由 commit 显式提交。
    async with get_session() as db:
        previous = await db.scalar(
            select(Note.text)
            .where(Note.user_id == user_id)
            .order_by(Note.id.desc())
            .limit(1)
        )
        if note.result:
            db.add(Note(user_id=user_id, text=note.result))
            await db.commit()

    reply = f"{config.greeting}，这是你第 {record['count']} 次使用示例命令"
    return f"{reply}（上次备注：{previous}）" if previous else reply
