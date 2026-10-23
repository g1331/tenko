"""调用统计经真实 Entari 分发的回归测试。"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from arclet.letoderea.scope import global_propagators
from arclet.letoderea.utils import _EventSystem

from command_dispatch_support import (
    FakeAccount,
    FakeProtocol,
    dispatch,
    entari_command_dispatch,
    message_event,
)
from external_plugin_support import drop_module, entry, patch_entry_points, write_plugin
from perm_support import make_checker
from tenko.host import guards, stats
from tenko.host.features import CommandPolicy, FeatureService
from tenko.host.perm import Permission
from tenko.host.plugins.runtime import PluginRuntime

PLUGIN = """
from arclet.alconna import Alconna, Option, store_true
from arclet.entari import ExceptionEvent, MessageCreatedEvent, Session, command, plugin

from tenko_api.perm import Permission, require

plugin.metadata("统计插件", author=["t"], version="0.0.1")

@command.on(Alconna("好"))
async def good():
    return "好的"

@command.on(Alconna("选", Option("-v", action=store_true, default=False)))
async def pick():
    return "选了"

@command.on(Alconna("坏"))
async def bad():
    raise RuntimeError("坏了")

@command.on(Alconna("管"))
@require(Permission.GroupAdmin)
async def admin():
    return "管好了"

@plugin.listen(MessageCreatedEvent)
async def quiet():
    pass

@plugin.listen(MessageCreatedEvent)
async def echo(session: Session):
    if session.content == "回声":
        await session.send("回声")

# ExceptionEvent 的提供者会对参数注解调用 issubclass：计时器的参数不能是联合类型。
@plugin.listen(ExceptionEvent)
async def on_error(event: ExceptionEvent):
    pass
"""


class CountingProtocol(FakeProtocol):
    async def send_message(self, channel, message, *args, **kwargs):
        # 生产里由 GuardedProtocol.call_api 计数。
        stats.record_api("message.create")
        return await super().send_message(channel, message, *args, **kwargs)


@pytest_asyncio.fixture
async def env(entari_plugin_host, monkeypatch, tmp_path):
    monkeypatch.setattr(_EventSystem, "loop", asyncio.get_running_loop())
    monkeypatch.setattr(stats, "call_stats", stats.CallStats())
    write_plugin(tmp_path, monkeypatch, "tenko_t_stats", PLUGIN)
    patch_entry_points(monkeypatch, entry("stats_ext", "tenko_t_stats"))
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["stats_ext"]}))
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    checker = make_checker()
    monkeypatch.setattr(guards, "permission_checker", checker)
    policy = CommandPolicy(
        FeatureService(), plugin_runtime=runtime, permission_checker=checker
    )
    stats.install()
    try:
        with entari_command_dispatch(policy):
            await runtime.load_all()
            account = FakeAccount()
            account.protocol = CountingProtocol()

            async def send(content: str) -> None:
                await dispatch(entari_plugin_host, account, message_event(content))

            yield send
    finally:
        global_propagators.remove(stats._tracker)
        drop_module("tenko_t_stats")


def outcomes() -> dict[tuple[str, str], tuple[int, int]]:
    result: dict[tuple[str, str], tuple[int, int]] = {}
    for r in stats.call_stats.records():
        count, replies = result.get((r.handler, r.outcome), (0, 0))
        result[(r.handler, r.outcome)] = (count + r.count, replies + r.replies)
    return result


@pytest.mark.asyncio
async def test_calls_are_counted_by_outcome(env) -> None:
    for content in ("/好", "/坏", "/管", "随便聊聊", "回声", "/选", "/选 -v"):
        await env(content)

    assert outcomes() == {
        ("好", "ok"): (1, 1),
        ("坏", "error"): (1, 0),
        ("管", "denied:permission"): (1, 1),
        # 被动监听只在做了事时记一次；quiet 每条消息都经过，不记。
        ("echo", "ok"): (1, 1),
        ("选", "ok"): (2, 2),
    }
    # 选项的默认值不算用到。
    assert sorted(
        r.subcommand for r in stats.call_stats.records() if r.handler == "选"
    ) == [
        "",
        "v",
    ]
    (record,) = [r for r in stats.call_stats.records() if r.handler == "好"]
    assert (record.plugin, record.group_id) == ("stats_ext", "40001")
    assert Permission  # 守卫按成员权限拒绝


def test_importing_the_runtime_does_not_load_the_database_plugin() -> None:
    # 提前导入官方 database 插件会让它以默认配置（./data.db）初始化，读不到真实库。
    import subprocess
    import sys

    code = "import sys, tenko.runtime; print('entari_plugin_database' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip().splitlines()[-1] == "False"
