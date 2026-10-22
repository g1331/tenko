from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from tenko.host import notify, startup
from tenko.host.accounts import AccountRegistry
from tenko.host.features import FeatureService
from tenko.host.notify import AccountAlerts, NotifySettings, parse_settings, pick_sender
from tenko.host.startup import StartupHistory, StartupNotifier


@dataclass(eq=False)
class FakeAccount:
    self_id: str
    platform: str = "qq-client"


def registry_with(*online: FakeAccount, offline: tuple[FakeAccount, ...] = ()):
    registry = AccountRegistry()
    for account in online:
        registry.register(account, available=True)
    for account in offline:
        registry.register(account, available=False)
    return registry


def test_sender_prefers_notify_account_and_falls_back_when_it_is_offline() -> None:
    first, second = FakeAccount("10001"), FakeAccount("10002")
    registry = registry_with(first, second)
    settings = NotifySettings(account="10002")

    assert pick_sender(settings, first, registry) is second
    registry.set_available(second, False)
    assert pick_sender(settings, None, registry) is first


def test_group_mode_requires_a_group() -> None:
    with pytest.raises(ValueError, match="通知群"):
        parse_settings({"mode": "group"})
    assert parse_settings({"mode": "group", "group": 40001}).group == "40001"


@pytest.mark.asyncio
async def test_brief_disconnect_is_quiet_but_long_one_reports_down_and_recovery() -> (
    None
):
    account = FakeAccount("10001")
    registry = registry_with(account)
    sent = AsyncMock()
    alerts = AccountAlerts(registry, sent, grace=0.01)

    registry.set_available(account, False)
    alerts.offline(account)
    registry.set_available(account, True)
    alerts.online(account)
    await asyncio.sleep(0.03)
    assert sent.await_count == 0

    registry.set_available(account, False)
    alerts.offline(account)
    await asyncio.sleep(0.03)
    assert "断开连接" in sent.await_args.args[0]
    registry.set_available(account, True)
    alerts.online(account)
    await asyncio.sleep(0)
    assert "已恢复在线" in sent.await_args.args[0]


@pytest.mark.asyncio
async def test_disabled_account_disconnect_is_not_reported() -> None:
    account = FakeAccount("10001")
    registry = registry_with(account)
    registry.set_enabled(account, False)
    sent = AsyncMock()

    AccountAlerts(registry, sent, grace=0.01).offline(account)
    await asyncio.sleep(0.03)

    assert sent.await_count == 0


@pytest.mark.asyncio
async def test_startup_notice_waits_for_the_notify_account(monkeypatch) -> None:
    first, second = FakeAccount("10001"), FakeAccount("10002")
    registry = registry_with(first, offline=(second,))
    monkeypatch.setattr(startup, "account_registry", registry)
    monkeypatch.setattr(notify, "account_registry", registry)
    monkeypatch.setattr(notify, "superuser_ids", lambda _platform: ("90001",))
    senders: list[str] = []

    class Actions:
        async def send_private_message(self, account, user_id, content):
            senders.append(account.self_id)

    async def settings() -> NotifySettings:
        return NotifySettings(account="10002")

    notifier = StartupNotifier(
        settings=settings,
        history=StartupHistory(),
        action_service=Actions(),
        feature_service=FeatureService(),
        started_at=0.0,
        clock=lambda: 1.0,
    )
    await notifier.mark_framework_ready()
    await notifier.mark_account_online(first)
    assert senders == []

    registry.set_available(second, True)
    await notifier.mark_account_online(second)
    assert senders == ["10002"]
    notifier._grace.cancel()


@pytest.mark.asyncio
async def test_kicked_account_is_reported_and_no_longer_used_to_send() -> None:
    first, second = FakeAccount("10001"), FakeAccount("10002")
    registry = registry_with(first, second)
    sent = AsyncMock()

    AccountAlerts(registry, sent).kicked(first, "在其他设备登录")
    await asyncio.sleep(0)

    assert "在其他设备登录" in sent.await_args.args[0]
    assert pick_sender(NotifySettings(account="10001"), None, registry) is second
