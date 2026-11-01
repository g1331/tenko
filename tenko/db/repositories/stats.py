"""命令调用统计 repository。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select

from ..models import CommandStat
from ._base import _Repository, _utc


@dataclass(frozen=True, slots=True)
class CommandStatRecord:
    hour: datetime
    plugin: str
    handler: str
    subcommand: str
    platform: str
    group_id: str
    outcome: str
    count: int
    total_ms: int
    max_ms: int
    render_ms: int
    replies: int
    actions: Mapping[str, int]

    @property
    def key(self) -> tuple[Any, ...]:
        return (
            self.hour,
            self.plugin,
            self.handler,
            self.subcommand,
            self.platform,
            self.group_id,
            self.outcome,
        )


class CommandStatRepository(_Repository):
    """调用统计：按小时汇总的计数只做累加。"""

    async def add(self, records: Iterable[CommandStatRecord]) -> None:
        async with self._session() as session:
            for record in records:
                row = await session.get(CommandStat, record.key)
                if row is None:
                    row = CommandStat(
                        hour=record.hour,
                        plugin=record.plugin,
                        handler=record.handler,
                        subcommand=record.subcommand,
                        platform=record.platform,
                        group_id=record.group_id,
                        outcome=record.outcome,
                        count=0,
                        total_ms=0,
                        max_ms=0,
                        render_ms=0,
                        replies=0,
                        actions="{}",
                    )
                    session.add(row)
                row.count += record.count
                row.total_ms += record.total_ms
                row.max_ms = max(row.max_ms, record.max_ms)
                row.render_ms += record.render_ms
                row.replies += record.replies
                actions = json.loads(row.actions or "{}")
                for name, times in record.actions.items():
                    actions[name] = actions.get(name, 0) + times
                row.actions = json.dumps(actions, ensure_ascii=False)
            await session.commit()

    async def since(self, start: datetime) -> tuple[CommandStatRecord, ...]:
        async with self._session() as session:
            rows = (
                await session.scalars(
                    select(CommandStat).where(CommandStat.hour >= start)
                )
            ).all()
            return tuple(
                CommandStatRecord(
                    _utc(row.hour),
                    row.plugin,
                    row.handler,
                    row.subcommand,
                    row.platform,
                    row.group_id,
                    row.outcome,
                    row.count,
                    row.total_ms,
                    row.max_ms,
                    row.render_ms,
                    row.replies,
                    json.loads(row.actions or "{}"),
                )
                for row in rows
            )

    async def prune(self, before: datetime) -> None:
        async with self._session() as session:
            await session.execute(delete(CommandStat).where(CommandStat.hour < before))
            await session.commit()
