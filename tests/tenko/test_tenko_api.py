"""tenko-api 门面：导入路径、declare 元数据、与宿主代码同版本的 sys.path 优先级。"""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
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
    make_group_context,
    patch_entry_points,
    write_plugin,
)

from tenko.host.features import CommandPolicy, feature_service
from tenko.host.perm import PermissionChecker, PermissionRegistry
from tenko.host.plugins import PluginRuntime
from tenko.host.ratelimit import RateLimitService

ROOT = Path(__file__).resolve().parents[2]
API_SOURCE = ROOT / "packages/tenko-api/src"

PUBLIC_API = {
    "context": {"MessageContext", "context_from_session"},
    "perm": {
        "Permission",
        "require_perm",
        "get_user_perm",
        "get_group_perm",
        "require",
        "require_group",
        "private_only",
        "group_only",
    },
    "actions": {
        "send_group_message",
        "send_private_message",
        "mute_member",
        "unmute_member",
        "kick_member",
        "delete_message",
        "get_group_member",
        "list_group_members",
        "list_groups",
        "ActionServiceError",
        "ActionPermissionDenied",
        "ActionAccountUnavailable",
        "ActionTargetUnavailable",
        "ActionCapabilityUnavailable",
        "ActionExecutionError",
    },
    "accounts": {"online_accounts", "accounts_for_group"},
    "features": {"feature_enabled", "declare", "feature_required"},
    "render": {"render_or_none", "RenderService"},
    "messages": {"text_message", "report_error", "action_error_message"},
    "storage": {"data_dir", "Store", "StoreVersionError", "PluginStorageError"},
}


@pytest.mark.parametrize("module", sorted(PUBLIC_API))
def test_facade_modules_export_documented_names(module: str) -> None:
    facade = importlib.import_module(f"tenko_api.{module}")
    assert facade.__doc__
    assert PUBLIC_API[module] <= set(facade.__all__)
    for name in facade.__all__:
        assert getattr(facade, name) is not None


def test_facade_has_no_runtime_dependencies_and_is_typed() -> None:
    project = tomllib.loads((API_SOURCE.parent / "pyproject.toml").read_text())
    assert project["project"]["dependencies"] == []
    assert (API_SOURCE / "tenko_api/py.typed").is_file()
    version = importlib.import_module("tenko_api").__version__
    assert version == "0.1.0" or version.count(".") == 2


def test_facade_forwards_to_host_objects() -> None:
    from tenko.host.accounts import account_registry
    from tenko.host.actions import action_service
    from tenko_api import accounts, actions, features

    assert actions.mute_member.__self__ is action_service
    assert accounts.online_accounts() == tuple(
        a
        for a in account_registry.accounts.values()
        if account_registry.is_available(a)
    )
    assert features.feature_enabled("never-seen-plugin", "40001") is True


@pytest.mark.asyncio
async def test_perm_facade_hides_checker_argument(monkeypatch) -> None:
    from tenko_api import perm

    context = make_group_context("40001")
    monkeypatch.setattr(
        "tenko.host.perm.PermissionChecker",
        lambda: PermissionChecker(registry=PermissionRegistry()),
    )
    assert await perm.require_perm(context, perm.Permission.User)
    assert not await perm.require_perm(context, perm.Permission.Master)
    assert await perm.get_user_perm(context) == perm.Permission.User


def test_in_tree_facade_precedes_installed_copy(tmp_path) -> None:
    """升级器只切换代码目录、共享 venv：随宿主发布的门面必须压过 site-packages 里的旧副本。"""

    stale = tmp_path / "site-packages" / "tenko_api"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text('__version__ = "0.0.0-stale"\n')
    code = (
        "import sys, tenko, tenko_api;"
        "print(sys.path[0]);"
        "print(tenko_api.__file__);"
        "print(tenko_api.__version__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={"PYTHONPATH": f"{stale.parent}:{ROOT}", "PATH": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    first, location, version = result.stdout.split()
    assert Path(first) == API_SOURCE
    assert Path(location) == API_SOURCE / "tenko_api/__init__.py"
    assert version != "0.0.0-stale"


DECLARE_PLUGIN = """
from arclet.alconna import Alconna
from arclet.entari import command, plugin
from tenko_api.features import declare

plugin.metadata("声明演示", author=["t"], version="0.0.1", classifier=["host"])
declare(scope="{scope}", default_enabled={default}, rate_weight={weight})

@command.on(Alconna("声明演示"))
async def handler():
    return "ok"
"""


def declare_runtime(monkeypatch, tmp_path, module, **values) -> PluginRuntime:
    write_plugin(tmp_path, monkeypatch, module, DECLARE_PLUGIN.format(**values))
    patch_entry_points(monkeypatch, entry("decl", module))
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"enabled": ["decl"]}))
    return PluginRuntime(tmp_path / "no_builtin", state_file=state_file)


@pytest.mark.asyncio
async def test_declare_sets_host_metadata_and_cannot_grant_protection(
    entari_plugin_host, monkeypatch, tmp_path
) -> None:
    runtime = declare_runtime(
        monkeypatch,
        tmp_path,
        "tenko_t_declare",
        scope="global",
        default=False,
        weight=7,
    )
    try:
        await runtime.load_all()
        metadata = runtime.loaded_plugins["decl"].metadata
        assert (metadata.feature_scope, metadata.default_switch) == ("global", False)
        assert metadata.rate_weight == 7
        info = runtime.discover()[0]
        assert info.rate_weight == 7
        # 第三方插件自带的 classifier=host 不会让它成为受保护插件。
        assert not runtime.is_protected(info)
        assert feature_service.plugin_defaults == {"decl": False}
    finally:
        feature_service.plugin_defaults.clear()
        drop_module("tenko_t_declare")


@pytest.mark.asyncio
async def test_declare_default_disabled_and_rate_weight_reach_command_policy(
    entari_plugin_host, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(_EventSystem, "loop", asyncio.get_running_loop())
    runtime = declare_runtime(
        monkeypatch,
        tmp_path,
        "tenko_t_declare2",
        scope="group",
        default=False,
        weight=5,
    )
    features = feature_service
    # 窗口权重上限 6：权重 5 的命令第二次就超限，权重 1 则要第六次。
    limiter = RateLimitService(max_weight=6, cooldown_seconds=10, blacklist_seconds=0)
    policy = CommandPolicy(
        features,
        limiter,
        plugin_runtime=runtime,
        permission_checker=PermissionChecker(registry=PermissionRegistry()),
    )
    account = FakeAccount()

    async def send(group: str) -> None:
        await dispatch(
            entari_plugin_host, account, message_event("/声明演示", group=group)
        )

    try:
        with entari_command_dispatch(policy):
            await runtime.load_all()
            # 默认关闭：没有显式开启的群被拒绝；显式开启的群可用。
            await send("40001")
            assert "插件已关闭" in account.sent[-1]
            features.enable("decl", "40002")
            await send("40002")
            assert account.sent[-1] == "ok"
            # 限流静默：超限后不再有任何回复。
            sent = len(account.sent)
            await send("40002")
            assert len(account.sent) == sent
    finally:
        features.plugin_defaults.clear()
        features.configure(None, default_enabled=True)
        drop_module("tenko_t_declare2")


@pytest.mark.asyncio
async def test_declare_rejects_bad_values_and_foreign_callers(
    entari_plugin_host,
) -> None:
    from tenko_api.features import declare

    with pytest.raises(ValueError, match="scope"):
        declare(scope="guild")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="rate_weight"):
        declare(rate_weight=0)
    with pytest.raises(TypeError, match="default_enabled"):
        declare(default_enabled="no")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        declare(level=16)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="第三方插件"):
        declare()
