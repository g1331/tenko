"""群级频率限制开关 repository。"""

from __future__ import annotations

from sqlalchemy import select

from ...context import GroupKey, group_key
from ..models import GroupSetting
from ._base import _Repository


class GroupSettingRepository(_Repository):
    """群级频率限制开关 repository。"""

    async def get(self, group: object) -> GroupSetting | None:
        async with self._session() as session:
            return await session.get(GroupSetting, group_key(group))

    async def frequency_disabled_groups(self) -> tuple[GroupKey, ...]:
        """返回显式关闭了频率限制的群；没有记录的群按默认开启处理。"""

        async with self._session() as session:
            result = await session.execute(
                select(GroupSetting.platform, GroupSetting.group_id)
                .where(GroupSetting.frequency_limitation.is_(False))
                .order_by(GroupSetting.platform, GroupSetting.group_id)
            )
            return tuple((platform, group) for platform, group in result.all())

    async def set_frequency_limitation(self, group: object, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise TypeError("frequency_limitation 必须是布尔值")
        platform, group_id = group_key(group)
        async with self._session() as session:
            await session.merge(
                GroupSetting(
                    platform=platform,
                    group_id=group_id,
                    frequency_limitation=enabled,
                )
            )
            await session.commit()


group_setting_repository = GroupSettingRepository()
