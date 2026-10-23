"""声明式权限装饰器、feature_required 和受控插件经真实 Entari 分发的回归测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from arclet.letoderea.utils import _EventSystem

from command_dispatch_support import (
    FakeAccount,
    dispatch,
    entari_command_dispatch,
    message_event,
)
from external_plugin_support import (
    drop_module,
    entry,
    patch_entry_points,
    write_plugin,
)
from perm_support import make_checker
from tenko.host import guards
from tenko.host import features as features_module
from tenko.host.features import CommandPolicy, FeatureService
from tenko.host.perm import Permission
from tenko.host.plugins.runtime import PluginRuntime

GUARD_PLUGIN = """
from arclet.alconna import Alconna
from arclet.entari import MessageCreatedEvent, command, plugin

from tenko_api.features import feature_required
from tenko_api.perm import Permission, group_only, private_only, require

plugin.metadata("守卫插件", author=["t"], version="0.0.1")
CALLS = []

# 装饰器在 command.on 之下：先作用在函数上，Subscriber 创建时再收集。
@command.on(Alconna("下方"))
@require(Permission.GroupAdmin)
async def below():
    CALLS.append("below")

# 装饰器在 command.on 之上：直接挂在已创建的 Subscriber 上。
@require(Permission.GroupAdmin)
@command.on(Alconna("上方"))
async def above():
    CALLS.append("above")

@command.on(Alconna("仅群"))
@group_only()
async def only_group():
    CALLS.append("group")

@command.on(Alconna("仅私"))
@private_only()
async def only_private():
    CALLS.append("private")

@plugin.listen(MessageCreatedEvent)
@feature_required()
async def silent_listener():
    CALLS.append("silent")

@plugin.listen(MessageCreatedEvent)
@feature_required(notify=True)
async def notify_listener():
    CALLS.append("notify")
"""
CONTROLLED_PLUGIN = """
from arclet.alconna import Alconna
from arclet.entari import command, plugin

from tenko_api.features import declare

plugin.metadata("受控插件", author=["t"], version="0.0.1")
declare(controlled=True)
CALLS = []

@command.on(Alconna("受控"))
async def handler():
    CALLS.append(1)
"""
GROUP, USER = "40001", "20001"
LISTENERS = {"silent", "notify"}


@pytest_asyncio.fixture
async def env(entari_plugin_host, monkeypatch, tmp_path):
    monkeypatch.setattr(_EventSystem, "loop", asyncio.get_running_loop())
    write_plugin(tmp_path, monkeypatch, "tenko_t_guard", GUARD_PLUGIN)
    write_plugin(tmp_path, monkeypatch, "tenko_t_ctl", CONTROLLED_PLUGIN)
    patch_entry_points(
        monkeypatch,
        entry("guard_ext", "tenko_t_guard"),
        entry("ctl_ext", "tenko_t_ctl"),
    )
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["guard_ext", "ctl_ext"]}))
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    checker = make_checker()
    monkeypatch.setattr(guards, "permission_checker", checker)
    features = FeatureService()
    monkeypatch.setattr(guards, "feature_service", features)
    # declare() 把受控插件登记在全局的功能服务上。
    monkeypatch.setattr(features_module, "feature_service", features)
    policy = CommandPolicy(features, plugin_runtime=runtime, permission_checker=checker)
    with entari_command_dispatch(policy):
        await runtime.load_all()
        account = FakeAccount()
        guard = runtime.loaded_plugins["guard_ext"]

        async def send(content: str, **kwargs) -> list[str]:
            before = len(account.sent)
            await dispatch(
                entari_plugin_host, account, message_event(content, **kwargs)
            )
            return account.sent[before:]

        try:
            yield SimpleNamespace(
                features=features,
                store=checker.store,
                send=send,
                guard=guard,
                calls=guard.module.CALLS,
                controlled_calls=runtime.loaded_plugins["ctl_ext"].module.CALLS,
            )
        finally:
            drop_module("tenko_t_guard", "tenko_t_ctl")


def command_calls(env) -> list[str]:
    return [call for call in env.calls if call not in LISTENERS]


# (命令, 成员权限, 私聊, 期望执行, 期望回复)
DECORATOR_CASES = {
    "below_allowed": ("下方", Permission.GroupAdmin, False, ["below"], []),
    "above_allowed": ("上方", Permission.GroupAdmin, False, ["above"], []),
    "below_denied": (
        "下方",
        Permission.User,
        False,
        [],
        ["权限不足（当前权限：成员 / 需要权限：群管理员）"],
    ),
    "above_denied": (
        "上方",
        Permission.User,
        False,
        [],
        ["权限不足（当前权限：成员 / 需要权限：群管理员）"],
    ),
    "group_only_in_private": (
        "仅群",
        Permission.User,
        True,
        [],
        ["该指令只能在群聊中使用"],
    ),
    "group_only_in_group": ("仅群", Permission.User, False, ["group"], []),
    "private_only_in_group": (
        "仅私",
        Permission.User,
        False,
        [],
        ["该指令只能在私聊中使用"],
    ),
    "private_only_in_private": ("仅私", Permission.User, True, ["private"], []),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", DECORATOR_CASES, ids=list(DECORATOR_CASES))
async def test_decorators_allow_deny_and_stay_silent(env, name) -> None:
    command, level, private, calls, replies = DECORATOR_CASES[name]
    if level == Permission.GroupAdmin:
        await env.store.grant("qq-client", GROUP, USER)

    sent = await env.send(f"/{command}", group=None if private else GROUP)

    assert command_calls(env) == calls
    assert sent == replies


@pytest.mark.asyncio
async def test_requirements_are_recorded_for_help_in_either_order(env) -> None:
    requires = guards.command_requirements(env.guard)

    assert requires["下方"] == requires["上方"] == {"user": Permission.GroupAdmin}
    assert guards.requirement_text(requires["下方"]) == "群管理员"
    assert guards.requirement_text(requires["仅群"]) == "仅群聊"


@pytest.mark.asyncio
async def test_feature_required_checks_owner_plugin_switch(env) -> None:
    await env.send("闲聊")
    assert env.calls == ["silent", "notify"]

    env.features.disable("guard_ext", GROUP)
    sent = await env.send("闲聊")
    # 关闭后两个监听器都不执行；只有 notify=True 的回复提示。
    assert env.calls == ["silent", "notify"]
    assert sent == ["守卫插件插件已关闭"]

    env.features.set_maintenance("guard_ext", True)
    assert await env.send("闲聊") == ["守卫插件插件正在维护~"]
    # 私聊不受群开关约束。
    await env.send("闲聊", group=None)
    assert env.calls == ["silent", "notify", "silent", "notify"]


@pytest.mark.asyncio
async def test_controlled_plugin_is_off_by_default_and_bot_admin_only_in_private(
    env,
) -> None:
    sent = await env.send("/受控")
    assert env.controlled_calls == [] and "受控插件插件已关闭" in sent[0]

    env.features.enable("ctl_ext", GROUP)
    await env.send("/受控")
    assert env.controlled_calls == [1]

    sent = await env.send("/受控", group=None)
    assert sent == ["受控插件插件在私聊中仅 BOT 管理员可用"]
    await env.store.grant("qq-client", "", USER)
    await env.send("/受控", group=None)
    assert env.controlled_calls == [1, 1]
