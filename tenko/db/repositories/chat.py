"""面板聊天记录 repository。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from sqlalchemy import Executable, delete, func, select

from ..models import ChatMessage
from ._base import _Repository


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
                statement: Executable
                if dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                    statement = sqlite_insert(ChatMessage).on_conflict_do_nothing()
                else:
                    from sqlalchemy.dialects.postgresql import insert as pg_insert

                    statement = pg_insert(ChatMessage).on_conflict_do_nothing()
                await session.execute(statement, rows)
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


chat_message_repository = ChatMessageRepository()
