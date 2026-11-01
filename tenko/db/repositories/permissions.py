"""黑名单与手动授权 repository。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from ..models import (
    GroupBlacklist,
    PermissionGrant,
    UserBlacklist,
)
from ._base import _Repository, _utc


@dataclass(frozen=True, slots=True)
class BlacklistRecord:
    """一条人或群的黑名单记录；``target_id`` 是用户 ID 或群 ID。"""

    platform: str
    target_id: str
    reason: str = ""
    operator_id: str = ""
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    users: tuple[BlacklistRecord, ...]
    groups: tuple[BlacklistRecord, ...]
    # (platform, scope, user_id)；scope 为空串表示全局 BotAdmin。
    grants: tuple[tuple[str, str, str], ...]


_BLACKLIST_MODELS: dict[str, Any] = {
    "user": (UserBlacklist, "user_id"),
    "group": (GroupBlacklist, "group_id"),
}


class PermissionRepository(_Repository):
    """黑名单与手动授权；宿主启动时整表读入内存，之后逐条写入。"""

    async def load_state(self) -> PermissionSnapshot:
        async with self._session() as session:
            users = (await session.scalars(select(UserBlacklist))).all()
            groups = (await session.scalars(select(GroupBlacklist))).all()
            grants = (await session.scalars(select(PermissionGrant))).all()
        return PermissionSnapshot(
            users=tuple(
                BlacklistRecord(
                    row.platform,
                    row.user_id,
                    row.reason,
                    row.operator_id,
                    _utc(row.created_at),
                )
                for row in users
            ),
            groups=tuple(
                BlacklistRecord(
                    row.platform,
                    row.group_id,
                    row.reason,
                    row.operator_id,
                    _utc(row.created_at),
                )
                for row in groups
            ),
            grants=tuple((row.platform, row.scope, row.user_id) for row in grants),
        )

    async def add_blacklist(self, kind: str, record: BlacklistRecord) -> None:
        model, key = _BLACKLIST_MODELS[kind]
        async with self._session() as session:
            await session.merge(
                model(
                    platform=record.platform,
                    **{key: record.target_id},
                    reason=record.reason,
                    operator_id=record.operator_id,
                    created_at=record.created_at or datetime.now(UTC),
                )
            )
            await session.commit()

    async def remove_blacklist(self, kind: str, platform: str, target_id: str) -> None:
        model, key = _BLACKLIST_MODELS[kind]
        async with self._session() as session:
            await session.execute(
                delete(model).where(
                    model.platform == platform, getattr(model, key) == target_id
                )
            )
            await session.commit()

    async def add_grant(self, platform: str, scope: str, user_id: str) -> None:
        async with self._session() as session:
            await session.merge(
                PermissionGrant(platform=platform, scope=scope, user_id=user_id)
            )
            await session.commit()

    async def remove_grant(self, platform: str, scope: str, user_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                delete(PermissionGrant).where(
                    PermissionGrant.platform == platform,
                    PermissionGrant.scope == scope,
                    PermissionGrant.user_id == user_id,
                )
            )
            await session.commit()


permission_repository = PermissionRepository()
