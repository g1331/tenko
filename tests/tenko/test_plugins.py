from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from loguru import logger

from perm_support import make_checker
from tenko.context import MessageContext
from tenko.host.plugins import runtime as plugin_host
from tenko.host.plugins.runtime import PluginRuntime


@dataclass
class FakePlugin:
    id: str
    is_available: bool = True


class FakeLoadResult:
    """模拟 Entari 0.19 的 ``load_plugin`` 结果对象；``plugin=None`` 表示加载失败。"""

    def __init__(self, plugin: FakePlugin | None = None) -> None:
        self._plugin = plugin
        self.error = RuntimeError("加载失败")

    def __bool__(self) -> bool:
        return self._plugin is not None

    @property
    def plugin(self) -> FakePlugin:
        assert self._plugin is not None
        return self._plugin


def make_plugin_dir(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "package_plugin").mkdir()
    (plugin_dir / "package_plugin" / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "file_plugin.py").write_text("", encoding="utf-8")
    return plugin_dir


def make_group_context(group_id: str) -> MessageContext:
    return MessageContext(
        account_id="10001",
        event_type="message-created",
        protocol_event_type="message.group.normal",
        chat_type="group",
        channel_id=group_id,
        user_id="20001",
        message_id="30001",
        text="hello",
        image_urls=(),
    )


def test_discover_plugin_shapes_without_reading_plugin_metadata(
    tmp_path: Path,
) -> None:
    plugin_dir = make_plugin_dir(tmp_path)
    (plugin_dir / "package_plugin" / "metadata.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    (plugin_dir / "not_a_plugin").mkdir()
    (plugin_dir / "_private.py").write_text("", encoding="utf-8")

    runtime = PluginRuntime(plugin_dir)

    infos = runtime.discover()

    assert [info.name for info in infos] == ["file_plugin", "package_plugin"]
    assert infos[0].qualified_name == "tenko.plugins.file_plugin"
    assert "modules.self_contained.file_plugin" in infos[0].lookup_names


@pytest.mark.asyncio
async def test_lifecycle_delegates_to_entari(monkeypatch, tmp_path: Path) -> None:
    plugin_dir = make_plugin_dir(tmp_path)
    loaded = FakePlugin("tenko.plugins.file_plugin")
    load_plugin = Mock(return_value=FakeLoadResult(loaded))
    unload_plugin = Mock(return_value=True)
    enable_plugin = AsyncMock(return_value=True)
    disable_plugin = AsyncMock(return_value=True)
    monkeypatch.setattr(plugin_host, "load_plugin", load_plugin)
    monkeypatch.setattr(plugin_host, "unload_plugin", unload_plugin)
    monkeypatch.setattr(plugin_host, "enable_plugin", enable_plugin)
    monkeypatch.setattr(plugin_host, "disable_plugin", disable_plugin)

    runtime = PluginRuntime(plugin_dir)

    assert await runtime.load("file_plugin") is loaded
    assert load_plugin.call_args.args == ("tenko.plugins.file_plugin",)
    assert runtime.unload("file_plugin")
    assert unload_plugin.call_args.args == ("tenko.plugins.file_plugin",)
    assert await runtime.enable("file_plugin")
    assert await runtime.disable("file_plugin")
    enable_plugin.assert_awaited_once_with("tenko.plugins.file_plugin")
    disable_plugin.assert_awaited_once_with("tenko.plugins.file_plugin")


@pytest.mark.asyncio
async def test_load_all_loads_explicit_configs_before_default_plugins(
    monkeypatch, tmp_path: Path
) -> None:
    plugin_dir = make_plugin_dir(tmp_path)
    native_plugins = {
        "file_plugin": FakePlugin("tenko.plugins.file_plugin"),
        "package_plugin": FakePlugin("tenko.plugins.package_plugin"),
    }
    load_plugin_mock = Mock(
        side_effect=lambda name, *args: FakeLoadResult(
            native_plugins[name.rsplit(".", 1)[-1]]
        )
    )
    monkeypatch.setattr(plugin_host, "load_plugin", load_plugin_mock)
    monkeypatch.setattr(
        plugin_host,
        "get_plugins",
        lambda subplugged=False: list(native_plugins.values()),
    )

    runtime = PluginRuntime(plugin_dir)
    await runtime.load_all({"package_plugin": {"enabled": True}})

    assert load_plugin_mock.call_args_list == [
        call("tenko.plugins.package_plugin", {"enabled": True}),
        call("tenko.plugins.file_plugin"),
    ]


@pytest.mark.asyncio
async def test_reload_uses_native_unload_and_load(monkeypatch, tmp_path: Path) -> None:
    plugin_dir = make_plugin_dir(tmp_path)
    first = FakePlugin("tenko.plugins.file_plugin")
    second = FakePlugin("tenko.plugins.file_plugin")
    load_plugin = Mock(side_effect=[FakeLoadResult(first), FakeLoadResult(second)])
    unload_plugin = Mock(return_value=True)
    monkeypatch.setattr(plugin_host, "load_plugin", load_plugin)
    monkeypatch.setattr(plugin_host, "unload_plugin", unload_plugin)

    runtime = PluginRuntime(plugin_dir)

    assert await runtime.load("file_plugin") is first
    assert await runtime.reload("file_plugin") is second
    assert unload_plugin.call_args.args == ("tenko.plugins.file_plugin",)
    assert load_plugin.call_args_list[-1].args == ("tenko.plugins.file_plugin",)


@pytest.mark.asyncio
async def test_command_owner_resolves_real_plugins_by_registered_command(
    entari_plugin_host,
) -> None:
    from arclet.alconna import command_manager

    from command_dispatch_support import message_event
    from tenko.commands import configure_command_prefix
    from tenko.host.features import CommandPolicy, FeatureService

    runtime = PluginRuntime()
    configure_command_prefix(())
    try:
        for name in ("group_manager", "perm_manager"):
            await runtime.load(name)
        # 归属取自 Entari 的插件命令登记，按 Alconna 命令名查询。
        assert runtime.command_owner("禁言").name == "group_manager"
        assert runtime.command_owner("全体禁言").name == "group_manager"
        assert runtime.command_owner("授权").name == "perm_manager"
        assert runtime.command_owner("拉黑").name == "perm_manager"
        assert runtime.command_owner("禁言x") is None
        assert runtime.command_owner("普通消息") is None

        features = FeatureService()
        features.disable("group_manager", "40001")
        policy = CommandPolicy(
            features,
            plugin_runtime=runtime,
            permission_checker=make_checker(),
        )
        session = SimpleNamespace(
            event=SimpleNamespace(_origin=message_event("/禁言 @1 2"))
        )
        mute = next(c for c in command_manager.get_commands() if c.command == "禁言")
        blocked = await policy.check(session, mute)
        assert not blocked.allowed and "插件已关闭" in (blocked.notice or "")
        features.enable("group_manager", "40001")
        assert (await policy.check(session, mute)).allowed
    finally:
        for name in ("group_manager", "perm_manager"):
            runtime.unload(name)
        configure_command_prefix()


def _entry(name: str, value: str, dist: str | None = None):
    if dist is None:
        return EntryPoint(name, value, "tenko.plugins")
    return SimpleNamespace(name=name, value=value, dist=SimpleNamespace(name=dist))


def patch_entry_points(monkeypatch, tenko=(), entari=()) -> None:
    groups = {"tenko.plugins": list(tenko), "entari.plugin": list(entari)}
    monkeypatch.setattr(
        plugin_host, "entry_points", lambda *, group: groups.get(group, [])
    )


@pytest.fixture
def log_lines():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    yield lines
    logger.remove(sink)


def test_discover_external_entry_points_validates_and_never_imports(
    monkeypatch, tmp_path: Path, log_lines
) -> None:
    # 这些模块一旦被导入就会失败：发现阶段不能导入任何插件模块。
    (tmp_path / "boom_good.py").write_text("raise RuntimeError('imported')")
    monkeypatch.syspath_prepend(tmp_path)
    patch_entry_points(
        monkeypatch,
        tenko=[
            _entry("good", "boom_good", "dist-good"),
            _entry("Bad-ID", "boom_a", "dist-bad-id"),
            _entry("attr_style", "boom_b:plugin", "dist-attr"),
            _entry("file_plugin", "boom_c", "dist-collide"),
            _entry("twin", "boom_d", "dist-twin-1"),
            _entry("twin", "boom_e", "dist-twin-2"),
            _entry("also_entari", "boom_f", "dist-both"),
        ],
        entari=[EntryPoint("x", "boom_f", "entari.plugin")],
    )

    infos = PluginRuntime(make_plugin_dir(tmp_path)).discover()

    by_name = {info.name: info for info in infos}
    assert set(by_name) == {"file_plugin", "package_plugin", "good", "also_entari"}
    good = by_name["good"]
    assert (good.source, good.qualified_name, good.path) == (
        "external",
        "boom_good",
        None,
    )
    assert good.lookup_names == ("good", "boom_good")
    assert by_name["file_plugin"].source == "builtin"
    assert "boom_good" not in sys.modules
    log = "".join(log_lines)
    for expected in ("Bad-ID", "dist-bad-id", "dist-attr", "dist-collide"):
        assert expected in log
    assert "dist-twin-1" in log and "dist-twin-2" in log
    assert "also_entari" in log and "entari.plugin" in log


@pytest.mark.asyncio
async def test_load_all_only_loads_enabled_external_plugins(
    monkeypatch, tmp_path: Path
) -> None:
    patch_entry_points(
        monkeypatch,
        tenko=[
            _entry("on", "ext_on"),
            _entry("off", "ext_off"),
            # 已启用但要求旧版 tenko-api：不加载（uv pip 会降级环境里的包而不是拒绝安装）。
            SimpleNamespace(
                name="old",
                value="ext_old",
                dist=SimpleNamespace(name="old", requires=["tenko-api<0.2,>=0.1"]),
            ),
        ],
    )
    state_file = tmp_path / "state" / "state.json"
    state_file.parent.mkdir()
    state_file.write_text(json.dumps({"enabled": ["on", "old"]}), encoding="utf-8")
    load_plugin = Mock(return_value=FakeLoadResult())
    monkeypatch.setattr(plugin_host, "load_plugin", load_plugin)

    runtime = PluginRuntime(make_plugin_dir(tmp_path), state_file=state_file)
    await runtime.load_all()

    assert [c.args[0] for c in load_plugin.call_args_list] == [
        "tenko.plugins.file_plugin",
        "tenko.plugins.package_plugin",
        "ext_on",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "{not json", '{"enabled": "on"}', "[]"])
async def test_missing_or_malformed_state_enables_nothing(
    monkeypatch, tmp_path: Path, log_lines, content
) -> None:
    patch_entry_points(monkeypatch, tenko=[_entry("on", "ext_on")])
    state_file = tmp_path / "state.json"
    if content is not None:
        state_file.write_text(content, encoding="utf-8")
    load_plugin = Mock(return_value=FakeLoadResult())
    monkeypatch.setattr(plugin_host, "load_plugin", load_plugin)

    runtime = PluginRuntime(make_plugin_dir(tmp_path), state_file=state_file)
    await runtime.load_all()

    assert "ext_on" not in [c.args[0] for c in load_plugin.call_args_list]
    assert not runtime.is_enabled("on")
    assert (content is not None) == ("状态文件无效" in "".join(log_lines))


@pytest.mark.asyncio
async def test_failing_external_load_does_not_abort_startup(
    monkeypatch, tmp_path: Path, log_lines
) -> None:
    patch_entry_points(
        monkeypatch, tenko=[_entry("bad", "ext_bad"), _entry("ok", "ext_ok")]
    )
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["bad", "ok"]}), encoding="utf-8")

    def load_plugin(name, *args):
        if name == "ext_bad":
            raise ImportError("broken")
        return FakeLoadResult()

    load_plugin_mock = Mock(side_effect=load_plugin)
    monkeypatch.setattr(plugin_host, "load_plugin", load_plugin_mock)

    runtime = PluginRuntime(tmp_path / "none", state_file=state_file)
    await runtime.load_all()

    assert [c.args[0] for c in load_plugin_mock.call_args_list] == [
        "ext_bad",
        "ext_ok",
    ]
    assert "扩展插件加载失败: bad" in "".join(log_lines)


@pytest.mark.asyncio
async def test_set_enabled_persists_external_state_atomically(
    monkeypatch, tmp_path: Path
) -> None:
    patch_entry_points(monkeypatch, tenko=[_entry("ext", "ext_mod")])
    state_file = tmp_path / "nested" / "state.json"
    enable_plugin = AsyncMock(return_value=True)
    disable_plugin = AsyncMock(return_value=True)
    monkeypatch.setattr(plugin_host, "enable_plugin", enable_plugin)
    monkeypatch.setattr(plugin_host, "disable_plugin", disable_plugin)
    loaded: list[FakePlugin] = []
    monkeypatch.setattr(plugin_host, "get_plugins", lambda subplugged=False: loaded)
    runtime = PluginRuntime(tmp_path / "none", state_file=state_file)

    # 未加载：只写状态文件，重启后才生效。
    assert await runtime.set_enabled("ext", True)
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"enabled": ["ext"]}
    assert runtime.is_enabled("ext") and runtime.restart_required("ext")
    enable_plugin.assert_not_awaited()
    assert [p.name for p in state_file.parent.iterdir()] == ["state.json"]

    # 已加载：同时应用 Entari 的运行期启停。
    loaded.append(FakePlugin("ext_mod"))
    assert not runtime.restart_required("ext")
    assert await runtime.set_enabled("ext", False)
    disable_plugin.assert_awaited_once_with("ext_mod")
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"enabled": []}
    enable_plugin.return_value = False
    assert not await runtime.set_enabled("ext", True)
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"enabled": []}


def _write_command_plugin(root: Path, module: str, command: str) -> None:
    (root / f"{module}.py").write_text(
        "from arclet.alconna import Alconna\n"
        "from arclet.entari import command, plugin\n"
        f'plugin.metadata("{module}", author=["t"], version="0.0.1")\n'
        f'@command.on(Alconna("{command}"))\n'
        "async def handler():\n"
        '    return "ok"\n',
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_command_owner_covers_external_and_flags_unmanaged(
    entari_plugin_host, monkeypatch, tmp_path: Path
) -> None:
    from arclet.entari.plugin import load_plugin, unload_plugin

    from tenko.commands import configure_command_prefix

    _write_command_plugin(tmp_path, "tenko_t_external", "外部演示")
    _write_command_plugin(tmp_path, "tenko_t_unmanaged", "野生命令")
    monkeypatch.syspath_prepend(tmp_path)
    patch_entry_points(monkeypatch, tenko=[_entry("ext_demo", "tenko_t_external")])
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["ext_demo"]}), encoding="utf-8")
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    configure_command_prefix(())
    try:
        await runtime.load_all()
        assert load_plugin("tenko_t_unmanaged")

        owner = runtime.command_owner("外部演示")
        assert (owner.source, owner.name) == ("external", "ext_demo")
        assert runtime.is_loaded("ext_demo") and not runtime.restart_required(
            "ext_demo"
        )
        unmanaged = runtime.command_owner("野生命令")
        assert (unmanaged.source, unmanaged.name) == (
            "unmanaged",
            "tenko_t_unmanaged",
        )
        assert runtime.command_owner("无此命令") is None
    finally:
        configure_command_prefix()
        for module in ("tenko_t_external", "tenko_t_unmanaged"):
            unload_plugin(module)
            sys.modules.pop(module, None)


CONFIG_PLUGIN = """
from arclet.entari import BasicConfModel, plugin

class Cfg(BasicConfModel):
    greeting: str = "默认"
    limit: int = 1

    def __post_init__(self):
        # Entari 对标准 dataclass 配置模型不做类型校验，需要时由插件自己检查。
        if not isinstance(self.limit, int):
            raise ValueError("limit 必须是整数")

plugin.metadata("配置演示", author=["t"], version="0.0.1", config=Cfg)
config = plugin.get_config(Cfg)
"""


@pytest.mark.asyncio
async def test_external_plugin_receives_its_plugin_config_table(
    entari_plugin_host, monkeypatch, tmp_path: Path, log_lines
) -> None:
    from arclet.entari.plugin import unload_plugin

    (tmp_path / "tenko_t_cfg.py").write_text(CONFIG_PLUGIN, encoding="utf-8")
    (tmp_path / "tenko_t_cfg_off.py").write_text(CONFIG_PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    patch_entry_points(
        monkeypatch,
        tenko=[_entry("cfg", "tenko_t_cfg"), _entry("cfg_off", "tenko_t_cfg_off")],
    )
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["cfg"]}), encoding="utf-8")
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    try:
        await runtime.load_all(
            external_configs={
                "cfg": {"greeting": "你好", "limit": 5},
                # 没有启用的插件不会被加载，它的配置也不会被使用。
                "cfg_off": {"greeting": "不应出现"},
            }
        )
        module = runtime.loaded_plugins["cfg"].module
        assert (module.config.greeting, module.config.limit) == ("你好", 5)
        assert not runtime.is_loaded("cfg_off")
    finally:
        for module in ("tenko_t_cfg", "tenko_t_cfg_off"):
            unload_plugin(module)
            sys.modules.pop(module, None)


@pytest.mark.asyncio
async def test_apply_config_reloads_running_plugin_and_keeps_it_on_failure(
    entari_plugin_host, monkeypatch, tmp_path: Path
) -> None:
    import asyncio

    from arclet.entari.plugin import unload_plugin
    from arclet.letoderea.utils import _EventSystem

    # 会话级 Entari 绑定的是别的事件循环；生产环境只有一个循环。
    monkeypatch.setattr(_EventSystem, "loop", asyncio.get_running_loop())
    (tmp_path / "tenko_t_cfg4.py").write_text(CONFIG_PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    patch_entry_points(monkeypatch, tenko=[_entry("cfg4", "tenko_t_cfg4")])
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["cfg4"]}), encoding="utf-8")
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    try:
        await runtime.load_all(external_configs={"cfg4": {"limit": 1}})
        await runtime.apply_config("cfg4", {"limit": 7})
        assert runtime.loaded_plugins["cfg4"].module.config.limit == 7

        with pytest.raises(ImportError, match="limit"):
            await runtime.apply_config("cfg4", {"limit": "不是数字"})
        assert runtime.loaded_plugins["cfg4"].module.config.limit == 7
    finally:
        unload_plugin("tenko_t_cfg4")
        sys.modules.pop("tenko_t_cfg4", None)


@pytest.mark.asyncio
async def test_external_plugin_without_config_table_uses_model_defaults(
    entari_plugin_host, monkeypatch, tmp_path: Path
) -> None:
    from arclet.entari.plugin import unload_plugin

    (tmp_path / "tenko_t_cfg2.py").write_text(CONFIG_PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    patch_entry_points(monkeypatch, tenko=[_entry("cfg2", "tenko_t_cfg2")])
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["cfg2"]}), encoding="utf-8")
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    try:
        await runtime.load_all()
        assert runtime.loaded_plugins["cfg2"].module.config.greeting == "默认"
    finally:
        unload_plugin("tenko_t_cfg2")
        sys.modules.pop("tenko_t_cfg2", None)


@pytest.mark.asyncio
async def test_invalid_plugin_config_fails_only_that_plugin(
    entari_plugin_host, monkeypatch, tmp_path: Path, log_lines
) -> None:
    from arclet.entari.plugin import unload_plugin

    (tmp_path / "tenko_t_cfg3.py").write_text(CONFIG_PLUGIN, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    patch_entry_points(monkeypatch, tenko=[_entry("cfg3", "tenko_t_cfg3")])
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["cfg3"]}), encoding="utf-8")
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    try:
        await runtime.load_all(external_configs={"cfg3": {"limit": "不是数字"}})
        assert not runtime.is_loaded("cfg3")
        assert "扩展插件加载失败: cfg3" in "".join(log_lines)
    finally:
        unload_plugin("tenko_t_cfg3")
        sys.modules.pop("tenko_t_cfg3", None)


@pytest.mark.asyncio
async def test_first_enable_loads_plugin_installed_after_startup(
    entari_plugin_host, monkeypatch, tmp_path: Path
) -> None:
    from arclet.entari.plugin import unload_plugin

    from tenko.host.plugins.manager import PluginManager

    monkeypatch.syspath_prepend(tmp_path)
    patch_entry_points(monkeypatch)
    state_file = tmp_path / "state.json"
    lock = tmp_path / "plugins.lock"
    runtime = PluginRuntime(tmp_path / "no_builtin", state_file=state_file)
    manager = PluginManager(python=sys.executable, runtime=runtime, cwd=tmp_path)
    manager.startup_check()  # 启动时插件锁为空
    await runtime.load_all()

    # 运行中安装：插件锁只新增条目，新插件启用即可加载，命令也能归到它名下。
    _write_command_plugin(tmp_path, "tenko_t_live", "热加载演示")
    _write_command_plugin(tmp_path, "tenko_t_late", "晚到演示")
    patch_entry_points(
        monkeypatch,
        tenko=[_entry("live", "tenko_t_live"), _entry("late", "tenko_t_late")],
    )
    lock.write_text("tenko-plugin-live==1.0.0\n", encoding="utf-8")
    try:
        live = next(i for i in runtime.discover() if i.name == "live")
        assert await runtime.set_enabled(live, True)
        assert await manager.load_now(runtime, live) is None
        assert runtime.is_loaded("live") and not runtime.restart_required("live")
        assert runtime.command_owner("热加载演示").name == "live"

        # 已有的包版本变了：本进程里可能是旧代码，必须重启。
        lock.write_text(
            "tenko-plugin-live==1.0.1\ntenko-plugin-late==1.0.0\n", encoding="utf-8"
        )
        manager._startup_lock = {"tenko-plugin-live": "tenko-plugin-live==1.0.0"}
        late = next(i for i in runtime.discover() if i.name == "late")
        assert await runtime.set_enabled(late, True)
        assert "变化" in (await manager.load_now(runtime, late) or "")
        assert runtime.restart_required("late") and "tenko_t_late" not in sys.modules
    finally:
        for module in ("tenko_t_live", "tenko_t_late"):
            unload_plugin(module)
            sys.modules.pop(module, None)


def test_package_keeps_cross_version_import_names() -> None:
    # 旧版升级器的导入检查和 tenko-registry probe 都从包入口导入，搬家后也要能用。
    import tenko.host.plugins as package
    from tenko.host.plugins import runtime

    assert package.PluginRuntime is runtime.PluginRuntime
    assert package.api_incompatibility is runtime.api_incompatibility
