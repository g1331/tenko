"""WebUI 插件市场：只能装目录里的插件、任务结果、插件配置表单、重启。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from arclet.entari import BasicConfModel
from test_registry import index_data
from test_webui import _login

from tenko.config import TenkoConfig
from tenko.host.plugins.manager import PluginChange, PluginManagerError
from tenko.host.plugins.registry import Registry
from tenko.host.updater import (
    CheckResult,
    PluginCompatibilityError,
    Release,
    UpdateChannel,
    UpdatePolicy,
    parse_version,
)
from tenko.webui import WebUIService


class FakeManager:
    def __init__(self, tmp_path) -> None:
        self.history_dir = tmp_path / "history"
        self.sources = []
        self.fail: PluginManagerError | None = None

    def status(self):
        return []

    def suspended(self):
        return {}

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
        apply_config=AsyncMock(),
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

    assert saved.json()["data"]["values"]["limit"] == 5
    apply = market.service.plugin_runtime.apply_config
    apply.assert_awaited_once_with("example", {"greeting": "嗨", "limit": 5})
    # 只追加改动的键，注释和其他内容逐字保留。
    assert market.path.read_text() == CONFIG + "limit = 5\n"

    # 插件按新配置加载失败：旧插件继续运行，配置文件改回去。
    apply.side_effect = ValueError("limit 太大")
    failed = await client.post(url, json={"values": {"limit": 9}, "confirm": True})
    assert (
        failed.status_code == 409 and "limit 太大" in failed.json()["error"]["message"]
    )
    assert market.path.read_text() == CONFIG + "limit = 5\n"


@pytest.mark.asyncio
async def test_core_config_validates_and_keeps_legacy_layout(market) -> None:
    client, url = market.client, "/webui/api/manage/config"
    fields = {f["key"]: f for f in (await client.get(url)).json()["data"]["fields"]}
    assert fields["render.quality"]["value"] == 85

    for bad in (
        {"render.quality": 0},
        {"upgrade.channel": "nightly"},
        {"notify.group": "abc"},
        {"onebot.access_token": "x"},
    ):
        response = await client.post(url, json={"values": bad})
        assert response.status_code == 400, bad
    assert market.path.read_text() == CONFIG

    saved = await client.post(
        url, json={"values": {"render.quality": 90, "log_level": "DEBUG"}}
    )
    assert saved.json()["data"]["restart_required"] is True
    text = market.path.read_text()
    assert text.startswith(CONFIG)
    # 没有 [basic] 节时写旧 [runtime]，避免新建 basic 让旧 [entari] 失效。
    assert "[runtime]\nlog_level = " in text and "[basic" not in text
    assert TenkoConfig.load(market.path).render.quality == 90


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


@pytest.mark.asyncio
async def test_plugin_detail_drops_unsafe_links_and_serves_icons(
    market, monkeypatch
) -> None:
    client = market.client
    native = market.service.plugin_runtime.discover()[0].native_plugin
    native.metadata.urls = {
        "Issues": "https://e.x/issues",
        "evil": "javascript:alert(1)",
    }
    monkeypatch.setattr("tenko.webui.plugins.plugin_command_help", lambda _p: [])
    await client.get("/webui/api/market")  # 刷新目录缓存

    detail = (await client.get("/webui/api/plugins/example/detail")).json()["data"]
    assert [link["url"] for link in detail["links"]] == ["https://e.x/issues"]
    assert detail["has_config"] is True and detail["name"] == "示例"
    assert (await client.get("/webui/api/plugins/nope/detail")).status_code == 404

    # 目录图标还没下载：回退到内置的单色图标。
    assert detail["icon"]["glyph"] is True
    icon = await client.get(detail["icon"]["url"])
    assert icon.headers["content-type"].startswith("image/svg+xml")


class FakeUpdater:
    enabled = True
    current_version = parse_version("1.0.0")
    channel = UpdateChannel.STABLE
    policy = UpdatePolicy.CHECK_ONLY
    check_interval_hours = 6

    def __init__(self, tmp_path, reasons, api_version=None) -> None:
        self.api_version = api_version
        self.layout = SimpleNamespace(
            audit_file=tmp_path / "audit.jsonl", read_previous=lambda: None
        )
        self.reasons = dict(reasons)
        self.calls = []

    async def check(self):
        release = Release(parse_version("1.1.0"), "v1.1.0", notes="修复")
        return CheckResult(self.current_version, self.channel, "memory", "ok", release)

    async def prepare(self, release):
        self.calls.append("prepare")
        if self.reasons:
            raise PluginCompatibilityError(
                release.version, self.reasons, self.api_version
            )

    async def request_install(self):
        self.calls.append("install")

    def spawn_restart_watcher(self):
        self.calls.append("arm")
        return True

    def request_graceful_shutdown(self):
        self.calls.append("exit")
        return True


async def wait_upgrade(client, *states) -> dict:
    for _ in range(100):
        job = (await client.get("/webui/api/manage/upgrade")).json()["data"]["job"]
        if job["state"] in states:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(job)


@pytest.mark.asyncio
async def test_upgrade_waits_for_a_choice_per_incompatible_plugin(
    market, tmp_path
) -> None:
    client, plugins = market.client, market.service.market
    reasons = {"example": "需要 tenko-api<1", "old": "需要 tenko<1.1"}
    updater = FakeUpdater(tmp_path, reasons, api_version="99.0.0")
    plugins._updater = updater
    suspended = []

    async def suspend(choices, *, reasons, actor):
        suspended.append(choices)
        updater.reasons.clear()

    plugins._manager.suspend = suspend
    url = "/webui/api/manage/upgrade"
    body = {"action": "upgrade", "confirm": True}

    assert (await client.post(url, json=body)).status_code == 200
    job = await wait_upgrade(client, "failed")
    # 目录里能装在新版本（tenko-api 99）上的只有 0.3.0；未收录的插件只能停用。
    assert job["incompatible"] == reasons
    assert job["options"] == {"example": ["0.3.0"], "old": []}
    assert suspended == [] and updater.calls == ["prepare"]

    # 还有插件没有选择时不继续。
    await client.post(url, json={**body, "plugins": {"old": "suspend"}})
    await wait_upgrade(client, "failed")
    assert suspended == []

    choice = {"example": "0.3.0", "old": "suspend"}
    await client.post(url, json={**body, "plugins": choice})
    job = await wait_upgrade(client, "restarting")
    (chosen,) = suspended
    assert chosen["old"] is None
    assert chosen["example"].requirement == "tenko-plugin-example==0.3.0"
    assert job["target"] == "v1.1.0"
    # 重启前不能再开插件任务或第二次升级。
    assert (await client.post(url, json=body)).status_code == 409
    task = await client.post(
        "/webui/api/manage/plugin-tasks",
        json={"action": "sync", "confirm": True},
    )
    assert task.status_code == 409
    await asyncio.sleep(1.1)
    assert updater.calls[-1] == "exit"


@pytest.mark.asyncio
async def test_upgrade_history_lists_final_results_newest_first(
    market, tmp_path
) -> None:
    updater = FakeUpdater(tmp_path, {})
    market.service.market._updater = updater
    records = [
        {"action": "install", "result": "started", "target_version": "1.1.0"},
        {
            "action": "install",
            "result": "success",
            "current_version": "1.0.0",
            "target_version": "1.1.0",
        },
        "{broken",
        {
            "action": "startup",
            "result": "failed",
            "target_version": "1.2.0",
            "reason": "\x1b[31mImportError\x1b[0m",
        },
        {
            "action": "rollback",
            "result": "success",
            "automatic": True,
            "target_version": "1.1.0",
        },
    ]
    updater.layout.audit_file.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in records)
    )

    data = (await market.client.get("/webui/api/manage/upgrade")).json()["data"]

    assert [(e["action"], e["result"]) for e in data["history"]] == [
        ("rollback", "success"),
        ("startup", "failed"),
        ("install", "success"),
    ]
    assert data["history"][0]["automatic"] is True
    assert data["history"][1]["error"] == "ImportError"
