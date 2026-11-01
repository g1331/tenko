"""管理面板账号与登录会话 repository。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update

from ..models import WebUIAccount, WebUISession
from ._base import _Repository, _utc


@dataclass(frozen=True, slots=True)
class WebUIAccountRecord:
    username: str
    password_hash: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WebUISessionRecord:
    id: str
    token_hash: str
    username: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str
    user_agent: str


def _session_record(row: WebUISession) -> WebUISessionRecord:
    return WebUISessionRecord(
        id=row.id,
        token_hash=row.token_hash,
        username=row.username,
        created_at=_utc(row.created_at),
        last_seen_at=_utc(row.last_seen_at),
        expires_at=_utc(row.expires_at),
        ip=row.ip,
        user_agent=row.user_agent,
    )


class WebUIAuthRepository(_Repository):
    """管理面板唯一账号与登录会话；面板、插件和 CLI 共享这份持久化状态。"""

    async def get_account(self) -> WebUIAccountRecord | None:
        async with self._session() as session:
            row = await session.get(WebUIAccount, 1)
            if row is None:
                return None
            return WebUIAccountRecord(
                row.username, row.password_hash, _utc(row.updated_at)
            )

    async def save_account(
        self, username: str, password_hash: str, now: datetime
    ) -> None:
        async with self._session() as session:
            await session.merge(
                WebUIAccount(
                    id=1,
                    username=username,
                    password_hash=password_hash,
                    updated_at=now,
                )
            )
            await session.commit()

    async def create_session(self, record: WebUISessionRecord) -> None:
        async with self._session() as session:
            session.add(
                WebUISession(
                    id=record.id,
                    token_hash=record.token_hash,
                    username=record.username,
                    created_at=record.created_at,
                    last_seen_at=record.last_seen_at,
                    expires_at=record.expires_at,
                    ip=record.ip,
                    user_agent=record.user_agent,
                )
            )
            await session.commit()

    async def get_session_by_token_hash(
        self, token_hash: str
    ) -> WebUISessionRecord | None:
        async with self._session() as session:
            row = await session.scalar(
                select(WebUISession).where(WebUISession.token_hash == token_hash)
            )
            return None if row is None else _session_record(row)

    async def touch_session(
        self, session_id: str, last_seen_at: datetime, expires_at: datetime
    ) -> None:
        async with self._session() as session:
            await session.execute(
                update(WebUISession)
                .where(WebUISession.id == session_id)
                .values(last_seen_at=last_seen_at, expires_at=expires_at)
            )
            await session.commit()

    async def list_sessions(self) -> tuple[WebUISessionRecord, ...]:
        async with self._session() as session:
            rows = await session.scalars(
                select(WebUISession).order_by(WebUISession.created_at.desc())
            )
            return tuple(_session_record(row) for row in rows)

    async def count_sessions(self, now: datetime) -> int:
        async with self._session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(WebUISession)
                    .where(WebUISession.expires_at > now)
                )
                or 0
            )

    async def delete_session(self, session_id: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                delete(WebUISession).where(WebUISession.id == session_id)
            )
            await session.commit()
            return bool(result.rowcount)

    async def delete_sessions(self, *, except_id: str | None = None) -> int:
        """删除全部会话，可保留一个；返回删除数量。"""

        statement = delete(WebUISession)
        if except_id is not None:
            statement = statement.where(WebUISession.id != except_id)
        async with self._session() as session:
            result = await session.execute(statement)
            await session.commit()
            return int(result.rowcount or 0)

    async def delete_expired(self, now: datetime) -> int:
        async with self._session() as session:
            result = await session.execute(
                delete(WebUISession).where(WebUISession.expires_at <= now)
            )
            await session.commit()
            return int(result.rowcount or 0)


webui_auth_repository = WebUIAuthRepository()
