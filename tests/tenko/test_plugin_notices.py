"""扩展插件通知 Master（tenko_api.notify）：来源、送达结果、回复识别、限速和沙盒。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from external_plugin_support import entry, patch_entry_points
from satori import (
    Channel,
    ChannelType,
    EventType,
    Guild,
    Login,
    MessageObject,
    User,
)
from satori.element import At, Author, Image, Quote, Text
from satori.model import Event

from tenko.host import notify, perm
from tenko.host.accounts import AccountRegistry
from tenko.host.actions import ActionCapability, ActionReceipt
from tenko.host.plugins import notices
from tenko.host.plugins.notices import MasterNotice, MasterReply, NoticeError
from tenko.host.plugins.storage import PLUGIN_DATA_ROOT
from tenko.host.sandbox import (
    SANDBOX_BOT_ID,
    SANDBOX_MASTER_ID,
    SANDBOX_PLATFORM,
    SANDBOX_PRIVATE_CHANNEL,
    SandboxAdapter,
    sandbox_origin,
)

MASTER = "90001"
PLUGIN_SOURCE = """
from tenko_api.notify import master_reply, notify_master

async def send(content, key=None):
    return await notify_master(content, key=key)

async def reply(session):
    return await master_reply(session)
"""


def plugin_module(name: str) -> dict:
    namespace = {"__name__": name}
    exec(compile(PLUGIN_SOURCE, name, "exec"), namespace)
    return namespace


class Actions:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list]] = []

    async def send_private_message(self, account, user_id, content):
        self.sent.append((user_id, content))
        return ActionReceipt(
            account.self_id,
            ActionCapability.SEND_PRIVATE_MESSAGE,
            "send_private_msg",
            data=[MessageObject(f"m{len(self.sent)}", "")],
        )

    async def send_group_message(self, account, group_id, content, **kwargs):
        self.sent.append((group_id, content))
        return ActionReceipt(
            account.self_id,
            ActionCapability.SEND_GROUP_MESSAGE,
            "send_group_msg",
            data=[MessageObject(f"m{len(self.sent)}", "")],
        )


@pytest.fixture
def host(monkeypatch, tmp_path):
    """``wife`` 插件拥有 ``tenko_t_wife``；一个在线账号，通知私聊 Master 90001。"""

    monkeypatch.chdir(tmp_path)
    patch_entry_points(
        monkeypatch,
        entry("wife", "tenko_t_wife"),
        entry("other", "tenko_t_other"),
    )
    account = SimpleNamespace(self_id="10001", platform="qq-client")
    registry = AccountRegistry()
    registry.register(account, available=True)
    actions = Actions()

    async def settings():
        return notify.NotifySettings()

    monkeypatch.setattr(notify, "account_registry", registry)
    monkeypatch.setattr(notices, "account_registry", registry)
    monkeypatch.setattr(notify, "action_service", actions)
    monkeypatch.setattr(notify, "load_settings", settings)
    monkeypatch.setattr(notify, "superuser_ids", lambda _platform: (MASTER,))
    monkeypatch.setattr(
        perm,
        "superuser_ids",
        lambda platform: (SANDBOX_MASTER_ID,) if platform == "sandbox" else (MASTER,),
    )
    monkeypatch.setattr(notices, "limiter", notices.NoticeLimiter())
    return SimpleNamespace(
        account=account,
        actions=actions,
        registry=registry,
        wife=plugin_module("tenko_t_wife"),
        other=plugin_module("tenko_t_other"),
    )


def reply_session(
    quote_id: str,
    text: str,
    *,
    user: str = MASTER,
    platform: str = "qq-client",
    account: str = "10001",
    group: str | None = None,
    quoted: list | None = None,
):
    """私聊（或 ``group``）里引用 ``quote_id`` 回复 ``text``，开头带一个 QQ 自动加上的 @。

    ``quoted`` 是协议端回传的被引用消息（作者和内容）。
    """

    event = Event(
        type=EventType.MESSAGE_CREATED,
        timestamp=datetime.now(),
        login=Login(platform=platform, user=User(account)),
        channel=Channel(group, ChannelType.TEXT)
        if group
        else Channel(f"private:{user}", ChannelType.DIRECT),
        guild=Guild(group) if group else None,
        user=User(user),
        message=MessageObject("r1", text),
    )
    return SimpleNamespace(
        quote=Quote(quote_id, content=quoted),
        account=SimpleNamespace(platform=platform, self_id=account),
        elements=[Quote(quote_id), At(account), Text(f" {text} ")],
        event=SimpleNamespace(_origin=event),
    )


@pytest.mark.asyncio
async def test_notice_names_its_plugin_and_master_reply_returns_its_key(host):
    image = Image(src="https://example.invalid/wife.png")
    notice = await host.wife["send"](["投稿待审核", image], key="submission:42")

    assert notice == MasterNotice(True, None, "submission:42")
    [(target, content)] = host.actions.sent
    assert target == MASTER
    assert content[0].text == "🔌 来自插件「wife」\n"
    assert content[1:] == [Text("投稿待审核"), image]

    reply = host.wife["reply"]
    assert await reply(reply_session("m1", "y")) == MasterReply(
        "submission:42", "y", MASTER
    )
    # 不是 Master、引用的不是这条通知、别的插件来问，都对不上。
    assert await reply(reply_session("m1", "y", user="20001")) is None
    assert await reply(reply_session("m9", "y")) is None
    assert await host.other["reply"](reply_session("m1", "y")) is None


@pytest.mark.asyncio
async def test_group_reply_received_by_another_own_account_matches_by_text(
    host, monkeypatch
):
    async def group_settings():
        return notify.NotifySettings("group", "40001")

    monkeypatch.setattr(notify, "load_settings", group_settings)
    host.registry.register(SimpleNamespace(self_id="10002", platform="qq-client"))
    await host.wife["send"]("投稿 #42 待审核", key="submission:42")

    # 10002 收到回复：被引用消息在它那里是另一个本地编号，作者是发出通知的 10001。
    text = Text("🔌 来自插件「wife」\n投稿 #42 待审核")
    for author, expected in (("10001", "submission:42"), ("20001", None)):
        # 作者不是自家账号（例如有人复制了通知原文）时不算。
        session = reply_session(
            "local-7",
            "y",
            account="10002",
            group="40001",
            quoted=[Author(author), text],
        )
        reply = await host.wife["reply"](session)
        assert (reply and reply.key) == expected


@pytest.mark.asyncio
async def test_undelivered_notices_say_why(host):
    send = host.wife["send"]
    for _ in range(notices.BURST):
        assert (await send("服务异常")).delivered
    assert await send("服务异常") == MasterNotice(False, "rate_limited")
    assert await send("服务异常") == MasterNotice(False, "rate_limited")
    warnings = [c for _, c in host.actions.sent if "过于频繁" in str(c)]
    assert len(warnings) == 1

    host.registry.set_available(host.account, False)
    assert await host.other["send"]("服务异常") == MasterNotice(False, "no_account")


@pytest.mark.asyncio
async def test_sandbox_notice_stays_in_the_sandbox_chat_and_can_be_answered(host):
    sandbox = SandboxAdapter()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(notices.sandbox, "sandbox", sandbox)
        with sandbox_origin(SANDBOX_PRIVATE_CHANNEL):
            notice = await host.wife["send"]("投稿待审核", key="submission:7")
            [message] = sandbox.messages(SANDBOX_PRIVATE_CHANNEL)
            reply = await host.wife["reply"](
                reply_session(
                    message["message_id"],
                    "图太糊",
                    user=SANDBOX_MASTER_ID,
                    platform=SANDBOX_PLATFORM,
                    account=SANDBOX_BOT_ID,
                )
            )

    assert notice.delivered
    assert host.actions.sent == []
    assert "本应发给 Master" in str(message["content"])
    assert "投稿待审核" in str(message["content"])
    assert reply == MasterReply("submission:7", "图太糊", SANDBOX_MASTER_ID)
    # 记录写在沙盒数据副本里，真实数据目录没有。
    assert not (PLUGIN_DATA_ROOT / "wife" / notices.BOOK_NAME).exists()


@pytest.mark.asyncio
async def test_bad_calls_fail_at_the_call_site(host):
    with pytest.raises(NoticeError, match="tenko.plugins 入口"):
        notices.notify_master("hi")
    send = host.wife["send"]
    with pytest.raises(TypeError, match="At"):
        await send([At("all"), "全体注意"])
    with pytest.raises(ValueError, match="不能为空"):
        await send("  ")
    assert host.actions.sent == []
