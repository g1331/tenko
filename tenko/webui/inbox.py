"""面板收件箱：待处理的好友、入群请求，批准、拒绝或忽略。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import read_json

if TYPE_CHECKING:
    from .service import WebUIService


class InboxPanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (
            ("/api/manage/inbox", ["GET"], self.inbox_view),
            ("/api/manage/inbox/{id}", ["POST"], self.inbox_action),
        )

    def data(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "platform": item.platform,
                    "account_id": item.account_id,
                    "user_id": item.user_id,
                    "user_name": item.user_name,
                    "comment": item.comment,
                    "group_id": item.group_id,
                    "group_name": item.group_name,
                    "created_at": item.created_at.isoformat(),
                    "expires_at": item.expires_at.isoformat()
                    if item.expires_at
                    else None,
                }
                for item in self.service.inbox.items()
            ]
        }

    async def inbox_view(self, request: Request) -> JSONResponse:
        del request
        return await self.service.read("inbox", self.data)

    async def inbox_action(self, request: Request) -> JSONResponse:
        inbox = self.service.inbox

        async def action():
            body = await read_json(request)
            decision = body.get("action")
            comment = body.get("comment", "")
            if (
                set(body) - {"action", "confirm", "comment"}
                or decision not in {"approve", "reject", "dismiss"}
                or body.get("confirm") is not True
            ):
                raise ValueError("请确认要执行的操作")
            if not isinstance(comment, str) or len(comment) > 100:
                raise ValueError("理由必须是 100 字以内的文本")
            item = inbox.get(request.path_params["id"])
            request.state.webui_action = f"inbox-{decision}"
            # 忽略只从列表移除，平台上的请求保持原样。
            if decision != "dismiss":
                await item.decide(decision == "approve", comment.strip())
            inbox.discard(item.kind, item.account_id, item.request_id)
            return self.data()

        return await self.service.manage(request, action)


__all__ = ["InboxPanel"]
