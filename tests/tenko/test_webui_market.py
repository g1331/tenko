"""WebUI 插件市场：只能装目录里的插件、任务结果、插件配置表单、重启。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from arclet.entari import BasicConfModel
from test_registry import index_data
from test_webui import _login

from tenko.config import TenkoConfig
from tenko.host.plugin_manager import PluginChange, PluginManagerError
from tenko.host.registry import Registry
from tenko.webui import WebUIService


class FakeManager:
    def __init__(self, tmp_path) -> None:
        self.history_dir = tmp_path / "history"
        self.sources = []
        self.fail: PluginManagerError | None = None

    def status(self):
        return []

    def drift(self):
        return []

    async def install(self, source, *, actor):
        self.sources.append(source)
        if self.fail:
            raise self.fail
        return PluginChange("install", "example", "tenko-plugin-example", None, "0.1.0")


CONFIG = """# 手工维护的说明
[webui]
enabled = true  # 行尾注释

[plugin_config.example]
# 问候语
greeting = "嗨"
"""


class ExampleConfig(BasicConfModel):
    greeting: str = "你好"
    limit: int = 3


@pytest_asyncio.fixture
async def market(tmp_path, tenko_database):
    path = tmp_path / "tenko.toml"
    path.write_text(CONFIG)
    config = TenkoConfig.load(path)
    native = SimpleNamespace(metadata=SimpleNamespace(config=ExampleConfig))
    runtime = SimpleNamespace(
        discover=lambda: [
            SimpleNamespace(
                name="example",
                source="external",
                native_plugin=native,
                display_name="示例",
            ),
            SimpleNamespace(name="evil", source="external", native_plugin=None),
        ],
        is_protected=lambda _info: False,
        is_enabled=lambda _info: False,
    )
    from satori.server import Server

    server = Server(host="127.0.0.1", port=0)
    service = WebUIService(
        server, config.webui, tenko_config=config, plugin_runtime=runtime
    )

    async def fetch(_url: str, _limit: int) -> bytes:
        return json.dumps(index_data()).encode()

    service.market._registry = Registry(
        ["https://r/index.json"], tmp_path / "reg", fetch=fetch
    )
    service.market._manager = FakeManager(tmp_path)
    transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        await _login(client)
        yield SimpleNamespace(client=client, service=service, path=path)


async def wait_task(client, task_id: str) -> dict:
    for _ in range(100):
        data = (await client.get(f"/webui/api/manage/plugin-tasks/{task_id}")).json()[
            "data"
        ]
        if data["state"] != "running":
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("任务没有结束")


@pytest.mark.asyncio
async def test_market_installs_only_listed_plugins_and_reports_failures(market) -> None:
    client, manager = market.client, market.service.market._manager
    listing = (await client.get("/webui/api/market")).json()["data"]
    assert [(p["id"], p["latest"]) for p in listing["plugins"]] == [
        ("example", "0.1.0")
    ]

    url = "/webui/api/manage/plugin-tasks"
    unlisted = await client.post(
        url,
        json={"action": "install", "plugin_id": "tenko-plugin-other", "confirm": True},
    )
    assert unlisted.status_code == 400 and "聊天" in unlisted.json()["error"]["message"]

    started = await client.post(
        url, json={"action": "install", "plugin_id": "example", "confirm": True}
    )
    done = await wait_task(client, started.json()["data"]["id"])
    assert done["state"] == "succeeded" and done["changes"][0]["to"] == "0.1.0"
    assert manager.sources[0].pins and manager.sources[0].trust == "listed"

    manager.fail = PluginManagerError("校验", "入口不符", restored=True)
    started = await client.post(
        url, json={"action": "install", "plugin_id": "example", "confirm": True}
    )
    failed = await wait_task(client, started.json()["data"]["id"])
    assert failed["state"] == "failed" and failed["restored"] is True
    assert "入口不符" in failed["message"]


@pytest.mark.asyncio
async def test_plugin_config_form_validates_and_writes_toml(market) -> None:
    client, url = market.client, "/webui/api/manage/plugin-config/example"
    form = (await client.get(url)).json()["data"]
    assert form["values"] == {"greeting": "嗨", "limit": 3}
    assert form["editable"] == ["greeting", "limit"]

    for bad in (
        {"values": {"limit": "many"}, "confirm": True},
        {"values": {"unknown": 1}, "confirm": True},
        {"values": {"limit": 5}},
    ):
        assert (await client.post(url, json=bad)).status_code == 400
    saved = await client.post(url, json={"values": {"limit": 5}, "confirm": True})

    assert saved.json()["data"]["restart_required"] is True
    # 只追加改动的键，注释和其他内容逐字保留。
    assert market.path.read_text() == CONFIG + "limit = 5\n"


@pytest.mark.asyncio
async def test_restart_needs_upgrade_system(market) -> None:
    url = "/webui/api/manage/restart"
    calls = []
    updater = SimpleNamespace(
        enabled=False,
        spawn_restart_watcher=lambda: calls.append("arm") or True,
        request_graceful_shutdown=lambda: calls.append("exit") or True,
    )
    market.service.market._updater = updater
    assert (await market.client.post(url, json={"confirm": True})).status_code == 409

    updater.enabled = True
    assert (await market.client.post(url, json={"confirm": True})).status_code == 200
    await asyncio.sleep(1.1)
    assert calls == ["arm", "exit"]


@pytest.mark.asyncio
async def test_blocked_plugin_cannot_be_enabled_from_panel(market) -> None:
    market.service.management = SimpleNamespace(ready=True, repository=object())
    await market.client.get("/webui/api/market")  # 刷新目录缓存

    response = await market.client.post(
        "/webui/api/manage/plugins/evil", json={"enabled": True, "confirm": True}
    )

    assert response.status_code == 403 and "封禁" in response.json()["error"]["message"]
