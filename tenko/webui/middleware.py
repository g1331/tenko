"""WebUI 的 ASGI 中间件和静态资源挂载：只依赖鉴权与配置，与面板数据无关。"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable
from typing import Any

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from ..config import WebUIConfig
from ..db.errors import DatabaseError
from .auth import WEBUI_PATH, WebUIAuth, error_response as _error_response

ASSETS_PATH = f"{WEBUI_PATH}/assets"
_ASSET_CACHE = "public, max-age=31536000, immutable"


class HashedAssets(StaticFiles):
    """前端构建产物：文件名带内容哈希，可长期缓存；路径穿越由 StaticFiles 拒绝。"""

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in {200, 304}:
            response.headers["Cache-Control"] = _ASSET_CACHE
        return response


def _is_webui_path(path: str) -> bool:
    return path == WEBUI_PATH or path.startswith(f"{WEBUI_PATH}/")


class WebUIAuthMiddleware(BaseHTTPMiddleware):
    """仅保护 `/webui` 路径：来源 IP 白名单、会话 cookie 和写请求的同源校验。"""

    def __init__(self, app: Any, *, config: WebUIConfig, auth: WebUIAuth) -> None:
        super().__init__(app)
        self.config = config
        self.auth = auth

    def _allowed_ip(self, request: Request) -> bool:
        client = request.client
        if client is None:
            return False
        try:
            client_ip = ipaddress.ip_address(client.host)
        except ValueError:
            return False
        return any(
            client_ip in ipaddress.ip_network(item) for item in self.config.allowed_ips
        )

    @staticmethod
    def _is_public(request: Request) -> bool:
        path = request.url.path
        # 静态资源不含数据，登录页也需要加载；来源 IP 仍由上游校验。
        if request.method in {"GET", "HEAD"} and path.startswith(f"{ASSETS_PATH}/"):
            return True
        if request.method == "GET":
            return path in {
                WEBUI_PATH,
                f"{WEBUI_PATH}/",
                f"{WEBUI_PATH}/api/auth/state",
            }
        return request.method == "POST" and path == f"{WEBUI_PATH}/api/auth/login"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await self._dispatch(request, call_next)
        if _is_webui_path(request.url.path) and request.method not in {"GET", "HEAD"}:
            logger.info(
                "WebUI audit: method={} target={} action={} source={} status={}",
                request.method,
                json.dumps(request.url.path[:300], ensure_ascii=False),
                getattr(request.state, "webui_action", "request"),
                request.client.host if request.client else "unknown",
                response.status_code,
            )
        return response

    async def _dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if not _is_webui_path(path):
            return await call_next(request)
        if not self.config.enabled:
            return _error_response("not_found", "Not Found", 404)
        if not self._allowed_ip(request):
            return _error_response("unauthorized", "Unauthorized", 401)
        if not self._is_public(request):
            try:
                session = await self.auth.resolve(request)
            except DatabaseError:
                logger.error("WebUI session lookup failed: database unavailable")
                return _error_response(
                    "database_unavailable", "数据库暂不可用，无法校验登录状态", 503
                )
            if session is None:
                return _error_response("unauthorized", "Unauthorized", 401)
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if origin is not None and origin != expected:
                return _error_response("forbidden", "不允许跨来源操作", 403)
            if (
                request.headers.get("content-type", "").split(";")[0].strip()
                != "application/json"
            ):
                return _error_response("invalid_request", "请使用 JSON 请求体", 415)
        if path in {WEBUI_PATH, f"{WEBUI_PATH}/"} or path.startswith(f"{ASSETS_PATH}/"):
            # 入口页和静态资源由各自的处理器设置缓存头。
            return await call_next(request)
        try:
            response = await call_next(request)
        except DatabaseError:
            logger.error("WebUI auth operation failed: database unavailable")
            return _error_response(
                "database_unavailable", "数据库暂不可用，未确认操作成功", 503
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        refreshed = getattr(request.state, "webui_refresh_token", None)
        if refreshed and response.status_code < 400:
            self.auth.set_cookie(response, request, refreshed)
        return response


__all__ = ["ASSETS_PATH", "HashedAssets", "WebUIAuthMiddleware"]
