"""待处理的好友申请和群邀请。

插件把需要人工决定的请求登记在这里，并附上执行决定的回调；面板列出它们，
处理后由回调完成平台动作。只存在进程内存：群邀请一小时后会自动拒绝，好友申请
在 QQ 里也能找到，重启后不再列出。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .sandbox import refuse_in_sandbox

Decide = Callable[[bool, str], Awaitable[None]]


@dataclass(slots=True)
class InboxItem:
    kind: Literal["friend", "group"]
    request_id: str
    account: Any
    user_id: str | None
    user_name: str
    comment: str
    decide: Decide
    group_id: str | None = None
    group_name: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def account_id(self) -> str:
        return str(getattr(self.account, "self_id", ""))

    @property
    def platform(self) -> str:
        return str(getattr(self.account, "platform", ""))


class Inbox:
    def __init__(self) -> None:
        self._items: dict[str, InboxItem] = {}

    def __len__(self) -> int:
        return len(self._items)

    def add(self, item: InboxItem) -> InboxItem:
        # 同一请求重复推送时只保留最新一条。
        self.discard(item.kind, item.account_id, item.request_id)
        self._items[item.id] = item
        return item

    def discard(self, kind: str, account_id: str, request_id: str) -> None:
        for key, item in tuple(self._items.items()):
            if (item.kind, item.account_id, item.request_id) == (
                kind,
                account_id,
                request_id,
            ):
                del self._items[key]

    def remove(self, item_id: str) -> InboxItem:
        refuse_in_sandbox("处理待办申请")
        return self._items.pop(item_id)

    def get(self, item_id: str) -> InboxItem:
        return self._items[item_id]

    def items(self) -> list[InboxItem]:
        return sorted(self._items.values(), key=lambda item: item.created_at)


inbox = Inbox()

__all__ = ["Inbox", "InboxItem", "inbox"]
