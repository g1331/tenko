"""repository 共用的 session 基础设施。

全局 session 工厂只保存在这个模块里，``_Repository`` 在调用时读取它。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from ..errors import DatabaseUnavailableError, is_database_unavailable


_SessionFactory = Callable[[], Any]
_session_factory: _SessionFactory | None = None


def configure_session_factory(factory: _SessionFactory | None) -> None:
    """配置全局 repository 使用的异步 session 工厂。"""

    global _session_factory
    if factory is not None and not callable(factory):
        raise TypeError("数据库 session 工厂必须是可调用对象或 None")
    _session_factory = factory


def configure_database_service(service: Any | None) -> None:
    """将官方 ``SqlalchemyService`` 接入全局 repository。"""

    if service is None:
        configure_session_factory(None)
        return
    factory = getattr(service, "get_session", None)
    if not callable(factory):
        raise DatabaseUnavailableError(
            "官方 database service 尚未提供 get_session 工厂"
        )
    configure_session_factory(factory)


class _Repository:
    def __init__(self, session_factory: _SessionFactory | None = None) -> None:
        if session_factory is not None and not callable(session_factory):
            raise TypeError("数据库 session 工厂必须是可调用对象或 None")
        self.session_factory = session_factory

    def _factory(self) -> _SessionFactory:
        factory = self.session_factory or _session_factory
        if factory is None:
            raise DatabaseUnavailableError("Tenko 数据库 session 工厂未配置")
        return factory

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        try:
            factory = self._factory()
            async with factory() as session:
                try:
                    yield session
                except Exception:
                    await session.rollback()
                    raise
        except DatabaseUnavailableError:
            raise
        except Exception as error:
            if not is_database_unavailable(error):
                raise
            raise DatabaseUnavailableError(
                f"Tenko 数据库不可用: {type(error).__name__}: {error}"
            ) from error


def _utc(value: datetime) -> datetime:
    # SQLite 不保存时区，读回的时间统一按 UTC 解释。
    return value if value.tzinfo else value.replace(tzinfo=UTC)
