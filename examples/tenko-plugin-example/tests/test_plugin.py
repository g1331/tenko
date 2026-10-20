from unittest.mock import AsyncMock

from arclet.entari.command import Query
from conftest import make_session

from tenko.host.plugins import PluginRuntime


def test_entry_point_is_discovered_but_disabled_by_default(tmp_path):
    """第三方插件默认关闭：没有状态文件时只被发现，不被加载。"""

    runtime = PluginRuntime(tmp_path / "no-builtin", state_file=tmp_path / "none.json")
    info = next(item for item in runtime.discover() if item.name == "example")
    assert info.source == "external"
    assert not runtime.is_enabled(info)


async def test_plugin_loads_with_config_and_declared_metadata(runtime, module):
    assert runtime.is_loaded("example")
    assert module.config.greeting == "测试问候"
    metadata = runtime.loaded_plugins["example"].metadata
    assert (metadata.feature_scope, metadata.rate_weight) == ("group", 1)
    # 命令归属于 example，所以功能开关和限流会按插件 ID 生效。
    assert runtime.command_owner("示例").name == "example"


async def test_command_counts_visits_and_remembers_the_last_note(module, monkeypatch):
    monkeypatch.setattr(module, "require_perm", AsyncMock(return_value=True))
    session = make_session("20001")

    first = await module.example.callable_target(session, Query("note", "备忘"))
    second = await module.example.callable_target(session, Query("note", ""))

    assert str(first) == "测试问候，这是你第 1 次使用示例命令"
    assert "第 2 次" in str(second) and "上次备注：备忘" in str(second)
    assert module.visits.load()["20001"] == {"count": 2, "note": "备忘"}


async def test_command_rejects_users_without_permission(module, monkeypatch):
    monkeypatch.setattr(module, "require_perm", AsyncMock(return_value=False))
    result = await module.example.callable_target(
        make_session("20002"), Query("note", "")
    )
    assert str(result) == "权限不足"
