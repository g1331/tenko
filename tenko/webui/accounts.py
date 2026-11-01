"""面板账号与群响应策略：账号总览、账号管理动作和群响应策略设置。"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..context import QQ_PLATFORM, group_key
from ..host.accounts import account_reference
from ..host.perm import Permission, permission_store
from .auth import read_json

if TYPE_CHECKING:
    from .service import WebUIService


class AccountPanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (
            ("/api/manage/accounts", ["GET"], self.managed_accounts),
            (
                "/api/manage/accounts/{platform}/{account_id}/{action}",
                ["POST"],
                self.manage_account,
            ),
            ("/api/manage/groups/{group_id}/response", ["POST"], self.manage_response),
        )

    @staticmethod
    def _account_name(account: object) -> str | None:
        user = getattr(getattr(account, "self_info", None), "user", None)
        return str(user.name) if user is not None and user.name else None

    def _account_state(self, key: tuple[str, str], group_id: str) -> str:
        """账号在群内的状态，优先级与选路一致：停用 > 离线 > 禁言。"""

        accounts = self.service.accounts
        if not accounts.is_enabled(key):
            return "disabled"
        if not accounts.is_online(key):
            return "offline"
        if accounts.is_muted(key, group_id):
            return "muted"
        return "available"

    def _group_role(self, key: tuple[str, str], group_id: str) -> str | None:
        # 身份只在执行管理动作时查询过才知道。
        level = self.service.accounts.group_permission(key, group_id)
        if level is None:
            return None
        if level >= Permission.GroupOwner:
            return "owner"
        return "admin" if level >= Permission.GroupAdmin else "member"

    def data(self) -> dict[str, Any]:
        registry = self.service.accounts
        groups = []
        for group_id in registry.group_ids:
            strategy = registry.response_type_for_group(group_id)
            target = registry.deterministic_account_for_group(group_id)
            members = []
            for key in registry.group_members(group_id):
                ref = account_reference(key)
                until = registry.mute_until(key, group_id)
                members.append(
                    {
                        "ref": ref,
                        "id": key[1],
                        "platform": key[0],
                        "name": self._account_name(registry.get(key)),
                        "state": self._account_state(key, group_id),
                        "role": self._group_role(key, group_id),
                        "muted_until": until.astimezone(UTC).isoformat()
                        if until
                        else None,
                    }
                )
            groups.append(
                {
                    "group_id": group_id,
                    "strategy": strategy,
                    "deterministic_account": target,
                    "silent_reason": "blacklisted"
                    if permission_store.blacklisted("group", *group_key(group_id))
                    else registry.silent_reason(group_id),
                    "accounts": members,
                }
            )
        accounts = []
        for account_id, account in registry.accounts.items():
            platform = getattr(account, "platform", None)
            if not platform:
                platform = getattr(
                    getattr(account, "self_info", None), "platform", None
                )
            accounts.append(
                {
                    "id": str(account.self_id),
                    "ref": str(account_id),
                    "platform": str(platform or "unknown"),
                    "name": self._account_name(account),
                    "online": bool(registry.is_available(account_id)),
                    "group_count": len(registry.groups_for_account(account_id)),
                }
            )
        return {"accounts": accounts, "groups": groups}

    async def accounts_view(self, request: Request) -> JSONResponse:
        del request
        return await self.service.read("accounts", self.data)

    async def managed_accounts(self, request: Request) -> JSONResponse:
        service = self.service

        async def read():
            management = service.require_management()
            rows = management.list_accounts()
            for row in rows:
                key = (row["platform"], row["id"])
                account = service.accounts.get(key)
                row.update(
                    online=service.accounts.is_online(key),
                    available=service.accounts.is_available(key),
                    group_count=len(service.accounts.groups_for_account(key)),
                    groups=list(service.accounts.groups_for_account(key)),
                    connected=bool(
                        service.connection
                        and key[0] == QQ_PLATFORM
                        and key[1] in service.connection.adapter.connections
                    ),
                    name=str(
                        getattr(getattr(account, "self_info", None), "user", None).name
                        or row["id"]
                    )
                    if getattr(getattr(account, "self_info", None), "user", None)
                    else row["id"],
                    manageable=key[0] == QQ_PLATFORM,
                )
            from ..host.actions import ActionCapability

            return {
                "accounts": rows,
                "capabilities": [item.value for item in ActionCapability],
            }

        return await service.manage(request, read)

    async def manage_account(self, request: Request) -> JSONResponse:
        service = self.service

        async def change():
            management = service.require_management()
            platform, identifier, action = (
                request.path_params[name]
                for name in ("platform", "account_id", "action")
            )
            if any(
                not value or len(value) > 128 or any(ord(c) < 32 for c in value)
                for value in (platform, identifier)
            ):
                raise ValueError("平台和账户 ID 必须是 1–128 字的有效标识")
            key = (platform, identifier)
            if not management.contains(key):
                raise KeyError(key)
            # shortcut: QQ 号目前只经 OneBot 反向连接接入；接入 Milky 等协议端时改为按 adapter 判断。
            if platform != QQ_PLATFORM or service.connection is None:
                raise RuntimeError("当前协议适配器尚未提供账户管理能力")
            body = await read_json(request)
            if action == "preferences":
                if set(body) - {"alias", "capabilities"}:
                    raise ValueError("仅支持别名和能力覆盖")
                await management.update(key, body)
            elif action in {"enable", "disable", "kick", "forget"}:
                if set(body) != {"confirm"} or body["confirm"] is not True:
                    raise ValueError("请确认账户操作")
                if action == "forget":
                    if (
                        identifier in service.connection.adapter.connections
                        or service.accounts.is_online(key)
                    ):
                        raise RuntimeError(
                            "账户仍连接中，请先停用或在协议端断开，再移除记录"
                        )
                    await management.forget(key)
                elif action == "kick":
                    if not await service.connection.adapter.disconnect(identifier):
                        raise RuntimeError("账户当前没有连接")
                else:
                    await management.update(key, {"enabled": action == "enable"})
                    if action == "disable":
                        await service.connection.adapter.disconnect(identifier)
            else:
                raise ValueError("未知账户操作")
            return {"action": action, "platform": platform, "id": identifier}

        return await service.manage(request, change)

    async def manage_response(self, request: Request) -> JSONResponse:
        """设置群响应策略；定向时 account 为 null 表示按绑定顺序的默认账号。"""

        accounts = self.service.accounts

        async def change():
            group_id = request.path_params["group_id"]
            if group_id not in accounts.group_ids:
                raise ValueError("群 ID 不在已知路由中")
            body = await read_json(request)
            strategy, account = body.get("strategy"), body.get("account")
            if set(body) != {"strategy", "account"} or strategy not in (
                "random",
                "deterministic",
            ):
                raise ValueError("strategy 必须是 random 或 deterministic")
            if account is not None and (
                strategy != "deterministic" or not isinstance(account, str)
            ):
                raise ValueError("只有定向策略可以指定账号")
            if strategy == "deterministic":
                if account is None:
                    accounts.clear_deterministic_account(group_id)
                else:
                    # 先于改策略执行：账号未绑定到该群时抛 KeyError（404），不留半改状态。
                    accounts.set_deterministic_account(group_id, account)
            accounts.set_response_type(group_id, strategy)
            await accounts.persist_state()
            return {
                "group_id": group_id,
                "strategy": strategy,
                "deterministic_account": (
                    accounts.deterministic_account_for_group(group_id)
                ),
            }

        return await self.service.manage(request, change)


__all__ = ["AccountPanel"]
