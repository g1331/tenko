"""启动耗时样本 repository。"""

from __future__ import annotations

from sqlalchemy import select

from ..models import StartupTime
from ._base import _Repository


class StartupTimeRepository(_Repository):
    """启动耗时样本 repository。"""

    async def list_durations(self) -> tuple[float, ...]:
        async with self._session() as session:
            result = await session.scalars(
                select(StartupTime.duration).order_by(StartupTime.id)
            )
            return tuple(float(value) for value in result.all())

    async def record(self, duration: float) -> None:
        async with self._session() as session:
            session.add(StartupTime(duration=duration))
            await session.commit()


startup_time_repository = StartupTimeRepository()
