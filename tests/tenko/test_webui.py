from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from satori.server import Server

from tenko.config import WebUIConfig
from tenko.events import MessageMetrics
from tenko.host.accounts import AccountRegistry
from tenko.host.features import FeatureService
from tenko.webui import WebUIService


async def _get(
    app,
    path: str,
    *,
    client_host: str = "127.0.0.1",
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(client_host, 12345))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.get(path)


async def _login(client: httpx.AsyncClient) -> None:
    """通过 reset_password 建立账号并登录，cookie 保存在 client 中。"""

    from tenko.webui.auth import reset_password

    username, password, _revoked = await reset_password()
    response = await client.post(
        "/webui/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text


@asynccontextmanager
async def _session_client(app, client_host: str = "127.0.0.1"):
    transport = httpx.ASGITransport(app=app, client=(client_host, 12345))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        await _login(client)
        yield client


def _service(
    config: WebUIConfig,
    *,
    accounts: AccountRegistry | None = None,
    metrics: MessageMetrics | None = None,
    feature_service: FeatureService | None = None,
    feature_repository=None,
    group_repository=None,
    plugin_runtime=None,
) -> tuple[Server, WebUIService]:
    server = Server(host="127.0.0.1", port=0)
    service = WebUIService(
        server,
        config,
        accounts=accounts if accounts is not None else AccountRegistry(),
        metrics=metrics if metrics is not None else MessageMetrics(),
        feature_service=(
            feature_service if feature_service is not None else FeatureService()
        ),
        feature_repository=feature_repository,
        group_repository=group_repository,
        plugin_runtime=plugin_runtime,
    )
    return server, service


@pytest.mark.asyncio
async def test_webui_requires_session_cookie_and_allowed_ip(tenko_database):
    server, service = _service(WebUIConfig(enabled=True))

    assert service.required == {"satori-python.server"}

    missing = await _get(server.app, "/webui/api/accounts")
    page = await _get(server.app, "/webui")
    remote_page = await _get(server.app, "/webui", client_host="192.0.2.10")
    state = await _get(server.app, "/webui/api/auth/state")
    async with _session_client(server.app) as client:
        allowed = await client.get("/webui/api/accounts")
        removed_session_api = await client.get("/webui/api/session")
        query_token = await _get(server.app, "/webui/api/accounts?token=x")
    transport = httpx.ASGITransport(app=server.app, client=("192.0.2.10", 1))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", cookies=client.cookies
    ) as remote:
        remote_api = await remote.get("/webui/api/accounts")

    assert missing.status_code == 401
    assert page.status_code == 200
    assert remote_page.status_code == 401
    assert state.json()["data"] == {
        "authenticated": False,
        "username": None,
        "password_set": False,
        "expires_at": None,
    }
    assert removed_session_api.status_code == 404
    assert allowed.status_code == 200
    assert query_token.status_code == 401
    assert remote_api.status_code == 401
    assert "Tenko WebUI" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert allowed.json() == {"ok": True, "data": {"accounts": []}}
    assert missing.json() == {
        "ok": False,
        "error": {"code": "unauthorized", "message": "Unauthorized"},
    }


@pytest.fixture
def static_site(tmp_path, monkeypatch):
    """隔离的前端构建产物：一个入口页、一个资源和一个位于 assets 之外的文件。"""

    from tenko.webui import service

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>Tenko WebUI</title>")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log('tenko')")
    (tmp_path / "secret.txt").write_text("outside-assets")
    monkeypatch.setattr(service, "_STATIC_INDEX", tmp_path / "index.html")
    monkeypatch.setattr(service, "_STATIC_ASSETS", tmp_path / "assets")
    return tmp_path


@pytest.mark.asyncio
async def test_webui_serves_index_and_hashed_assets_without_session(static_site):
    server, _service_instance = _service(WebUIConfig(enabled=True))

    index = await _get(server.app, "/webui")
    slash = await _get(server.app, "/webui/")
    asset = await _get(server.app, "/webui/assets/app-abc123.js")
    missing = await _get(server.app, "/webui/assets/missing.js")

    assert index.status_code == slash.status_code == 200
    assert slash.text == index.text
    assert index.headers["cache-control"] == "no-store"
    assert asset.status_code == 200
    assert asset.text == "console.log('tenko')"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_webui_assets_respect_enabled_and_allowed_ips(static_site):
    server, _service_instance = _service(WebUIConfig(enabled=True))
    remote = await _get(
        server.app, "/webui/assets/app-abc123.js", client_host="192.0.2.10"
    )
    disabled_server, _disabled = _service(WebUIConfig())
    disabled = await _get(disabled_server.app, "/webui/assets/app-abc123.js")

    assert remote.status_code == 401
    assert "console.log" not in remote.text
    assert disabled.status_code == 404
    assert "console.log" not in disabled.text


@pytest.mark.asyncio
async def test_webui_assets_refuse_path_traversal(static_site):
    server, _service_instance = _service(WebUIConfig(enabled=True))

    # httpx 会先规范化 `..`，这里用百分号编码把穿越路径原样交给服务端。
    responses = [
        await _get(server.app, "/webui/assets/%2e%2e/secret.txt"),
        await _get(server.app, "/webui/assets/..%2fsecret.txt"),
        await _get(server.app, "/webui/assets/%2e%2e/index.html"),
    ]

    for response in responses:
        assert response.status_code == 404
        assert "outside-assets" not in response.text
        assert "Tenko WebUI" not in response.text


@pytest.mark.asyncio
async def test_webui_disabled_returns_not_found_for_root_and_api():
    server, _service_instance = _service(WebUIConfig())

    root = await _get(server.app, "/webui")
    api = await _get(server.app, "/webui/api/overview")

    assert root.status_code == 404
    assert api.status_code == 404
    assert root.json() == {
        "ok": False,
        "error": {"code": "not_found", "message": "Not Found"},
    }
    assert api.json() == root.json()


@pytest.mark.asyncio
async def test_webui_read_only_apis_expose_safe_account_and_feature_data(
    tenko_database,
):
    accounts = AccountRegistry()
    first = SimpleNamespace(self_id="10001", platform="onebot")
    second = SimpleNamespace(self_id="10002", platform="satori")
    accounts.register(first, available=True, groups=("40001", "40002"))
    accounts.register(second, available=False, groups=("40002",))

    class FeatureRepository:
        async def list_states(self):
            return (
                SimpleNamespace(
                    plugin_name="demo",
                    group_id="40001",
                    enabled=False,
                    maintenance=False,
                ),
                SimpleNamespace(
                    plugin_name="global",
                    group_id=None,
                    enabled=False,
                    maintenance=False,
                ),
                SimpleNamespace(
                    plugin_name="maintenance",
                    group_id=None,
                    enabled=None,
                    maintenance=True,
                ),
            )

    plugin_info = SimpleNamespace(name="demo", display_name="演示插件", lookup_names=())
    plugin_runtime = SimpleNamespace(discover=lambda: (plugin_info,))
    server, _service_instance = _service(
        WebUIConfig(enabled=True),
        accounts=accounts,
        feature_service=FeatureService(default_enabled=True),
        feature_repository=FeatureRepository(),
        plugin_runtime=plugin_runtime,
    )

    async with _session_client(server.app) as client:
        account_response = await client.get("/webui/api/accounts")
        feature_response = await client.get("/webui/api/features")

    assert account_response.status_code == 200
    assert account_response.json() == {
        "ok": True,
        "data": {
            "accounts": [
                {
                    "id": "10001",
                    "platform": "onebot",
                    "online": True,
                    "group_count": 2,
                    "response_strategies": [
                        {"group_id": "40001", "strategy": "random"},
                        {"group_id": "40002", "strategy": "random"},
                    ],
                },
                {
                    "id": "10002",
                    "platform": "satori",
                    "online": False,
                    "group_count": 1,
                    "response_strategies": [
                        {"group_id": "40002", "strategy": "random"},
                    ],
                },
            ]
        },
    }
    features = feature_response.json()["data"]["features"]
    assert features == [
        {
            "plugin": "demo",
            "name": "演示插件",
            "scope": "group",
            "source": "builtin",
            "protected": False,
            "loaded": False,
            "enabled": False,
            "maintenance": False,
            "global_enabled": None,
            "groups": {"40001": False, "40002": True},
        },
        {
            "plugin": "global",
            "name": "global",
            "scope": "group",
            "source": "builtin",
            "protected": False,
            "loaded": False,
            "enabled": False,
            "maintenance": False,
            "global_enabled": False,
            "groups": {"40001": False, "40002": False},
        },
        {
            "plugin": "maintenance",
            "name": "maintenance",
            "scope": "group",
            "source": "builtin",
            "protected": False,
            "loaded": False,
            "enabled": False,
            "maintenance": True,
            "global_enabled": None,
            "groups": {"40001": False, "40002": False},
        },
    ]
    assert "access_token" not in account_response.text + feature_response.text


@pytest.mark.asyncio
async def test_webui_features_expose_group_names_and_survive_database_errors(
    tenko_database,
):
    def row(group_id, name):
        return SimpleNamespace(group_id=group_id, group_name=name)

    class Groups:
        async def list_all(self):
            return (row(40001, "测试群"), row(40002, "40002"), row(40003, ""))

    class BrokenGroups:
        async def list_all(self):
            raise RuntimeError("database is locked")

    async def fetch(group_repository):
        server, _service_instance = _service(
            WebUIConfig(enabled=True), group_repository=group_repository
        )
        async with _session_client(server.app) as client:
            response = await client.get("/webui/api/features")
        assert response.status_code == 200
        return response.json()["data"]

    assert (await fetch(Groups()))["group_names"] == {"40001": "测试群"}
    assert (await fetch(BrokenGroups()))["group_names"] == {}
    assert (await fetch(None))["group_names"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["status"], indirect=True)
async def test_webui_overview_uses_status_data_metrics_and_counts(
    loaded_plugin, monkeypatch, tenko_database
):
    status = loaded_plugin
    accounts = AccountRegistry()
    accounts.register(
        SimpleNamespace(self_id="10001", platform="onebot"),
        available=True,
        groups=("40001", "40002"),
    )
    accounts.register(
        SimpleNamespace(self_id="10002", platform="onebot"),
        available=False,
        groups=("40002",),
    )
    metrics = MessageMetrics()
    metrics._received_count = 17
    metrics._sent_count = 5
    process = status.ProcessInfo(
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        uptime_seconds=42.5,
        rss=None,
    )
    resources = status.SystemResources(0, 0, 1, 0, 0, 1, 0, 0, 0)
    version_details = ("版本信息：v9.8.7",)
    monkeypatch.setattr(status, "collect_process_info", lambda: process)
    monkeypatch.setattr(status, "collect_system_resources", lambda: resources)
    monkeypatch.setattr(status, "get_version_details", lambda: version_details)
    plugin_runtime = SimpleNamespace(discover=lambda: (object(),))
    server, _service_instance = _service(
        WebUIConfig(enabled=True),
        accounts=accounts,
        metrics=metrics,
        plugin_runtime=plugin_runtime,
    )

    async with _session_client(server.app) as client:
        response = await client.get("/webui/api/overview")
    expected_status = status.build_status_data(
        SimpleNamespace(chat_type="private", channel_id=""),
        1,
        registry=accounts,
        metrics=metrics,
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {
            "version": "9.8.7",
            "uptime_seconds": expected_status["process"]["uptime_seconds"],
            "online_accounts": 1,
            "active_groups": expected_status["active_groups"],
            "messages": {
                "received_count": expected_status["metrics"]["received_count"],
                "sent_count": expected_status["metrics"]["sent_count"],
            },
        },
    }


@pytest.mark.asyncio
async def test_management_auth_persistence_and_failure_boundaries(
    tenko_database, tmp_path, monkeypatch
):
    from tenko.config import TenkoConfig
    from tenko.connection import OneBotConnection
    from tenko.db.repositories import ManagementRepository, AccountStateRepository
    from tenko.host.account_management import AccountManagement
    from tenko.host.actions import ActionService

    config = TenkoConfig.from_mapping(
        {
            "webui": {"enabled": True},
            "onebot": {"access_token": "onebot-secret"},
        }
    )
    accounts = AccountRegistry(AccountStateRepository())
    await accounts.initialize()
    management = AccountManagement(accounts, ActionService(registry=accounts))
    management.repository = ManagementRepository()
    await management.initialize({})
    first = SimpleNamespace(self_id="10001", platform="onebot")
    second = SimpleNamespace(self_id="10001", platform="telegram")
    accounts.register(first, available=True, groups=("40001",))
    accounts.register(second, available=True)
    # 内存 SQLite 共用连接，先完成路由任务，避免测试事务相互回滚。
    await accounts.flush_persistence()
    connection = OneBotConnection(config.onebot)
    service = WebUIService(
        connection.server,
        config.webui,
        accounts=accounts,
        management=management,
        connection=connection,
        tenko_config=config,
        feature_service=FeatureService(),
    )
    transport = httpx.ASGITransport(
        app=connection.server.app, client=("127.0.0.1", 12345)
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        path = "/webui/api/manage/accounts/onebot/10001/disable"
        anonymous = await client.post(path, json={"confirm": True})
        assert anonymous.status_code == 401
        await _login(client)
        for headers, code in [
            ({"Origin": "https://evil.invalid"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        ]:
            response = await client.post(path, headers=headers, json={"confirm": True})
            assert response.status_code == code
        assert (await client.post(path, json={"confirm": 1})).status_code == 400
        assert (
            await client.post(
                path, content="x" * 17000, headers={"Content-Type": "application/json"}
            )
        ).status_code == 400
        original = management.repository.save_account

        async def fail(_value):
            raise OSError("secret must not appear in API errors")

        monkeypatch.setattr(management.repository, "save_account", fail)
        failed = await client.post(path, json={"confirm": True})
        assert failed.status_code == 503
        assert "secret must" not in failed.text
        assert accounts.is_available(first)
        monkeypatch.setattr(management.repository, "save_account", original)
        assert (await client.post(path, json={"confirm": True})).status_code == 200
        assert not accounts.is_available(first)
        assert accounts.is_available(second)
        accounts.register(first, available=True)
        assert not accounts.is_available(first)
        assert not management.can_connect("onebot", "10001")
        assert accounts.get(("telegram", "10001")) is second
        rows = (await client.get("/webui/api/manage/accounts")).json()["data"][
            "accounts"
        ]
        assert len(rows) == 2
        assert all("access_token" not in row for row in rows)
        pairing = await client.get("/webui/api/manage/pairing")
        assert "onebot-secret" not in pairing.text
        reveal = await client.post(
            "/webui/api/manage/pairing", json={"action": "reveal"}
        )
        assert reveal.json()["data"]["access_token"] == "onebot-secret"
        assert reveal.headers["cache-control"] == "no-store"
        assert (
            await client.post(
                "/webui/api/manage/settings", json={"default_enabled": False}
            )
        ).status_code == 200
        assert not service.feature_service.default_enabled

    restored = AccountManagement(AccountRegistry(), ActionService())
    restored.repository = ManagementRepository()
    await restored.initialize({})
    restored.accounts.register(first, available=True)
    assert not restored.accounts.is_available(first)
    feature = FeatureService(default_enabled=True)
    await restored.restore_settings(feature, None)
    assert not feature.default_enabled
    await accounts.flush_persistence()


@pytest.mark.asyncio
async def test_pairing_rotation_is_atomic_and_detects_external_config_changes(
    tmp_path, monkeypatch, tenko_database
):
    import tomllib
    from tenko.config import TenkoConfig
    from tenko.connection import OneBotConnection

    path = tmp_path / "tenko.toml"
    original = '[onebot]\naccess_token = "old-secret"\n[webui]\nenabled = true\n'
    path.write_text(original)
    config = TenkoConfig.load(path)
    connection = OneBotConnection(config.onebot)
    service = WebUIService(
        connection.server, config.webui, connection=connection, tenko_config=config
    )
    transport = httpx.ASGITransport(
        app=connection.server.app, client=("127.0.0.1", 12345)
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        await _login(client)
        url = "/webui/api/manage/pairing"
        body = {"action": "rotate", "confirm": True}
        path.write_text(original + "# external change\n")
        assert (await client.post(url, json=body)).status_code == 409
        assert connection.adapter.access_token == "old-secret"
        path.write_text(original)
        import tenko.webui.service as webui_module

        replace = webui_module.os.replace

        def fail_replace(*args):
            raise OSError("disk failure")

        monkeypatch.setattr(webui_module.os, "replace", fail_replace)
        assert (await client.post(url, json=body)).status_code == 503
        assert path.read_text() == original
        assert connection.adapter.access_token == "old-secret"
        monkeypatch.setattr(webui_module.os, "replace", replace)
        response = await client.post(url, json=body)
        assert response.status_code == 200, response.text
        token = response.json()["data"]["access_token"]
        assert token != "old-secret" and len(token) >= 32
        assert tomllib.loads(path.read_text())["onebot"]["access_token"] == token
        assert connection.adapter.access_token == token
        assert service.logs.redact(f"old-secret {token}") == "[REDACTED] [REDACTED]"
        assert not list(tmp_path.glob(".tenko-config-*"))


def test_management_logs_redact_credentials_and_restrict_history(tmp_path):
    import gzip
    from tenko.webui.logs import LogReader

    logs = LogReader(lambda: ("private-secret",))
    message = SimpleNamespace(
        record={
            "time": datetime.now(UTC),
            "level": SimpleNamespace(name="INFO"),
            "message": 'private-secret Authorization: Bearer abc token="xyz"',
        }
    )
    logs.sink(message)
    result = logs.live(0, "", "")
    assert result["cursor"] == 1
    assert (
        "private-secret" not in str(result)
        and "abc" not in str(result)
        and "xyz" not in str(result)
    )
    assert not logs.live(1, "", "")["records"]
    (tmp_path / "latest.log").write_text("old\nprivate-secret newest\n")
    (tmp_path / "symlink.log").symlink_to(tmp_path / "latest.log")
    with gzip.open(tmp_path / "rotated.log.gz", "wt") as file:
        file.write('token="xyz"\n')
    assert "symlink.log" not in logs.files(tmp_path)
    assert logs.history(tmp_path, "latest.log", "newest")["lines"] == [
        "[REDACTED] newest"
    ]
    assert "xyz" not in str(logs.history(tmp_path, "rotated.log.gz", ""))
    with pytest.raises(ValueError):
        logs.history(tmp_path, "../latest.log", "")


@pytest.mark.asyncio
async def test_plugin_and_group_switches_use_host_state_and_protect_control_plane(
    repositories, monkeypatch
):
    from unittest.mock import AsyncMock
    from tenko.config import TenkoConfig
    from tenko.connection import OneBotConnection
    from tenko.db.repositories import ManagementRepository
    from tenko.host.account_management import AccountManagement
    from tenko.host.actions import ActionService
    from tenko.host.plugins import PluginRuntime

    config = TenkoConfig.from_mapping({"webui": {"enabled": True}})
    accounts = AccountRegistry()
    accounts.register(
        SimpleNamespace(self_id="10001", platform="onebot"), groups=("40001",)
    )
    features = FeatureService(repositories["feature"])
    await features.initialize()
    management = AccountManagement(accounts, ActionService(registry=accounts))
    management.repository = ManagementRepository()
    await management.initialize({})
    runtime = PluginRuntime()
    state = {"status": True, "updater": True}
    monkeypatch.setattr(runtime, "is_protected", lambda info: info.name == "updater")
    monkeypatch.setattr(runtime, "is_enabled", lambda info: state[info.name])

    async def set_enabled(info, enabled):
        state[info.name] = enabled
        return True

    monkeypatch.setattr(runtime, "set_enabled", set_enabled)
    connection = OneBotConnection(config.onebot)
    service = WebUIService(
        connection.server,
        config.webui,
        accounts=accounts,
        feature_service=features,
        management=management,
        connection=connection,
        plugin_runtime=runtime,
        tenko_config=config,
    )
    transport = httpx.ASGITransport(
        app=connection.server.app, client=("127.0.0.1", 12345)
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        await _login(client)
        plugin = "/webui/api/manage/plugins/"
        feature = "/webui/api/manage/features/"
        assert (
            await client.post(
                plugin + "updater", json={"enabled": False, "confirm": True}
            )
        ).status_code == 403
        assert (
            await client.post(
                plugin + "missing", json={"enabled": False, "confirm": True}
            )
        ).status_code == 404
        assert (
            await client.post(
                plugin + "status", json={"enabled": False, "confirm": True}
            )
        ).status_code == 200
        assert not state["status"]
        assert await management.repository.setting("disabled-plugins") == ["status"]
        original = management.repository.save_setting
        monkeypatch.setattr(
            management.repository,
            "save_setting",
            AsyncMock(side_effect=OSError("disk full")),
        )
        assert (
            await client.post(
                plugin + "status", json={"enabled": True, "confirm": True}
            )
        ).status_code == 503
        assert not state["status"]
        monkeypatch.setattr(management.repository, "save_setting", original)
        assert (
            await client.post(
                feature + "status", json={"group_id": "unknown", "enabled": False}
            )
        ).status_code == 400
        assert (
            await client.post(
                feature + "status", json={"group_id": "40001", "enabled": False}
            )
        ).status_code == 200
        restored = FeatureService(repositories["feature"])
        await restored.initialize()
        assert not restored.is_enabled("status", "40001")
        assert (
            await client.post(feature + "status", json={"maintenance": True})
        ).status_code == 200
        assert (
            await client.post(
                feature + "status", json={"group_id": "40001", "enabled": True}
            )
        ).status_code == 409
        assert (
            await client.post(feature + "status", json={"maintenance": False})
        ).status_code == 200
        assert (
            await client.post(
                feature + "status", json={"group_id": None, "enabled": True}
            )
        ).status_code == 200
        assert (
            await client.post(
                feature + "status", json={"group_id": None, "enabled": None}
            )
        ).status_code == 200
        assert not features.is_enabled("status", "40001")
        assert service is not None


@pytest.mark.asyncio
async def test_capability_import_is_once_even_after_forgetting_account(tenko_database):
    from tenko.db.repositories import ManagementRepository
    from tenko.host.account_management import AccountManagement
    from tenko.host.actions import ActionService

    preferences = AccountManagement(AccountRegistry(), ActionService())
    preferences.repository = ManagementRepository()
    old = {"10001": {"set_group_ban": False}}
    await preferences.initialize(old)
    assert preferences.preferences[("onebot", "10001")]["capabilities"] == {
        "member_mute": False
    }
    await preferences.forget(("onebot", "10001"))
    await preferences.initialize(old)
    assert not preferences.preferences


def test_log_history_scans_tail_of_large_files(tmp_path):
    import gzip
    from tenko.webui.logs import LogReader

    logs = LogReader(lambda: ())
    body = ("x" * 99 + "\n") * (90 * 1024)  # 约 9 MiB，超过扫描上限
    (tmp_path / "big.log").write_text(body + "last-line\n")
    with gzip.open(tmp_path / "big.log.gz", "wt") as file:
        file.write(body + "last-line\n")
    for name in ("big.log", "big.log.gz"):
        result = logs.history(tmp_path, name, "last-line")
        assert result["lines"] == ["last-line"] and result["truncated"]
        assert logs.history(tmp_path, name, "")["lines"][0] == "x" * 99


@pytest.mark.asyncio
async def test_external_plugin_toggle_uses_state_file_and_reports_restart(
    repositories, monkeypatch, tmp_path
):
    from importlib.metadata import EntryPoint

    from tenko.config import TenkoConfig
    from tenko.connection import OneBotConnection
    from tenko.db.repositories import ManagementRepository
    from tenko.host import plugins as plugin_host
    from tenko.host.account_management import AccountManagement
    from tenko.host.actions import ActionService
    from tenko.host.plugins import PluginRuntime

    monkeypatch.setattr(
        plugin_host,
        "entry_points",
        lambda *, group: (
            [EntryPoint("ext_demo", "tenko_t_ext_demo", group)]
            if group == "tenko.plugins"
            else []
        ),
    )
    config = TenkoConfig.from_mapping({"webui": {"enabled": True}})
    accounts = AccountRegistry()
    features = FeatureService(repositories["feature"])
    await features.initialize()
    management = AccountManagement(accounts, ActionService(registry=accounts))
    management.repository = ManagementRepository()
    await management.initialize({})
    state_file = tmp_path / "plugins" / "state.json"
    runtime = PluginRuntime(state_file=state_file)
    connection = OneBotConnection(config.onebot)
    WebUIService(
        connection.server,
        config.webui,
        accounts=accounts,
        feature_service=features,
        management=management,
        connection=connection,
        plugin_runtime=runtime,
        tenko_config=config,
    )
    transport = httpx.ASGITransport(
        app=connection.server.app, client=("127.0.0.1", 12345)
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        await _login(client)
        listed = (await client.get("/webui/api/features")).json()["data"]["features"]
        row = next(item for item in listed if item["plugin"] == "ext_demo")
        assert (row["source"], row["loaded"], row["enabled"]) == (
            "external",
            False,
            False,
        )
        assert {item["source"] for item in listed if item["plugin"] != "ext_demo"} == {
            "builtin"
        }

        response = await client.post(
            "/webui/api/manage/plugins/ext_demo",
            json={"enabled": True, "confirm": True},
        )
        assert response.status_code == 200
        assert response.json()["data"] == {
            "plugin": "ext_demo",
            "enabled": True,
            "restart_required": True,
        }
        assert json.loads(state_file.read_text(encoding="utf-8")) == {
            "enabled": ["ext_demo"]
        }
        # 内置插件的停用列表不会被第三方插件污染。
        assert await management.repository.setting("disabled-plugins") is None

        response = await client.post(
            "/webui/api/manage/plugins/ext_demo",
            json={"enabled": False, "confirm": True},
        )
        assert response.json()["data"]["restart_required"] is False
        assert json.loads(state_file.read_text(encoding="utf-8")) == {"enabled": []}
