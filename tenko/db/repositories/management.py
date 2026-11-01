"""账户偏好与管理面板运行期设置 repository。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import aliased

from ..models import (
    AccountPreference,
    ChatMessage,
    WebUISetting,
)
from ._base import _Repository


class ManagementRepository(_Repository):
    """账户偏好及管理面板运行期设置的持久化边界。"""

    async def accounts(self) -> tuple[dict[str, Any], ...]:
        async with self._session() as session:
            rows = await session.scalars(select(AccountPreference))
            return tuple(
                {
                    "platform": row.platform,
                    "id": row.account_id,
                    "alias": row.alias,
                    "enabled": row.enabled,
                    "capabilities": json.loads(row.capabilities),
                }
                for row in rows
            )

    async def save_account(self, value: dict[str, Any]) -> None:
        async with self._session() as session:
            await session.merge(
                AccountPreference(
                    platform=value["platform"],
                    account_id=value["id"],
                    alias=value["alias"],
                    enabled=value["enabled"],
                    capabilities=json.dumps(value["capabilities"]),
                )
            )
            await session.commit()

    async def forget_account(self, platform: str, account_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                delete(AccountPreference).where(
                    AccountPreference.platform == platform,
                    AccountPreference.account_id == account_id,
                )
            )
            await session.commit()

    async def rename_platform(self, old: str, new: str) -> int:
        """把账号偏好和聊天记录中的旧平台名改为新名，返回改动的行数。

        升级、回滚、再升级后两种名字可能同时存在：与新名冲突的旧行以新行为准。
        冲突行先查出 ID 再删除，避免 MySQL 不支持在 DELETE 里自引用同一张表。
        """
        async with self._session() as session:
            current = aliased(AccountPreference)
            stale = (
                await session.scalars(
                    select(AccountPreference.account_id)
                    .join(
                        current,
                        (current.platform == new)
                        & (current.account_id == AccountPreference.account_id),
                    )
                    .where(AccountPreference.platform == old)
                )
            ).all()
            if stale:
                await session.execute(
                    delete(AccountPreference).where(
                        AccountPreference.platform == old,
                        AccountPreference.account_id.in_(stale),
                    )
                )
            current = aliased(ChatMessage)
            stale = (
                await session.scalars(
                    select(ChatMessage.id)
                    .join(
                        current,
                        (current.platform == new)
                        & (current.account_id == ChatMessage.account_id)
                        & (current.channel_id == ChatMessage.channel_id)
                        & (current.message_id == ChatMessage.message_id),
                    )
                    .where(ChatMessage.platform == old)
                )
            ).all()
            for start in range(0, len(stale), 500):
                await session.execute(
                    delete(ChatMessage).where(
                        ChatMessage.id.in_(stale[start : start + 500])
                    )
                )
            changed = 0
            for model in (AccountPreference, ChatMessage):
                result = await session.execute(
                    update(model).where(model.platform == old).values(platform=new)
                )
                changed += result.rowcount or 0
            await session.commit()
            return changed

    async def setting(self, name: str) -> Any:
        async with self._session() as session:
            row = await session.get(WebUISetting, name)
            return json.loads(row.value) if row is not None else None

    async def save_setting(self, name: str, value: Any) -> None:
        async with self._session() as session:
            await session.merge(WebUISetting(name=name, value=json.dumps(value)))
            await session.commit()
