"""测试里按契约核对 WebUI 的真实响应。

处理函数标注的 ``ApiResponse[契约]`` 决定成功响应的 ``data`` 必须满足哪份契约；
这里的 ASGI 中间件在响应发完后严格校验（多余字段、类型不符都算错），失败时异常
直接抛给发请求的测试。``conftest.py`` 给测试里构造的每个 ``WebUIService`` 装上它。
"""

from __future__ import annotations

import json
import typing
from typing import Any

from scripts.webui_schema import adapter
from tenko.webui.auth import ApiResponse


def response_contract(endpoint: object) -> Any | None:
    """处理函数返回标注里的契约；没有用 ``ApiResponse`` 标注时为 None。"""

    try:
        hints = typing.get_type_hints(endpoint)
    except (NameError, TypeError):
        return None
    annotation = hints.get("return")
    if typing.get_origin(annotation) is not ApiResponse:
        return None
    return typing.get_args(annotation)[0]


def check_payload(contract: Any, body: bytes) -> None:
    payload = json.loads(body)
    if payload.get("ok") is True:
        adapter(contract).validate_json(json.dumps(payload["data"]), strict=True)


class ContractCheck:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()

        async def capture(message: Any) -> None:
            if message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, capture)
        # 路由匹配后 Starlette 把处理函数写进同一个 scope。
        contract = response_contract(scope.get("endpoint"))
        if contract is not None:
            check_payload(contract, bytes(body))
