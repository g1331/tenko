"""WebUI 面板账号：密码哈希、服务端会话、登录限流和鉴权接口。

账号与会话都保存在数据库里，面板、QQ 指令和 CLI 共用同一份状态，因此任何一方
重置密码或撤销会话都会立即在运行中的面板生效。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..db.errors import DatabaseError

if TYPE_CHECKING:
    from ..db.repositories import WebUIAuthRepository, WebUISessionRecord

WEBUI_PATH = "/webui"
SESSION_COOKIE = "tenko_webui_session"
SESSION_TTL = timedelta(days=7)
# 滑动续期的最小写入间隔，避免面板轮询时每个请求都写库。
TOUCH_INTERVAL = timedelta(seconds=60)
DEFAULT_USERNAME = "master"
MIN_PASSWORD_LENGTH = 8
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,32}")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def error_response(
    code: str, message: str, status_code: int, **extra: object
) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message, **extra}},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def success_response(data: object) -> JSONResponse:
    return JSONResponse(
        {"ok": True, "data": data}, headers={"Cache-Control": "no-store"}
    )


async def read_json(request: Request, limit: int = 16384) -> dict[str, Any]:
    """读取不超过 ``limit`` 字节（默认 16 KiB）的 JSON 对象请求体。"""

    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise ValueError(f"请求体不能超过 {limit // 1024} KiB")
        data.extend(chunk)
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("请求体必须是有效 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return value


# ---------------------------------------------------------------- 密码


def hash_password(password: str) -> str:
    """返回 ``scrypt$n$r$p$salt_b64$hash_b64``。"""

    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
    )
    encode = base64.b64encode
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${encode(salt).decode()}${encode(digest).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码；哈希格式不合法时按不匹配处理。"""

    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_bytes = base64.b64decode(expected)
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected_bytes),
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected_bytes)


def generate_password() -> str:
    return secrets.token_urlsafe(12)


def validate_username(value: object) -> str:
    if not isinstance(value, str) or not _USERNAME_PATTERN.fullmatch(value):
        raise ValueError("用户名需为 1-32 位字母、数字、下划线、点或连字符")
    return value


def validate_new_password(value: object) -> str:
    if not isinstance(value, str) or len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"新密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def default_repository() -> WebUIAuthRepository:
    # repository 依赖官方 database 插件已加载，只能在运行期延迟导入。
    from ..db.repositories import webui_auth_repository

    return webui_auth_repository


async def reset_password(
    username: str | None = None,
    *,
    repository: WebUIAuthRepository | None = None,
) -> tuple[str, str, int]:
    """生成新随机密码并撤销全部会话，返回 ``(用户名, 密码, 撤销数量)``。

    账号不存在时以默认用户名创建；``username`` 为空则保持现有用户名。
    """

    repository = repository or default_repository()
    account = await repository.get_account()
    name = validate_username(
        username or (account.username if account else DEFAULT_USERNAME)
    )
    password = generate_password()
    await repository.save_account(
        name, await asyncio.to_thread(hash_password, password), _now()
    )
    return name, password, await repository.delete_sessions()


# ---------------------------------------------------------------- 登录限流


class LoginLimiter:
    """按客户端 IP 的内存限流：窗口内失败达到上限后拒绝，成功即清零。"""

    def __init__(
        self,
        max_failures: int = LOGIN_MAX_FAILURES,
        window: float = LOGIN_WINDOW_SECONDS,
        max_entries: int = 1024,
    ) -> None:
        self.max_failures = max_failures
        self.window = window
        self.max_entries = max_entries
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, ip: str, now: float) -> deque[float]:
        failures = self._failures.get(ip)
        if failures is None:
            return deque()
        while failures and now - failures[0] >= self.window:
            failures.popleft()
        if not failures:
            del self._failures[ip]
        return failures

    def retry_after(self, ip: str) -> int:
        """被限制时返回需要等待的秒数，否则返回 0。"""

        now = time.monotonic()
        failures = self._recent(ip, now)
        if len(failures) < self.max_failures:
            return 0
        return max(1, int(failures[0] + self.window - now) + 1)

    def fail(self, ip: str) -> None:
        now = time.monotonic()
        if ip not in self._failures and len(self._failures) >= self.max_entries:
            for key in list(self._failures):
                self._recent(key, now)
            while len(self._failures) >= self.max_entries:
                del self._failures[next(iter(self._failures))]
        self._failures.setdefault(ip, deque(maxlen=self.max_failures)).append(now)

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)


# ---------------------------------------------------------------- 会话与接口


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class WebUIAuth:
    """会话解析与 ``/webui/api/auth/*`` 接口。"""

    def __init__(
        self,
        repository: WebUIAuthRepository | None = None,
        limiter: LoginLimiter | None = None,
    ) -> None:
        self._repository = repository
        self.limiter = limiter or LoginLimiter()

    @property
    def repository(self) -> WebUIAuthRepository:
        return self._repository or default_repository()

    # cookie ---------------------------------------------------------------

    @staticmethod
    def set_cookie(response: Response, request: Request, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(SESSION_TTL.total_seconds()),
            path=WEBUI_PATH,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )

    @staticmethod
    def clear_cookie(response: Response, request: Request) -> None:
        # 当前请求不再需要由中间件续期 cookie。
        request.state.webui_refresh_token = None
        response.delete_cookie(
            SESSION_COOKIE,
            path=WEBUI_PATH,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )

    # 会话 -----------------------------------------------------------------

    async def resolve(self, request: Request) -> WebUISessionRecord | None:
        """按 cookie 解析会话；过期会话会被清理，活跃会话滑动续期。"""

        if hasattr(request.state, "webui_session"):
            return request.state.webui_session
        record = None
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            record = await self.repository.get_session_by_token_hash(_digest(token))
        if record is not None:
            now = _now()
            if record.expires_at <= now:
                await self.repository.delete_session(record.id)
                record = None
            elif now - record.last_seen_at >= TOUCH_INTERVAL:
                expires_at = now + SESSION_TTL
                await self.repository.touch_session(record.id, now, expires_at)
                record = replace(record, last_seen_at=now, expires_at=expires_at)
                request.state.webui_refresh_token = token
        request.state.webui_session = record
        return record

    async def revalidate(self, request: Request) -> bool:
        """长连接定期复核会话；数据库暂时不可用时先保持连接。"""

        if hasattr(request.state, "webui_session"):
            del request.state.webui_session
        try:
            return await self.resolve(request) is not None
        except DatabaseError:
            return True

    async def _current(self, request: Request) -> WebUISessionRecord:
        record = await self.resolve(request)
        assert record is not None  # 中间件已拦截未登录请求
        return record

    # 接口 -----------------------------------------------------------------

    async def state(self, request: Request) -> JSONResponse:
        record = await self.resolve(request)
        account = await self.repository.get_account()
        return success_response(
            {
                "authenticated": record is not None,
                "username": account.username if record and account else None,
                "password_set": account is not None,
                "expires_at": _iso(record.expires_at) if record else None,
            }
        )

    def _too_many(self, retry_after: int) -> JSONResponse:
        response = error_response(
            "too_many_attempts",
            "登录尝试过于频繁，请稍后再试",
            429,
            retry_after=retry_after,
        )
        response.headers["Retry-After"] = str(retry_after)
        return response

    async def login(self, request: Request) -> JSONResponse:
        ip = _client_ip(request)
        if retry_after := self.limiter.retry_after(ip):
            return self._too_many(retry_after)
        try:
            body = await read_json(request)
        except ValueError as error:
            return error_response("invalid_request", str(error), 400)
        username, password = body.get("username"), body.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return error_response("invalid_request", "需要 username 和 password", 400)
        account = await self.repository.get_account()
        if account is None:
            return error_response(
                "password_not_set",
                "尚未设置面板密码，请由 Master 在 QQ 私聊发送 /面板重置密码",
                409,
            )
        password_ok = await asyncio.to_thread(
            verify_password, password, account.password_hash
        )
        username_ok = hmac.compare_digest(username.encode(), account.username.encode())
        if not (password_ok and username_ok):
            self.limiter.fail(ip)
            return error_response("invalid_credentials", "用户名或密码错误", 401)
        self.limiter.reset(ip)
        from ..db.repositories import WebUISessionRecord

        now = _now()
        token = secrets.token_urlsafe(32)
        record = WebUISessionRecord(
            id=secrets.token_hex(8),
            token_hash=_digest(token),
            username=account.username,
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_TTL,
            ip=ip[:64],
            user_agent=request.headers.get("user-agent", "")[:200],
        )
        await self.repository.delete_expired(now)
        await self.repository.create_session(record)
        response = success_response(
            {"username": account.username, "expires_at": _iso(record.expires_at)}
        )
        self.set_cookie(response, request, token)
        return response

    async def logout(self, request: Request) -> JSONResponse:
        record = await self._current(request)
        await self.repository.delete_session(record.id)
        response = success_response({})
        self.clear_cookie(response, request)
        return response

    async def sessions(self, request: Request) -> JSONResponse:
        current = await self._current(request)
        now = _now()
        return success_response(
            {
                "sessions": [
                    {
                        "id": item.id,
                        "created_at": _iso(item.created_at),
                        "last_seen_at": _iso(item.last_seen_at),
                        "expires_at": _iso(item.expires_at),
                        "ip": item.ip,
                        "user_agent": item.user_agent,
                        "current": item.id == current.id,
                    }
                    for item in await self.repository.list_sessions()
                    if item.expires_at > now
                ]
            }
        )

    async def revoke(self, request: Request) -> JSONResponse:
        current = await self._current(request)
        session_id = request.path_params["id"]
        if not await self.repository.delete_session(session_id):
            return error_response("not_found", "会话不存在", 404)
        response = success_response({})
        if session_id == current.id:
            self.clear_cookie(response, request)
        return response

    async def revoke_others(self, request: Request) -> JSONResponse:
        current = await self._current(request)
        revoked = await self.repository.delete_sessions(except_id=current.id)
        return success_response({"revoked": revoked})

    async def account(self, request: Request) -> JSONResponse:
        current = await self._current(request)
        try:
            body = await read_json(request)
            password = body.get("current_password")
            if not isinstance(password, str):
                raise ValueError("需要 current_password")
            username = body.get("username")
            if username is not None:
                username = validate_username(username)
            new_password = body.get("new_password")
            if new_password is not None:
                new_password = validate_new_password(new_password)
        except ValueError as error:
            return error_response("invalid_request", str(error), 400)
        ip = _client_ip(request)
        if retry_after := self.limiter.retry_after(ip):
            return self._too_many(retry_after)
        account = await self.repository.get_account()
        if account is None or not await asyncio.to_thread(
            verify_password, password, account.password_hash
        ):
            self.limiter.fail(ip)
            return error_response("invalid_credentials", "当前密码错误", 401)
        self.limiter.reset(ip)
        password_hash = account.password_hash
        if new_password is not None:
            password_hash = await asyncio.to_thread(hash_password, new_password)
        username = username or account.username
        await self.repository.save_account(username, password_hash, _now())
        if new_password is not None:
            await self.repository.delete_sessions(except_id=current.id)
        return success_response({"username": username})


__all__ = [
    "DEFAULT_USERNAME",
    "LoginLimiter",
    "SESSION_COOKIE",
    "WEBUI_PATH",
    "WebUIAuth",
    "default_repository",
    "error_response",
    "generate_password",
    "hash_password",
    "read_json",
    "reset_password",
    "success_response",
    "validate_new_password",
    "validate_username",
    "verify_password",
]
