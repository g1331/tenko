"""面板黑名单管理（#234）：查看、加入、移出人和群的全局黑名单。

与 ``/拉黑``、``/拉黑群`` 命令共用 ``permission_store``；面板操作人按 Master 处理，
平台由面板选择，所以可以跨平台操作。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..context import QQ_PLATFORM, normalize_platform
from ..host.accounts import account_key
from ..host.notify import load_settings
from ..host.perm import GLOBAL_SCOPE, permission_store, superuser_ids
from .auth import read_json

if TYPE_CHECKING:
    from .service import WebUIService

KINDS = ("user", "group")


def _text(body: dict[str, Any], name: str, label: str, limit: int) -> str:
    value = body.get(name, "")
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f"{label}必须是不超过 {limit} 个字符的文本")
    return value.strip()


class BlacklistPanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (("/api/manage/blacklist", ["GET", "POST"], self.blacklist),)

    async def _data(self) -> dict[str, Any]:
        names = await self.service._group_names()
        platforms = {QQ_PLATFORM}
        platforms.update(
            account_key(account)[0]
            for account in self.service.accounts.accounts.values()
        )

        def rows(kind: str) -> list[dict[str, Any]]:
            result = []
            for record in permission_store.blacklist(kind):
                platforms.add(record.platform)
                result.append(
                    {
                        "platform": record.platform,
                        "id": record.target_id,
                        "name": names.get(record.target_id, "")
                        if kind == "group" and record.platform == QQ_PLATFORM
                        else "",
                        "reason": record.reason,
                        "operator_id": record.operator_id,
                        "created_at": record.created_at.isoformat()
                        if record.created_at
                        else None,
                    }
                )
            return result

        users, groups = rows("user"), rows("group")
        return {
            "ready": permission_store.ready,
            "users": users,
            "groups": groups,
            "platforms": sorted(platforms),
        }

    async def _refusal(self, kind: str, platform: str, target: str) -> str | None:
        if kind == "user":
            if target in superuser_ids(platform):
                return "无法拉黑 Master"
            if permission_store.granted(platform, GLOBAL_SCOPE, target):
                return "无法拉黑 BOT 管理员，请先撤销其 BOT 管理"
            return None
        settings = await load_settings()
        if platform == QQ_PLATFORM and settings.mode == "group":
            if target == settings.group:
                return "这是通知群，不能拉黑"
        return None

    async def blacklist(self, request: Request) -> JSONResponse:
        async def action():
            if request.method == "POST":
                if not permission_store.ready:
                    raise RuntimeError("权限状态尚未就绪")
                body = await read_json(request)
                if not set(body) <= {"kind", "platform", "id", "blacklisted", "reason"}:
                    raise ValueError("未知的黑名单参数")
                kind = body.get("kind")
                if kind not in KINDS:
                    raise ValueError("kind 必须是 user 或 group")
                if type(body.get("blacklisted")) is not bool:
                    raise ValueError("blacklisted 必须是布尔值")
                platform = normalize_platform(_text(body, "platform", "平台", 128))
                target = _text(body, "id", "ID", 128)
                reason = _text(body, "reason", "原因", 200)
                if not platform or not target:
                    raise ValueError("平台和 ID 不能为空")
                request.state.webui_action = (
                    f"blacklist-{'add' if body['blacklisted'] else 'remove'} {kind}"
                )
                if body["blacklisted"]:
                    refusal = await self._refusal(kind, platform, target)
                    if refusal:
                        raise PermissionError(refusal)
                    await permission_store.add_blacklist(
                        kind, platform, target, reason=reason, operator_id="webui"
                    )
                else:
                    await permission_store.remove_blacklist(kind, platform, target)
            return await self._data()

        return await self.service._manage(request, action)


__all__ = ["BlacklistPanel"]
