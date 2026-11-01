"""账号路由与响应状态 repository。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, select

from ...context import QQ_PLATFORM
from ..models import AccountResponseState, AccountRoute
from ._base import _Repository


@dataclass(frozen=True, slots=True)
class AccountRouteRecord:
    """repository 返回的有序账号路由记录。"""

    group_id: str
    account_id: str
    position: int
    platform: str = QQ_PLATFORM


@dataclass(frozen=True, slots=True)
class AccountResponseRecord:
    """repository 返回的群响应策略记录。"""

    group_id: str
    response_type: str
    deterministic_account: str | None
    platform: str = QQ_PLATFORM


@dataclass(frozen=True, slots=True)
class AccountStateSnapshot:
    """账号路由和响应策略的一致性快照。"""

    routes: tuple[AccountRouteRecord, ...]
    responses: tuple[AccountResponseRecord, ...]


class AccountStateRepository(_Repository):
    """Tenko 账号群路由及响应策略 repository。"""

    async def load_state(self) -> AccountStateSnapshot:
        async with self._session() as session:
            route_result = await session.scalars(
                select(AccountRoute).order_by(
                    AccountRoute.platform, AccountRoute.group_id, AccountRoute.position
                )
            )
            response_result = await session.scalars(
                select(AccountResponseState).order_by(
                    AccountResponseState.platform, AccountResponseState.group_id
                )
            )
            return AccountStateSnapshot(
                routes=tuple(
                    AccountRouteRecord(
                        platform=row.platform,
                        group_id=row.group_id,
                        account_id=row.account_id,
                        position=row.position,
                    )
                    for row in route_result.all()
                ),
                responses=tuple(
                    AccountResponseRecord(
                        platform=row.platform,
                        group_id=row.group_id,
                        response_type=row.response_type,
                        deterministic_account=row.deterministic_account,
                    )
                    for row in response_result.all()
                ),
            )

    async def replace_state(
        self,
        routes: Iterable[AccountRouteRecord],
        responses: Iterable[AccountResponseRecord],
    ) -> None:
        route_rows = tuple(routes)
        response_rows = tuple(responses)
        async with self._session() as session:
            await session.execute(delete(AccountRoute))
            await session.execute(delete(AccountResponseState))
            session.add_all(
                AccountRoute(
                    platform=row.platform,
                    group_id=row.group_id,
                    account_id=row.account_id,
                    position=row.position,
                )
                for row in route_rows
            )
            session.add_all(
                AccountResponseState(
                    platform=row.platform,
                    group_id=row.group_id,
                    response_type=row.response_type,
                    deterministic_account=row.deterministic_account,
                )
                for row in response_rows
            )
            await session.commit()


account_state_repository = AccountStateRepository()
