"""经真实 Entari 消息分发验证命令策略的测试支撑。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from itertools import count

import arclet.letoderea as le
from arclet.entari.config import EntariConfig
from arclet.entari.event.command import CommandParse
from arclet.entari.plugin import plugin_service
from satori import Channel, ChannelType, EventType, Guild, Login, MessageObject, User
from satori.model import Event

from tenko.commands import configure_command_prefix

# Entari 按消息 ID 缓存命令解析结果：同 ID 的重复消息会复用上一次结果，所以必须唯一。
_MESSAGE_IDS = count(1)

BOT_ID = "10001"
NICKNAME = "天天"


class FakeProtocol:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, channel, message, *args, **kwargs):
        self.sent.append(str(message))
        return [MessageObject(f"sent-{len(self.sent)}", str(message))]


class FakeAccount:
    self_id = BOT_ID
    platform = "onebot"

    def __init__(self) -> None:
        self.protocol = FakeProtocol()

    @property
    def sent(self) -> list[str]:
        return self.protocol.sent


def message_event(
    content: str, *, group: str | None = "40001", user: str = "20001"
) -> Event:
    """构造群聊（``group=None`` 为私聊）消息事件，``content`` 是 Satori XML。"""

    direct = group is None
    return Event(
        type=EventType.MESSAGE_CREATED,
        timestamp=datetime.now(),
        login=Login(platform="onebot", user=User(BOT_ID)),
        channel=Channel(
            f"private:{user}" if direct else group,
            ChannelType.DIRECT if direct else ChannelType.TEXT,
        ),
        guild=None if direct else Guild(group, "Tenko"),
        user=User(user),
        message=MessageObject(f"msg-{next(_MESSAGE_IDS)}", content),
    )


async def dispatch(app, account: FakeAccount, event: Event) -> None:
    """走 ``Entari.handle_event``，并等待本次事件派生的全部处理任务结束。"""

    before = asyncio.all_tasks()
    await app.handle_event(account, event)
    current = asyncio.current_task()
    pending = asyncio.all_tasks() - before - {current}
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@contextmanager
def entari_command_dispatch(policy=None) -> Iterator[None]:
    """启用 Entari 的命令分发和前缀配置，可选挂载 Tenko 命令策略。

    生产里 ``$.commands`` 子插件在 Entari 启动后才应用；测试里的 Entari 不启动，
    需要手动应用一次。前缀和昵称与生产一致：命令不带前缀，由 Entari 去除。
    """

    commands = plugin_service.plugins["$.commands"]
    if commands._apply:
        commands.exec_apply()
        commands._apply = None
    config = EntariConfig.instance.basic
    old = (config.prefix, config.nickname)
    configure_command_prefix(())
    config.prefix = ["/"]
    config.nickname = NICKNAME
    subscriber = (
        None if policy is None else le.on(CommandParse, policy.enforce, priority=0)
    )
    try:
        yield
    finally:
        if subscriber is not None:
            subscriber.dispose()
        config.prefix, config.nickname = old
        configure_command_prefix()


# Entari 识别命令前会去掉这些外壳；策略必须对它们一视同仁。
COMMAND_FORMS = {
    "plain": "/{c}",
    "leading_space": " /{c}",
    "at_bot": f'<at id="{BOT_ID}"/> /{{c}}',
    "at_bot_no_space": f'<at id="{BOT_ID}"/>/{{c}}',
    "nickname": f"{NICKNAME} {{c}}",
    "quote_then_command": '<quote id="1"><author id="20002"/>旧消息</quote>/{c}',
}
