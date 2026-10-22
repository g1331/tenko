"""用真实的 Tenko 宿主加载插件，不启动网络服务。

插件依赖的宿主能力都从 ``tenko_api`` 取得，所以这里只需要：Entari 运行环境、
插件启用状态和配置。
"""

import json
import os
from types import SimpleNamespace

import pytest
import pytest_asyncio
from arclet.entari import Entari
from arclet.entari.config import EntariConfig
from satori import User

from tenko.config import DatabaseConfig
from tenko.db.bootstrap import load_database_plugin
from tenko.db.migration import run_database_migrations
from tenko.host.plugins import PluginRuntime


@pytest.fixture(scope="session")
def host(tmp_path_factory):
    """会话级宿主：工作目录切到临时目录，数据目录 ``.tenko/plugin-data`` 落在其中。"""

    root = tmp_path_factory.mktemp("tenko-host")
    previous = os.getcwd()
    os.chdir(root)
    EntariConfig(root / "entari.yml")
    Entari(ignore_self_message=True)
    (root / "state.json").write_text(json.dumps({"enabled": ["example"]}))
    yield SimpleNamespace(root=root)
    os.chdir(previous)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def runtime(host):
    """按真实的入口发现机制加载 example 插件，并传入 ``[plugin_config.example]``。

    和宿主启动一样：先加载数据库插件，加载插件时登记它的库，再初始化服务并迁移。
    """

    service = load_database_plugin(
        DatabaseConfig(url=f"sqlite+aiosqlite:///{host.root / 'main.db'}")
    )
    runtime = PluginRuntime(
        host.root / "no-builtin", state_file=host.root / "state.json"
    )
    await runtime.load_all(external_configs={"example": {"greeting": "测试问候"}})
    await service.initialize()
    await run_database_migrations(service)
    yield runtime
    runtime.unload("example")
    for engine in service.engines.values():
        await engine.dispose()


@pytest.fixture(scope="session")
def module(runtime):
    """已加载的插件模块。"""

    return runtime.loaded_plugins["example"].module


def make_session(user_id: str):
    """构造只带发送者的最小 Session，足够直接调用命令处理函数。"""

    return SimpleNamespace(user=User(user_id))
