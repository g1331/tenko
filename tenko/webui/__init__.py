"""Tenko 内置 WebUI 的服务与配置适配。"""

from .middleware import WebUIAuthMiddleware
from .service import WebUIService

__all__ = ["WebUIAuthMiddleware", "WebUIService"]
