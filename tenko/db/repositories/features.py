"""功能开关状态 repository。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, select

from ...context import QQ_PLATFORM
from ..models import FeatureState
from ._base import _Repository


@dataclass(frozen=True, slots=True)
class FeatureStateRecord:
    """repository 返回的功能开关状态记录。"""

    plugin_name: str
    group_id: str | None
    enabled: bool | None
    maintenance: bool
    platform: str = QQ_PLATFORM


class FeatureStateRepository(_Repository):
    """Tenko 功能开关状态 repository。"""

    async def list_states(self) -> tuple[FeatureStateRecord, ...]:
        async with self._session() as session:
            result = await session.scalars(
                select(FeatureState).order_by(
                    FeatureState.plugin_name,
                    FeatureState.platform,
                    FeatureState.group_id,
                )
            )
            return tuple(
                FeatureStateRecord(
                    plugin_name=row.plugin_name,
                    group_id=row.group_id or None,
                    enabled=row.enabled,
                    maintenance=bool(row.maintenance),
                    platform=row.platform or QQ_PLATFORM,
                )
                for row in result.all()
            )

    async def replace_states(self, states: Iterable[FeatureStateRecord]) -> None:
        rows = tuple(states)
        async with self._session() as session:
            await session.execute(delete(FeatureState))
            session.add_all(
                FeatureState(
                    plugin_name=row.plugin_name,
                    platform="" if row.group_id is None else row.platform,
                    group_id=row.group_id or "",
                    enabled=row.enabled,
                    maintenance=row.maintenance,
                )
                for row in rows
            )
            await session.commit()


feature_state_repository = FeatureStateRepository()
