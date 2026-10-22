"""命令策略（维护、功能开关、限流、未登记插件）经真实 Entari 分发的回归测试。

Entari 识别命令前会去掉开头的 @bot、空白和昵称，所以策略必须挂在它已经解析出
命令的位置；这里每个用例都把同一条命令用多种写法发进 ``Entari.handle_event``。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from arclet.entari.plugin import load_plugin
from arclet.letoderea.utils import _EventSystem

from command_dispatch_support import (
    COMMAND_FORMS,
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
from tenko.host.features import CommandPolicy, FeatureService
from tenko.host.perm import PermissionChecker, PermissionRegistry
from tenko.host.plugins.runtime import PluginRuntime
from tenko.host.ratelimit import RateLimitService

COMMAND_PLUGIN = """
from arclet.alconna import Alconna
from arclet.entari import command, plugin

plugin.metadata("{title}", author=["t"], version="0.0.1")
CALLS = []

@command.on(Alconna("{name}"))
async def handler():
    CALLS.append(1)
"""
FORMS = pytest.mark.parametrize("form", list(COMMAND_FORMS))


def text_of(form: str, command: str = "策略外部") -> str:
    return COMMAND_FORMS[form].format(c=command)


@pytest_asyncio.fixture
async def env(entari_plugin_host, monkeypatch, tmp_path):
    monkeypatch.setattr(_EventSystem, "loop", asyncio.get_running_loop())
    write_plugin(
        tmp_path,
        monkeypatch,
        "tenko_t_policy_ext",
        COMMAND_PLUGIN.format(title="策略外部", name="策略外部"),
    )
    write_plugin(
        tmp_path,
        monkeypatch,
        "tenko_t_policy_wild",
        COMMAND_PLUGIN.format(title="策略野生", name="策略野生"),
    )
    patch_entry_points(monkeypatch, entry("ext_policy", "tenko_t_policy_ext"))
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["ext_policy"]}))
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    features = FeatureService()
    limiter = RateLimitService(max_weight=3, cooldown_seconds=10, blacklist_seconds=0)
    policy = CommandPolicy(
        features,
        limiter,
        plugin_runtime=runtime,
        permission_checker=PermissionChecker(registry=PermissionRegistry()),
    )
    # 命令前缀要在插件构建 Alconna 命令之前配置。
    with entari_command_dispatch(policy):
        await runtime.load_all()
        wild = load_plugin("tenko_t_policy_wild")
        assert wild
        account = FakeAccount()

        async def send(content: str, **kwargs) -> None:
            await dispatch(
                entari_plugin_host, account, message_event(content, **kwargs)
            )

        try:
            yield SimpleNamespace(
                features=features,
                limiter=limiter,
                account=account,
                send=send,
                ext_calls=runtime.loaded_plugins["ext_policy"].module.CALLS,
                wild_calls=wild.plugin.module.CALLS,
            )
        finally:
            drop_module("tenko_t_policy_ext", "tenko_t_policy_wild")


@FORMS
@pytest.mark.asyncio
async def test_policy_applies_to_every_form_entari_accepts(env, form) -> None:
    # 先证明这种写法确实会执行命令（否则下面的拦截断言没有意义）。
    await env.send(text_of(form))
    assert env.ext_calls == [1]

    env.features.set_maintenance("ext_policy", True)
    await env.send(text_of(form))
    env.features.set_maintenance("ext_policy", False)
    env.features.disable("ext_policy", "40001")
    await env.send(text_of(form))
    await env.send(text_of(form, "策略野生"))

    assert env.ext_calls == [1] and env.wild_calls == []
    assert "正在维护" in env.account.sent[0]
    assert "插件已关闭" in env.account.sent[1]
    assert env.account.sent[2] == "该命令所属插件未在 Tenko 登记，已拒绝执行"


@pytest.mark.asyncio
async def test_rate_limit_is_silent_and_skips_groups_with_limit_off(env) -> None:
    env.limiter.set_group_limited("40002", False)
    for form in ["plain", "at_bot", "leading_space", "nickname", "at_bot_no_space"]:
        await env.send(text_of(form))
        await env.send(text_of(form), group="40002")

    # max_weight=3：40001 前两次放行，之后无论写法都被静默拦截；40002 关闭了限流。
    assert len(env.ext_calls) == 2 + 5
    assert env.account.sent == []


@pytest.mark.asyncio
async def test_quoted_command_text_is_not_a_command(env) -> None:
    env.features.set_maintenance("ext_policy", True)
    # 被引用消息里的命令文本不是命令：不执行，也不触发策略提示。
    await env.send('<quote id="1"><author id="20002"/>/策略外部</quote>谢谢')

    assert env.ext_calls == []
    assert env.account.sent == []
