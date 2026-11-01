"""限流状态 repository。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, select

from ...context import QQ_PLATFORM
from ..models import RateLimitEvent, RateLimitSubjectState
from ._base import _Repository


@dataclass(frozen=True, slots=True)
class RateLimitEventRecord:
    """repository 返回的限流窗口事件记录。"""

    group_id: str
    user_id: str
    occurred_at: float
    weight: int
    platform: str = QQ_PLATFORM


@dataclass(frozen=True, slots=True)
class RateLimitSubjectRecord:
    """repository 返回的限流用户状态记录。"""

    group_id: str
    user_id: str
    cooldown_until: float | None
    blacklist_until: float | None
    platform: str = QQ_PLATFORM


@dataclass(frozen=True, slots=True)
class RateLimitStateSnapshot:
    """限流窗口和到期状态的一致性快照。"""

    events: tuple[RateLimitEventRecord, ...]
    subjects: tuple[RateLimitSubjectRecord, ...]


class RateLimitRepository(_Repository):
    """Tenko 命令限流状态 repository。"""

    async def load_state(self) -> RateLimitStateSnapshot:
        async with self._session() as session:
            event_result = await session.scalars(
                select(RateLimitEvent).order_by(RateLimitEvent.id)
            )
            subject_result = await session.scalars(
                select(RateLimitSubjectState).order_by(
                    RateLimitSubjectState.platform,
                    RateLimitSubjectState.group_id,
                    RateLimitSubjectState.user_id,
                )
            )
            return RateLimitStateSnapshot(
                events=tuple(
                    RateLimitEventRecord(
                        platform=row.platform,
                        group_id=row.group_id,
                        user_id=row.user_id,
                        occurred_at=float(row.occurred_at),
                        weight=int(row.weight),
                    )
                    for row in event_result.all()
                ),
                subjects=tuple(
                    RateLimitSubjectRecord(
                        platform=row.platform,
                        group_id=row.group_id,
                        user_id=row.user_id,
                        cooldown_until=(
                            None
                            if row.cooldown_until is None
                            else float(row.cooldown_until)
                        ),
                        blacklist_until=(
                            None
                            if row.blacklist_until is None
                            else float(row.blacklist_until)
                        ),
                    )
                    for row in subject_result.all()
                ),
            )

    async def replace_state(
        self,
        events: Iterable[RateLimitEventRecord],
        subjects: Iterable[RateLimitSubjectRecord],
    ) -> None:
        event_rows = tuple(events)
        subject_rows = tuple(subjects)
        async with self._session() as session:
            await session.execute(delete(RateLimitEvent))
            await session.execute(delete(RateLimitSubjectState))
            session.add_all(
                RateLimitEvent(
                    platform=row.platform,
                    group_id=row.group_id,
                    user_id=row.user_id,
                    occurred_at=row.occurred_at,
                    weight=row.weight,
                )
                for row in event_rows
            )
            session.add_all(
                RateLimitSubjectState(
                    platform=row.platform,
                    group_id=row.group_id,
                    user_id=row.user_id,
                    cooldown_until=row.cooldown_until,
                    blacklist_until=row.blacklist_until,
                )
                for row in subject_rows
            )
            await session.commit()


rate_limit_repository = RateLimitRepository()
