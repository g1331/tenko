"""面板命令沙盒：读取沙盒对话、以选定身份发消息、清空记录。

消息经 ``SandboxAdapter`` 变成标准事件进入 Entari，详见 ``tenko.host.sandbox``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..host import sandbox as sandbox_module
from ..host.sandbox import (
    IDENTITIES,
    SANDBOX_BOT_ID,
    SANDBOX_GROUP_ID,
    SANDBOX_GROUP_NAME,
    SANDBOX_PRIVATE_CHANNEL,
)
from .auth import read_json
from .chat import build_message

if TYPE_CHECKING:
    from .service import WebUIService

CHATS = {"group": SANDBOX_GROUP_ID, "private": SANDBOX_PRIVATE_CHANNEL}


def _adapter():
    adapter = sandbox_module.sandbox
    if adapter is None:
        raise RuntimeError("命令沙盒未启用")
    return adapter


def _chat(value: object) -> str:
    if value not in CHATS:
        raise ValueError("会话只能是 group 或 private")
    return str(value)


class SandboxPanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (
            ("/api/manage/sandbox", ["GET"], self.state),
            ("/api/manage/sandbox/send", ["POST"], self.send),
            ("/api/manage/sandbox/clear", ["POST"], self.clear),
        )

    async def state(self, request: Request) -> JSONResponse:
        async def read():
            params = request.query_params
            chat = _chat(params.get("chat", "group"))
            after = params.get("after")
            if after is not None and not after.isdigit():
                raise ValueError("after 必须是消息序号")
            adapter = _adapter()
            return {
                "ready": adapter.ready,
                "bot": {"id": SANDBOX_BOT_ID, "name": adapter.login.user.name or ""},
                "group": {"id": SANDBOX_GROUP_ID, "name": SANDBOX_GROUP_NAME},
                "identities": [
                    {"value": key, "user_id": user_id, "name": name}
                    for key, (user_id, name, _role) in IDENTITIES.items()
                ],
                "messages": adapter.messages(
                    CHATS[chat], None if after is None else int(after)
                ),
            }

        return await self.service._manage(request, read, lock=False)

    async def send(self, request: Request) -> JSONResponse:
        async def action():
            body = await read_json(request, limit=6 * 1024 * 1024)
            if set(body) - {"chat", "identity", "content"}:
                raise ValueError("未知的发送参数")
            chat = _chat(body.get("chat"))
            identity = body.get("identity")
            if identity not in IDENTITIES:
                raise ValueError("未知的测试身份")
            elements = build_message(body.get("content"))
            request.state.webui_action = f"sandbox-send {chat} as {identity}"
            await _adapter().send(chat, identity, elements)
            return {"sent": True}

        return await self.service._manage(request, action, lock=False)

    async def clear(self, request: Request) -> JSONResponse:
        async def action():
            request.state.webui_action = "sandbox-clear"
            _adapter().clear()
            return {"cleared": True}

        return await self.service._manage(request, action, lock=False)


__all__ = ["SandboxPanel"]
