from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from arclet.entari import Entari
from arclet.entari.config import EntariConfig
from arclet.entari.plugin import load_plugin, unload_plugin
from arclet.letoderea.utils import _EventSystem


@pytest.fixture(scope="session")
def entari_plugin_host():
    if not EntariConfig._inited:
        EntariConfig(Path("/tmp/tenko-entari-test.yml"))
    return Entari(ignore_self_message=True)


def unload_plugin_and_wait(plugin_id: str) -> None:
    """卸载插件并等待订阅者清理完成。

    Entari 0.19 / Letoderea 0.22 起，订阅者（含 Alconna 命令注册）在事件循环里
    异步清理；同步测试不让出循环就会残留上一个插件的命令。
    """

    loop = _EventSystem.loop
    if loop is None or loop.is_running():
        unload_plugin(plugin_id)
        return
    before = asyncio.all_tasks(loop)
    unload_plugin(plugin_id)
    # 只等待本次卸载新建的清理任务；循环里的其他任务不属于这个插件。
    if cleanup := asyncio.all_tasks(loop) - before:
        loop.run_until_complete(asyncio.wait(cleanup, timeout=5))


@pytest.fixture
def loaded_plugin(entari_plugin_host, request):
    plugin_id = f"tenko.plugins.{request.param}"
    loaded = load_plugin(plugin_id)
    assert loaded, getattr(loaded, "error", None)
    yield loaded.plugin.module
    unload_plugin_and_wait(plugin_id)
