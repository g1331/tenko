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
from arclet.entari.config import model_field
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

    async def update(self, plugin_id=None, *, sources, actor):
        self.sources.append(sources)
        return [
            PluginChange("update", "example", "tenko-plugin-example", "0.3.0", "0.1.0")
        ]

    async def go_live(self, runtime, changes):
        return {}


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
    words: list[str] = model_field(default_factory=list)
    pairs: dict[str, int] = model_field(default_factory=dict)


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
async def test_market_switches_an_installed_plugin_to_a_chosen_version(market) -> None:
    client, manager = market.client, market.service.market._manager
    url = "/webui/api/manage/plugin-tasks"
    body = {"action": "update", "plugin_id": "example", "confirm": True}

    started = await client.post(url, json={**body, "version": "0.1.0"})
    done = await wait_task(client, started.json()["data"]["id"])
    assert done["state"] == "succeeded"
    source = manager.sources[0]["example"]
    assert source.requirement.endswith("==0.1.0") and source.pins

    # 撤回的版本不能装，错误原因来自目录
    refused = await client.post(url, json={**body, "version": "0.2.0"})
    assert refused.status_code == 400 and "撤回" in refused.json()["error"]["message"]


@pytest.mark.asyncio
async def test_plugin_config_form_validates_and_writes_toml(market) -> None:
    client, url = market.client, "/webui/api/manage/plugin-config/example"
    form = (await client.get(url)).json()["data"]
    assert form["values"] == {"greeting": "嗨", "limit": 3, "words": [], "pairs": {}}
    # 字符串列表一行一个能在面板改；别的复合类型只读
    assert form["editable"] == ["greeting", "limit", "words"]

    for bad in (
        {"values": {"limit": "many"}, "confirm": True},
        {"values": {"unknown": 1}, "confirm": True},
        {"values": {"limit": 5}},
        {"values": {"words": "词"}, "confirm": True},
        {"values": {"words": ["词", 1]}, "confirm": True},
        {"values": {"pairs": {"a": 1}}, "confirm": True},
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
async def test_plugin_config_reset_removes_keys_so_defaults_apply(market) -> None:
    client, url = market.client, "/webui/api/manage/plugin-config/example"
    # 不是列表、未知项、同时又在 values 里
    for bad in ({"unknown": 1}, ["unknown"], ["limit"]):
        body = {"values": {"limit": 4}, "reset": bad, "confirm": True}
        assert (await client.post(url, json=body)).status_code == 400

    body = {"values": {}, "reset": ["greeting"], "confirm": True}
    saved = (await client.post(url, json=body)).json()["data"]

    assert saved["values"] == {"greeting": "你好", "limit": 3, "words": [], "pairs": {}}
    market.service.plugin_runtime.apply_config.assert_awaited_once_with("example", {})
    assert "greeting" not in market.path.read_text()
    assert "# 问候语" in market.path.read_text()


@pytest.mark.asyncio
async def test_plugin_config_edits_string_lists_and_resets_them(market) -> None:
    client, url = market.client, "/webui/api/manage/plugin-config/example"
    form = (await client.get(url)).json()["data"]
    # default_factory 的默认值也给前端，才能“恢复默认”
    assert form["schema"]["properties"]["words"]["default"] == []

    body = {"values": {"words": ["蓝银", "lanyinzhenshen"]}, "confirm": True}
    saved = (await client.post(url, json=body)).json()["data"]
    assert saved["values"]["words"] == ["蓝银", "lanyinzhenshen"]
    apply = market.service.plugin_runtime.apply_config
    apply.assert_awaited_with(
        "example", {"greeting": "嗨", "words": ["蓝银", "lanyinzhenshen"]}
    )
    assert 'words = ["蓝银", "lanyinzhenshen"]' in market.path.read_text()

    body = {"values": {}, "reset": ["words"], "confirm": True}
    saved = (await client.post(url, json=body)).json()["data"]
    assert saved["values"]["words"] == []
    assert "words" not in market.path.read_text()


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
async def test_core_config_save_applies_live(market) -> None:
    applied = []
    market.service._apply_config = lambda config: applied.append(config) or []
    saved = await market.client.post(
        "/webui/api/manage/config", json={"values": {"render.quality": 70}}
    )
    data = saved.json()["data"]
    assert data["restart_required"] is False and data["restart_fields"] == []
    assert [config.render.quality for config in applied] == [70]


@pytest.mark.asyncio
async def test_core_config_reset_removes_key_so_default_applies(market) -> None:
    applied = []
    market.service._apply_config = lambda config: applied.append(config) or []
    url = "/webui/api/manage/config"
    await market.client.post(url, json={"values": {"render.quality": 70}})
    saved = await market.client.post(url, json={"reset": ["render.quality"]})
    fields = {f["key"]: f for f in saved.json()["data"]["fields"]}
    assert fields["render.quality"]["value"] == 85
    assert "quality" not in market.path.read_text()
    assert [config.render.quality for config in applied] == [70, 85]
    bad = await market.client.post(url, json={"reset": ["onebot.access_token"]})
    assert bad.status_code == 400


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


@pytest.mark.asyncio
async def test_upgrade_changelog_lists_skipped_releases_newest_first() -> None:
    from types import SimpleNamespace

    from tenko.host.updater import CheckResult, Release, UpdateChannel, parse_version
    from tenko.webui.upgrade import HostUpgrade

    source = SimpleNamespace(
        name="git_tag", repository="https://github.com/o/r.git", tag_prefix="v"
    )
    upgrade = HostUpgrade(SimpleNamespace(updater=SimpleNamespace(sources=[source])))
    seen = []

    async def get_json(url, *, headers=None):
        seen.append(url)
        return [
            {"tag_name": tag, "body": f"## {tag}", "prerelease": "rc" in tag}
            for tag in ("v1.0.0", "v1.1.0-rc1", "v1.1.0", "v1.2.0", "v1.3.0", "x")
        ]

    upgrade.http = SimpleNamespace(get_json=get_json)
    result = CheckResult(
        parse_version("1.0.0"),
        UpdateChannel.STABLE,
        "git_tag",
        "available",
        Release(version="1.2.0", tag="v1.2.0"),
    )
    notes, error = await upgrade._changelog(result)

    assert seen == ["https://api.github.com/repos/o/r/releases?per_page=100"]
    assert error is None
    # 正式版通道跳过预发布；不含当前版本和候选之后的版本。
    assert [n["tag"] for n in notes] == ["v1.2.0", "v1.1.0"]
    assert notes[0]["url"] == "https://github.com/o/r/releases/tag/v1.2.0"


@pytest.mark.asyncio
async def test_upgrade_notes_returns_the_running_version_once() -> None:
    import json
    from types import SimpleNamespace

    from tenko.host.updater import parse_version
    from tenko.webui.upgrade import HostUpgrade

    source = SimpleNamespace(
        name="git_tag", repository="https://github.com/o/r.git", tag_prefix="v"
    )
    updater = SimpleNamespace(
        sources=[source], current_version=parse_version("1.1.0rc1")
    )
    upgrade = HostUpgrade(SimpleNamespace(updater=updater))
    calls = []

    async def get_json(url, *, headers=None):
        calls.append(url)
        return [{"tag_name": t, "body": f"## {t}"} for t in ("v1.1.0", "v1.1.0-rc1")]

    upgrade.http = SimpleNamespace(get_json=get_json)
    first = json.loads((await upgrade.notes(None)).body)
    await upgrade.notes(None)

    assert first["data"]["note"]["body"] == "## v1.1.0-rc1"
    assert len(calls) == 1  # 版本不变就不再请求 GitHub
