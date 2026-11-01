"""面板全局设置：新群默认开关、好友申请处理方式和通知。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from ..host.friend_requests import (
    MODE_LABELS,
    get_mode as get_friend_request_mode,
    set_mode as set_friend_request_mode,
)
from ..host.notify import (
    load_settings as load_notify_settings,
    save_settings as save_notify_settings,
)
from .auth import ApiResponse, read_json
from .contracts import NotifySettings, SettingsData

if TYPE_CHECKING:
    from .service import WebUIService


class SettingsPanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (("/api/manage/settings", ["GET", "POST"], self.settings),)

    async def settings(self, request: Request) -> ApiResponse[SettingsData]:
        service = self.service

        async def action() -> SettingsData:
            repository = service.require_repository()
            if request.method == "POST":
                request.state.webui_action = "settings"
                body = await read_json(request)
                if not body or not set(body) <= {
                    "default_enabled",
                    "friend_request_mode",
                    "notify",
                }:
                    raise ValueError(
                        "只能设置 default_enabled、friend_request_mode 或 notify"
                    )
                if (
                    "default_enabled" in body
                    and type(body["default_enabled"]) is not bool
                ):
                    raise ValueError("default_enabled 必须是布尔值")
                mode = body.get("friend_request_mode", "admins")
                if not isinstance(mode, str) or mode not in MODE_LABELS:
                    raise ValueError("friend_request_mode 必须是 off、admins 或 all")
                if not isinstance(body.get("notify", {}), dict):
                    raise ValueError("notify 必须是对象")
                if "notify" in body:
                    await save_notify_settings(body["notify"], repository)
                if "default_enabled" in body:
                    await repository.save_setting(
                        "feature-default", body["default_enabled"]
                    )
                    service.feature_service.default_enabled = body["default_enabled"]
                if "friend_request_mode" in body:
                    await set_friend_request_mode(
                        repository, body["friend_request_mode"]
                    )
            notify = await load_notify_settings(repository)
            notify_data: NotifySettings = {
                "mode": notify.mode,
                "group": notify.group,
                "account": notify.account,
            }
            return {
                "default_enabled": service.feature_service.default_enabled,
                "stored": await repository.setting("feature-default") is not None,
                "friend_request_mode": await get_friend_request_mode(repository),
                "notify": notify_data,
            }

        return await service.manage(request, action)


__all__ = ["SettingsPanel"]
