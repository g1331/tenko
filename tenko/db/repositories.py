"""Tenko 数据库 repository。

repository 只持有官方 database service 提供的异步 session 工厂，不把
SQLAlchemy 查询对象暴露给宿主或插件调用方。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from .errors import (
    DatabaseUnavailableError,
    InvalidGroupSettingError,
    is_database_unavailable,
)
from .ids import to_database_id
from .models import (
    AccountResponseState,
    ChatMessage,
    AccountRoute,
    FeatureState,
    GroupBlacklist,
    GroupSetting,
    PermissionGrant,
    RateLimitEvent,
    RateLimitSubjectState,
    StartupTime,
    UserBlacklist,
    WebUIAccount,
    WebUISession,
)

_SessionFactory = Callable[[], Any]
_session_factory: _SessionFactory | None = None
_RESPONSE_TYPES = frozenset({"random", "deterministic"})


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


class GroupSettingRepository(_Repository):
    """GroupSetting 的频控和响应策略 repository。"""

    async def get(self, group_id: str | int) -> GroupSetting | None:
        group = to_database_id(group_id, "群 ID")
        async with self._session() as session:
            return await session.get(GroupSetting, group)

    async def frequency_disabled_groups(self) -> tuple[int, ...]:
        """返回显式关闭了频率限制的群；没有记录的群按默认开启处理。"""

        async with self._session() as session:
            result = await session.scalars(
                select(GroupSetting.group_id)
                .where(GroupSetting.frequency_limitation.is_(False))
                .order_by(GroupSetting.group_id)
            )
            return tuple(result.all())

    async def set(
        self,
        group_id: str | int,
        *,
        frequency_limitation: bool | None = None,
        response_type: str | None = None,
    ) -> None:
        group = to_database_id(group_id, "群 ID")
        if frequency_limitation is not None and type(frequency_limitation) is not bool:
            raise TypeError("frequency_limitation 必须是布尔值或 None")
        if response_type is not None and response_type not in _RESPONSE_TYPES:
            raise InvalidGroupSettingError(
                "response_type 必须是 random 或 deterministic"
            )
        async with self._session() as session:
            row = await session.get(GroupSetting, group)
            if row is None:
                row = GroupSetting(
                    group_id=group,
                    frequency_limitation=(
                        True if frequency_limitation is None else frequency_limitation
                    ),
                    response_type=response_type or "random",
                )
                session.add(row)
            else:
                if frequency_limitation is not None:
                    row.frequency_limitation = frequency_limitation
                if response_type is not None:
                    row.response_type = response_type
            await session.commit()

    async def set_frequency_limitation(
        self, group_id: str | int, enabled: bool
    ) -> None:
        await self.set(group_id, frequency_limitation=enabled)

    async def set_response_type(self, group_id: str | int, response_type: str) -> None:
        await self.set(group_id, response_type=response_type)


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


@dataclass(frozen=True, slots=True)
class FeatureStateRecord:
    """repository 返回的功能开关状态记录。"""

    plugin_name: str
    group_id: str | None
    enabled: bool | None
    maintenance: bool


class FeatureStateRepository(_Repository):
    """Tenko 功能开关状态 repository。"""

    async def list_states(self) -> tuple[FeatureStateRecord, ...]:
        async with self._session() as session:
            result = await session.scalars(
                select(FeatureState).order_by(
                    FeatureState.plugin_name, FeatureState.group_id
                )
            )
            return tuple(
                FeatureStateRecord(
                    plugin_name=row.plugin_name,
                    group_id=row.group_id or None,
                    enabled=row.enabled,
                    maintenance=bool(row.maintenance),
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
                    group_id=row.group_id or "",
                    enabled=row.enabled,
                    maintenance=row.maintenance,
                )
                for row in rows
            )
            await session.commit()


@dataclass(frozen=True, slots=True)
class AccountRouteRecord:
    """repository 返回的有序账号路由记录。"""

    group_id: str
    account_id: str
    position: int


@dataclass(frozen=True, slots=True)
class AccountResponseRecord:
    """repository 返回的群响应策略记录。"""

    group_id: str
    response_type: str
    deterministic_account: str | None


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
                    AccountRoute.group_id, AccountRoute.position
                )
            )
            response_result = await session.scalars(
                select(AccountResponseState).order_by(AccountResponseState.group_id)
            )
            return AccountStateSnapshot(
                routes=tuple(
                    AccountRouteRecord(
                        group_id=row.group_id,
                        account_id=row.account_id,
                        position=row.position,
                    )
                    for row in route_result.all()
                ),
                responses=tuple(
                    AccountResponseRecord(
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
                    group_id=row.group_id,
                    account_id=row.account_id,
                    position=row.position,
                )
                for row in route_rows
            )
            session.add_all(
                AccountResponseState(
                    group_id=row.group_id,
                    response_type=row.response_type,
                    deterministic_account=row.deterministic_account,
                )
                for row in response_rows
            )
            await session.commit()


@dataclass(frozen=True, slots=True)
class RateLimitEventRecord:
    """repository 返回的限流窗口事件记录。"""

    group_id: str
    user_id: str
    occurred_at: float
    weight: int


@dataclass(frozen=True, slots=True)
class RateLimitSubjectRecord:
    """repository 返回的限流用户状态记录。"""

    group_id: str
    user_id: str
    cooldown_until: float | None
    blacklist_until: float | None


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
                    RateLimitSubjectState.group_id,
                    RateLimitSubjectState.user_id,
                )
            )
            return RateLimitStateSnapshot(
                events=tuple(
                    RateLimitEventRecord(
                        group_id=row.group_id,
                        user_id=row.user_id,
                        occurred_at=float(row.occurred_at),
                        weight=int(row.weight),
                    )
                    for row in event_result.all()
                ),
                subjects=tuple(
                    RateLimitSubjectRecord(
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
                    group_id=row.group_id,
                    user_id=row.user_id,
                    occurred_at=row.occurred_at,
                    weight=row.weight,
                )
                for row in event_rows
            )
            session.add_all(
                RateLimitSubjectState(
                    group_id=row.group_id,
                    user_id=row.user_id,
                    cooldown_until=row.cooldown_until,
                    blacklist_until=row.blacklist_until,
                )
                for row in subject_rows
            )
            await session.commit()


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


def _utc(value: datetime) -> datetime:
    # SQLite 不保存时区，读回的时间统一按 UTC 解释。
    return value if value.tzinfo else value.replace(tzinfo=UTC)


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


@dataclass(frozen=True, slots=True)
class ChatRecord:
    platform: str
    account_id: str
    chat_type: str
    channel_id: str
    channel_name: str
    message_id: str | None
    outgoing: bool
    user_id: str
    user_name: str
    content: str
    created_at: float
    id: int | None = None


def _chat_record(row: ChatMessage) -> ChatRecord:
    return ChatRecord(
        platform=row.platform,
        account_id=row.account_id,
        chat_type=row.chat_type,
        channel_id=row.channel_id,
        channel_name=row.channel_name,
        message_id=row.message_id,
        outgoing=row.outgoing,
        user_id=row.user_id,
        user_name=row.user_name,
        content=row.content,
        created_at=row.created_at,
        id=row.id,
    )


class ChatMessageRepository(_Repository):
    """面板聊天页的消息记录；写入按批进行，同一账号的同一条消息只存一次。"""

    async def add(self, records: Iterable[ChatRecord]) -> None:
        rows = [
            {
                "platform": record.platform,
                "account_id": record.account_id,
                "chat_type": record.chat_type,
                "channel_id": record.channel_id,
                "channel_name": record.channel_name,
                "message_id": record.message_id,
                "outgoing": record.outgoing,
                "user_id": record.user_id,
                "user_name": record.user_name,
                "content": record.content,
                "created_at": record.created_at,
            }
            for record in records
        ]
        if not rows:
            return
        async with self._session() as session:
            dialect = session.get_bind().dialect.name
            if dialect in {"sqlite", "postgresql"}:
                if dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert
                else:
                    from sqlalchemy.dialects.postgresql import insert
                await session.execute(
                    insert(ChatMessage).on_conflict_do_nothing(), rows
                )
                await session.commit()
                return
            # 其他数据库逐条写入，重复的消息跳过。
            for row in rows:
                if row["message_id"] is not None and await session.scalar(
                    select(ChatMessage.id).where(
                        ChatMessage.platform == row["platform"],
                        ChatMessage.account_id == row["account_id"],
                        ChatMessage.channel_id == row["channel_id"],
                        ChatMessage.message_id == row["message_id"],
                    )
                ):
                    continue
                session.add(ChatMessage(**row))
            await session.commit()

    async def conversations(
        self, platform: str, account_id: str, limit: int = 200
    ) -> tuple[ChatRecord, ...]:
        """账号每个会话的最后一条消息，最近的在前。

        channel_name 取会话里最近一次记下的名字：面板发出的消息不知道对方昵称。
        """

        scope = (ChatMessage.platform == platform, ChatMessage.account_id == account_id)
        latest = (
            select(func.max(ChatMessage.id))
            .where(*scope)
            .group_by(ChatMessage.channel_id)
        )
        named = (
            select(func.max(ChatMessage.id))
            .where(*scope, ChatMessage.channel_name != "")
            .group_by(ChatMessage.channel_id)
        )
        async with self._session() as session:
            rows = await session.scalars(
                select(ChatMessage)
                .where(ChatMessage.id.in_(latest))
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )
            records = [_chat_record(row) for row in rows]
            names = dict(
                (
                    await session.execute(
                        select(ChatMessage.channel_id, ChatMessage.channel_name).where(
                            ChatMessage.id.in_(named)
                        )
                    )
                ).all()
            )
        return tuple(
            replace(record, channel_name=names.get(record.channel_id, ""))
            for record in records
        )

    async def group_names(self) -> dict[str, str]:
        """每个群最近一次记下的非空群名，按群 ID 索引。"""

        named = (
            select(func.max(ChatMessage.id))
            .where(ChatMessage.chat_type == "group", ChatMessage.channel_name != "")
            .group_by(ChatMessage.channel_id)
        )
        async with self._session() as session:
            rows = await session.execute(
                select(ChatMessage.channel_id, ChatMessage.channel_name).where(
                    ChatMessage.id.in_(named)
                )
            )
            return dict(rows.all())

    async def messages(
        self,
        platform: str,
        account_id: str,
        channel_id: str,
        *,
        before: int | None = None,
        after: int | None = None,
        limit: int = 50,
    ) -> tuple[ChatRecord, ...]:
        """按时间顺序返回一段消息：默认最新的 ``limit`` 条，before / after 用于翻页和增量。"""

        statement = select(ChatMessage).where(
            ChatMessage.platform == platform,
            ChatMessage.account_id == account_id,
            ChatMessage.channel_id == channel_id,
        )
        if after is not None:
            statement = statement.where(ChatMessage.id > after).order_by(ChatMessage.id)
        else:
            if before is not None:
                statement = statement.where(ChatMessage.id < before)
            statement = statement.order_by(ChatMessage.id.desc())
        async with self._session() as session:
            rows = list(await session.scalars(statement.limit(limit)))
        if after is None:
            rows.reverse()
        return tuple(_chat_record(row) for row in rows)

    async def find(
        self,
        platform: str,
        account_id: str,
        channel_id: str,
        message_ids: Iterable[str],
    ) -> tuple[ChatRecord, ...]:
        """按平台消息 ID 取同一会话里的消息，用来补全回复引用。"""

        ids = sorted(set(message_ids))
        if not ids:
            return ()
        async with self._session() as session:
            rows = await session.scalars(
                select(ChatMessage).where(
                    ChatMessage.platform == platform,
                    ChatMessage.account_id == account_id,
                    ChatMessage.channel_id == channel_id,
                    ChatMessage.message_id.in_(ids),
                )
            )
            return tuple(_chat_record(row) for row in rows)

    async def prune(self, cutoff: float, keep: int) -> int:
        """删除早于 cutoff 的消息，并只保留最新的 keep 条；返回删除数量。"""

        async with self._session() as session:
            result = await session.execute(
                delete(ChatMessage).where(ChatMessage.created_at < cutoff)
            )
            deleted = int(result.rowcount or 0)
            boundary = await session.scalar(
                select(ChatMessage.id)
                .order_by(ChatMessage.id.desc())
                .offset(keep)
                .limit(1)
            )
            if boundary is not None:
                result = await session.execute(
                    delete(ChatMessage).where(ChatMessage.id <= boundary)
                )
                deleted += int(result.rowcount or 0)
            await session.commit()
            return deleted


permission_repository = PermissionRepository()
group_setting_repository = GroupSettingRepository()
feature_state_repository = FeatureStateRepository()
account_state_repository = AccountStateRepository()
rate_limit_repository = RateLimitRepository()
startup_time_repository = StartupTimeRepository()
webui_auth_repository = WebUIAuthRepository()
chat_message_repository = ChatMessageRepository()


__all__ = [
    "BlacklistRecord",
    "GroupSettingRepository",
    "PermissionRepository",
    "PermissionSnapshot",
    "AccountResponseRecord",
    "AccountRouteRecord",
    "AccountStateRepository",
    "AccountStateSnapshot",
    "ChatMessageRepository",
    "ChatRecord",
    "chat_message_repository",
    "FeatureStateRecord",
    "FeatureStateRepository",
    "RateLimitEventRecord",
    "RateLimitRepository",
    "RateLimitStateSnapshot",
    "RateLimitSubjectRecord",
    "StartupTimeRepository",
    "WebUIAccountRecord",
    "WebUIAuthRepository",
    "WebUISessionRecord",
    "account_state_repository",
    "configure_database_service",
    "configure_session_factory",
    "feature_state_repository",
    "group_setting_repository",
    "permission_repository",
    "rate_limit_repository",
    "startup_time_repository",
    "webui_auth_repository",
]


class ManagementRepository(_Repository):
    """账户偏好及管理面板运行期设置的持久化边界。"""

    async def accounts(self) -> tuple[dict[str, Any], ...]:
        import json
        from .models import AccountPreference

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
        import json
        from .models import AccountPreference

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
        from .models import AccountPreference

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
        from sqlalchemy.orm import aliased
        from .models import AccountPreference

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
        import json
        from .models import WebUISetting

        async with self._session() as session:
            row = await session.get(WebUISetting, name)
            return json.loads(row.value) if row is not None else None

    async def save_setting(self, name: str, value: Any) -> None:
        import json
        from .models import WebUISetting

        async with self._session() as session:
            await session.merge(WebUISetting(name=name, value=json.dumps(value)))
            await session.commit()
