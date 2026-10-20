"""WebUI 面板账号：密码哈希、会话、限流、指令与 CLI。"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from satori.server import Server

from test_perm_manager import make_private_session
from tenko.config import WebUIConfig
from tenko.host.guards import REQUIRES_ATTR
from tenko.host.perm import Permission
from tenko.webui import WebUIService
from tenko.webui.auth import (
    SESSION_COOKIE,
    LoginLimiter,
    hash_password,
    reset_password,
    verify_password,
)

PROTECTED = "/webui/api/accounts"


def _app() -> Server:
    server = Server(host="127.0.0.1", port=0)
    WebUIService(server, WebUIConfig(enabled=True))
    return server


def _client(server: Server, base_url: str = "http://testserver") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 12345))
    return httpx.AsyncClient(transport=transport, base_url=base_url)


async def _login(client: httpx.AsyncClient, password: str, username: str = "master"):
    return await client.post(
        "/webui/api/auth/login", json={"username": username, "password": password}
    )


def _repository():
    from tenko.db.repositories import webui_auth_repository

    return webui_auth_repository


def test_password_hash_roundtrip_and_malformed_hashes():
    stored = hash_password("correct horse")

    scheme, n, r, p, salt, digest = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert salt and digest
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong horse", stored)
    assert hash_password("correct horse") != stored  # 随机盐
    for broken in (
        "",
        "plain",
        "scrypt$1$2",
        "bcrypt$16384$8$1$AA==$AA==",
        "scrypt$x$8$1$AA==$AA==",
    ):
        assert not verify_password("correct horse", broken)


def test_login_limiter_blocks_after_five_failures_and_stays_bounded():
    limiter = LoginLimiter(max_failures=5, window=900, max_entries=4)
    for _ in range(4):
        limiter.fail("1.1.1.1")
    assert limiter.retry_after("1.1.1.1") == 0
    limiter.fail("1.1.1.1")
    assert 0 < limiter.retry_after("1.1.1.1") <= 901
    assert limiter.retry_after("2.2.2.2") == 0
    limiter.reset("1.1.1.1")
    assert limiter.retry_after("1.1.1.1") == 0
    for index in range(10):
        limiter.fail(f"10.0.0.{index}")
    assert len(limiter._failures) <= 4


@pytest.mark.asyncio
async def test_login_sets_http_only_cookie_and_unlocks_protected_api(tenko_database):
    _, password, _ = await reset_password()
    async with _client(_app()) as client:
        assert (await client.get(PROTECTED)).status_code == 401

        response = await _login(client, password)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["username"] == "master"
        assert data["expires_at"].endswith("Z")
        cookie = response.headers["set-cookie"].lower()
        assert f"{SESSION_COOKIE}=" in cookie
        for attribute in (
            "httponly",
            "samesite=strict",
            "path=/webui",
            "max-age=604800",
        ):
            assert attribute in cookie
        assert "secure" not in cookie

        assert (await client.get(PROTECTED)).status_code == 200
        state = (await client.get("/webui/api/auth/state")).json()["data"]
        assert state["authenticated"] is True and state["username"] == "master"
        assert state["password_set"] is True and state["expires_at"]
        # 数据库只保存令牌的哈希。
        token = client.cookies[SESSION_COOKIE]
        sessions = await _repository().list_sessions()
        assert len(sessions) == 1 and token not in sessions[0].token_hash

        # 没有 cookie 的新客户端仍被拒绝。
        async with _client(_app()) as other:
            assert (await other.get(PROTECTED)).status_code == 401


@pytest.mark.asyncio
async def test_cookie_is_secure_over_https(tenko_database):
    _, password, _ = await reset_password()
    async with _client(_app(), "https://testserver") as client:
        response = await _login(client, password)
    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_login_without_password_and_write_request_guards(tenko_database):
    async with _client(_app()) as client:
        missing = await _login(client, "whatever")
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "password_not_set"
        state = (await client.get("/webui/api/auth/state")).json()["data"]
        assert state["password_set"] is False

        _, password, _ = await reset_password()
        body = {"username": "master", "password": password}
        cross_origin = await client.post(
            "/webui/api/auth/login", json=body, headers={"Origin": "https://evil.test"}
        )
        not_json = await client.post(
            "/webui/api/auth/login", content="x", headers={"Content-Type": "text/plain"}
        )
        assert cross_origin.status_code == 403
        assert not_json.status_code == 415


@pytest.mark.asyncio
async def test_wrong_password_is_rejected_and_sixth_attempt_is_rate_limited(
    tenko_database,
):
    _, password, _ = await reset_password()
    async with _client(_app()) as client:
        for _ in range(5):
            response = await _login(client, "wrong-password")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "invalid_credentials"
        limited = await _login(client, password)
        assert limited.status_code == 429
        error = limited.json()["error"]
        assert error["code"] == "too_many_attempts"
        assert 0 < error["retry_after"] <= 901
        assert limited.headers["retry-after"] == str(error["retry_after"])
        assert not client.cookies


@pytest.mark.asyncio
async def test_success_resets_failure_counter_and_wrong_username_fails(tenko_database):
    _, password, _ = await reset_password()
    async with _client(_app()) as client:
        for _ in range(4):
            assert (await _login(client, "wrong-password")).status_code == 401
        assert (await _login(client, password)).status_code == 200
        for _ in range(4):
            assert (await _login(client, "wrong-password")).status_code == 401
        assert (await _login(client, password, "nobody")).status_code == 401


@pytest.mark.asyncio
async def test_expired_session_is_rejected_and_removed(tenko_database):
    _, password, _ = await reset_password()
    async with _client(_app()) as client:
        await _login(client, password)
        (record,) = await _repository().list_sessions()
        past = datetime.now(UTC) - timedelta(minutes=1)
        await _repository().touch_session(record.id, past, past)

        assert (await client.get(PROTECTED)).status_code == 401
        assert await _repository().list_sessions() == ()


@pytest.mark.asyncio
async def test_activity_slides_expiry_but_not_on_every_request(tenko_database):
    _, password, _ = await reset_password()
    async with _client(_app()) as client:
        await _login(client, password)
        (record,) = await _repository().list_sessions()

        await client.get(PROTECTED)
        (fresh,) = await _repository().list_sessions()
        assert fresh.last_seen_at == record.last_seen_at  # 60 秒内不写库

        stale = datetime.now(UTC) - timedelta(minutes=5)
        await _repository().touch_session(
            record.id, stale, datetime.now(UTC) + timedelta(days=1)
        )
        response = await client.get(PROTECTED)
        (renewed,) = await _repository().list_sessions()
        assert renewed.last_seen_at > stale
        assert renewed.expires_at > datetime.now(UTC) + timedelta(days=6)
        assert SESSION_COOKIE in response.headers["set-cookie"]  # 同步续期 cookie


@pytest.mark.asyncio
async def test_logout_revoke_and_revoke_others(tenko_database):
    _, password, _ = await reset_password()
    server = _app()
    async with _client(server) as first, _client(server) as second:
        await _login(first, password)
        await _login(second, password)
        listing = (await first.get("/webui/api/auth/sessions")).json()["data"]
        assert [item["current"] for item in listing["sessions"]].count(True) == 1
        assert set(listing["sessions"][0]) == {
            "id",
            "created_at",
            "last_seen_at",
            "expires_at",
            "ip",
            "user_agent",
            "current",
        }
        assert listing["sessions"][0]["ip"] == "127.0.0.1"
        other_id = next(
            item["id"] for item in listing["sessions"] if not item["current"]
        )

        missing = await first.post("/webui/api/auth/sessions/nope/revoke", json={})
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        assert (
            await first.post(f"/webui/api/auth/sessions/{other_id}/revoke", json={})
        ).json() == {"ok": True, "data": {}}
        assert (await second.get(PROTECTED)).status_code == 401

        await _login(second, password)
        revoked = await first.post("/webui/api/auth/sessions/revoke-others", json={})
        assert revoked.json() == {"ok": True, "data": {"revoked": 1}}
        assert (await second.get(PROTECTED)).status_code == 401
        assert (await first.get(PROTECTED)).status_code == 200

        current_id = (await _repository().list_sessions())[0].id
        revoke_self = await first.post(
            f"/webui/api/auth/sessions/{current_id}/revoke", json={}
        )
        assert "max-age=0" in revoke_self.headers["set-cookie"].lower()
        assert (await first.get(PROTECTED)).status_code == 401

        await _login(first, password)
        logout = await first.post("/webui/api/auth/logout", json={})
        assert logout.json() == {"ok": True, "data": {}}
        assert (await first.get(PROTECTED)).status_code == 401
        assert await _repository().list_sessions() == ()


@pytest.mark.asyncio
async def test_account_change_validates_and_revokes_other_sessions(tenko_database):
    _, password, _ = await reset_password()
    server = _app()
    async with _client(server) as first, _client(server) as second:
        await _login(first, password)
        await _login(second, password)
        url = "/webui/api/auth/account"

        wrong = await first.post(url, json={"current_password": "nope"})
        assert wrong.status_code == 401
        assert wrong.json()["error"]["code"] == "invalid_credentials"
        for body in (
            {"current_password": password, "username": "bad name"},
            {"current_password": password, "username": "x" * 33},
            {"current_password": password, "new_password": "short"},
            {"username": "boss"},
        ):
            invalid = await first.post(url, json=body)
            assert invalid.status_code == 400
            assert invalid.json()["error"]["code"] == "invalid_request"
        assert (await second.get(PROTECTED)).status_code == 200  # 仅改名不下线

        renamed = await first.post(
            url, json={"current_password": password, "username": "boss"}
        )
        assert renamed.json() == {"ok": True, "data": {"username": "boss"}}
        assert (await second.get(PROTECTED)).status_code == 200

        changed = await first.post(
            url, json={"current_password": password, "new_password": "new-password-1"}
        )
        assert changed.status_code == 200
        assert (await first.get(PROTECTED)).status_code == 200
        assert (await second.get(PROTECTED)).status_code == 401
        async with _client(server) as third:
            assert (await _login(third, password, "boss")).status_code == 401
            assert (await _login(third, "new-password-1", "boss")).status_code == 200


@pytest.mark.asyncio
async def test_account_change_counts_toward_rate_limit(tenko_database):
    _, password, _ = await reset_password()
    async with _client(_app()) as client:
        await _login(client, password)
        for _ in range(5):
            response = await client.post(
                "/webui/api/auth/account", json={"current_password": "nope"}
            )
            assert response.status_code == 401
        limited = await client.post(
            "/webui/api/auth/account", json={"current_password": password}
        )
        assert limited.status_code == 429


# ------------------------------------------------------------- QQ 指令


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["webui_account"], indirect=True)
async def test_reset_command_takes_effect_for_running_panel(
    loaded_plugin, tenko_database
):
    loaded_plugin.configure_webui(
        WebUIConfig(enabled=True, public_url="http://203.0.113.10:8081")
    )
    async with _client(_app()) as client:
        message = await loaded_plugin.show_account.callable_target(
            make_private_session("90001")
        )
        assert "未设置" in str(message)
        assert "http://203.0.113.10:8081/webui" in str(message)

        reply = str(
            await loaded_plugin.reset_account_password.callable_target(
                make_private_session("90001")
            )
        )
        password = reply.split("密码：")[1].split("\n")[0]
        assert "用户名：master" in reply and "http://203.0.113.10:8081/webui" in reply
        assert (await _login(client, password)).status_code == 200
        assert (await client.get(PROTECTED)).status_code == 200

        # 重置后既有会话立即失效，旧密码不可用，新密码可用。
        second = str(
            await loaded_plugin.reset_account_password.callable_target(
                make_private_session("90001")
            )
        )
        new_password = second.split("密码：")[1].split("\n")[0]
        assert new_password != password
        assert (await client.get(PROTECTED)).status_code == 401
        assert (await _login(client, password)).status_code == 401
        assert (await _login(client, new_password)).status_code == 200

        overview = str(
            await loaded_plugin.show_account.callable_target(
                make_private_session("90001")
            )
        )
        assert "在线会话：1" in overview and "密码状态：已设置" in overview
        assert new_password not in overview

        offline = await loaded_plugin.revoke_sessions.callable_target(
            make_private_session("90001")
        )
        assert "1 个" in str(offline)
        assert (await client.get(PROTECTED)).status_code == 401


@pytest.mark.parametrize("loaded_plugin", ["webui_account"], indirect=True)
@pytest.mark.parametrize(
    "handler", ["show_account", "reset_account_password", "revoke_sessions"]
)
def test_account_commands_declare_master_private_only(loaded_plugin, handler):
    command = getattr(loaded_plugin, handler).callable_target

    assert getattr(command, REQUIRES_ATTR) == {
        "chat": "private",
        "user": Permission.Master,
    }


@pytest.mark.parametrize("loaded_plugin", ["webui_account"], indirect=True)
def test_account_plugin_is_protected_control_plane(loaded_plugin):
    from tenko.host.plugins import PluginRuntime

    runtime = PluginRuntime()
    info = next(item for item in runtime.discover() if item.name == "webui_account")
    assert runtime.is_protected(info)
    assert loaded_plugin.reset_command.parse("/面板重置密码").matched
    assert not loaded_plugin.reset_command.parse("面板重置密码").matched


# ------------------------------------------------------------- CLI


def test_cli_webui_passwd_prints_working_password(tmp_path: Path):
    config = tmp_path / "tenko.toml"
    config.write_text("[webui]\nenabled = true\n", encoding="utf-8")
    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[2])}

    def run(*extra: str) -> dict[str, str]:
        result = subprocess.run(
            [sys.executable, "-m", "tenko", "webui-passwd", "--config", str(config)]
            + list(extra),
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return dict(
            line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line
        )

    first = run()
    second = run("--username", "boss")

    assert first["username"] == "master" and second["username"] == "boss"
    assert first["password"] != second["password"]
    import sqlite3

    with sqlite3.connect(tmp_path / ".tenko" / "tenko.db") as database:
        (stored,) = database.execute(
            "select password_hash from TenkoWebUIAccount"
        ).fetchone()
    assert verify_password(second["password"], stored)
    assert not verify_password(first["password"], stored)
