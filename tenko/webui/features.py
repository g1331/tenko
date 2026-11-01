"""面板功能开关与插件启停：各插件在群里的开关状态、维护、受控覆盖和启停。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..context import group_key, group_reference
from ..host.plugins.runtime import PluginRuntime
from ..host.sandbox import SANDBOX_GROUP_ID
from .auth import read_json

if TYPE_CHECKING:
    from .service import WebUIService


class FeaturePanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (
            ("/api/manage/plugins/{plugin}", ["POST"], self.manage_plugin),
            ("/api/manage/features/{plugin}", ["POST"], self.manage_feature),
        )

    def _plugin_infos(self) -> tuple[Any, ...]:
        runtime = self.service.plugin_runtime or PluginRuntime()
        return tuple(runtime.discover())

    @staticmethod
    def _empty_feature_state() -> dict[str, Any]:
        return {"maintenance": False, "groups": {}}

    def _feature_states_from_records(
        self, records: Iterable[Any]
    ) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for record in records:
            plugin_name = str(record.plugin_name)
            state = states.setdefault(plugin_name, self._empty_feature_state())
            group_id = record.group_id
            if group_id is None:
                state["maintenance"] = bool(record.maintenance)
                if record.enabled is not None:
                    state["global_enabled"] = bool(record.enabled)
            elif record.enabled is not None:
                key = group_key((record.platform, group_id))
                state["groups"][group_reference(key)] = bool(record.enabled)
        return states

    def _feature_states_from_service(self) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for plugin_name, value in self.service.feature_service.state.items():
            state = self._empty_feature_state()
            state["maintenance"] = bool(value.get("maintenance", False))
            state["groups"] = {
                str(group_id): bool(enabled)
                for group_id, enabled in value.get("groups", {}).items()
            }
            if "global_enabled" in value:
                state["global_enabled"] = bool(value["global_enabled"])
            states[str(plugin_name)] = state
        return states

    @staticmethod
    def _effective_enabled(
        state: Mapping[str, Any], group_id: str, default_enabled: bool
    ) -> bool:
        if state.get("maintenance"):
            return False
        global_enabled = state.get("global_enabled")
        if global_enabled is not None:
            return bool(global_enabled)
        if group_id in state.get("groups", {}):
            return bool(state["groups"][group_id])
        return default_enabled

    @staticmethod
    def _plugin_scope(info: object) -> str:
        metadata = getattr(getattr(info, "native_plugin", None), "metadata", None)
        return str(getattr(metadata, "feature_scope", "group"))

    def _plugin(self, name: str):
        runtime = self.service.plugin_runtime
        if runtime is None:
            raise RuntimeError("插件运行时尚未就绪")
        info = next((info for info in runtime.discover() if info.name == name), None)
        if info is None:
            raise KeyError(name)
        if runtime.is_protected(info):
            raise PermissionError("控制平面插件不能通过面板停用或修改开关")
        return info

    async def data(self) -> dict[str, Any]:
        service = self.service
        feature_service = service.feature_service
        if service.feature_repository is None:
            states = self._feature_states_from_service()
        else:
            records = await service.feature_repository.list_states()
            states = self._feature_states_from_records(records)

        infos = self._plugin_infos()
        plugin_names = [str(getattr(info, "name", info)) for info in infos]
        plugin_names.extend(name for name in states if name not in plugin_names)
        group_ids = list(service.accounts.group_ids)
        for state in states.values():
            for group_id in state["groups"]:
                # 沙盒群的开关在沙盒里用 /开启、/关闭 调整，不出现在开关页。
                if group_id not in group_ids and group_id != SANDBOX_GROUP_ID:
                    group_ids.append(group_id)

        default_enabled = bool(getattr(feature_service, "default_enabled", True))
        if not bool(getattr(feature_service, "ready", True)):
            default_enabled = False
        features = []
        for plugin_name in plugin_names:
            state = states.get(plugin_name, self._empty_feature_state())
            metadata_name = plugin_name
            scope = "group"
            source = "builtin"
            description = ""
            icon = None
            info = next(
                (
                    candidate
                    for candidate in infos
                    if str(getattr(candidate, "name", candidate)) == plugin_name
                ),
                None,
            )
            if info is not None:
                metadata_name = str(getattr(info, "display_name", plugin_name))
                scope = self._plugin_scope(info)
                source = str(getattr(info, "source", "builtin"))
                metadata = getattr(
                    getattr(info, "native_plugin", None), "metadata", None
                )
                description = str(getattr(metadata, "description", None) or "")
                icon = service.market.icon_of(plugin_name, source)

            # 插件可以声明“新群默认关闭”（declare(default_enabled=False)）；受控插件
            # 也默认关闭（#234）。
            controlled = feature_service.is_controlled(plugin_name)
            plugin_default = (
                default_enabled
                and not controlled
                and getattr(feature_service, "plugin_defaults", {}).get(
                    plugin_name, True
                )
            )
            groups = {
                group_id: self._effective_enabled(state, group_id, plugin_default)
                for group_id in group_ids
            }
            runtime = service.plugin_runtime
            features.append(
                {
                    "plugin": plugin_name,
                    "name": metadata_name,
                    "description": description,
                    "icon": icon,
                    "scope": scope,
                    "source": source,
                    "local": bool(getattr(info, "is_local", False)),
                    "controlled": controlled,
                    "controlled_declared": plugin_name in feature_service.controlled,
                    "protected": bool(
                        info is not None
                        and runtime is not None
                        and getattr(runtime, "is_protected", lambda _: False)(info)
                    ),
                    "loaded": bool(
                        info is not None
                        and runtime is not None
                        and getattr(runtime, "is_loaded", lambda _: False)(info)
                    ),
                    "enabled": bool(
                        info is not None
                        and runtime is not None
                        and getattr(runtime, "is_enabled", lambda _: False)(info)
                    ),
                    "maintenance": bool(state["maintenance"]),
                    "global_enabled": state.get("global_enabled"),
                    "groups": groups,
                }
            )
        return {"features": features, "group_names": await service.group_names()}

    async def features_view(self, request: Request) -> JSONResponse:
        del request
        return await self.service.read("features", self.data)

    async def manage_plugin(self, request: Request) -> JSONResponse:
        service = self.service

        async def change():
            repository = service.require_management().repository
            info = self._plugin(request.path_params["plugin"])
            runtime = service.plugin_runtime
            body = await read_json(request)
            if (
                set(body) != {"enabled", "confirm"}
                or type(body["enabled"]) is not bool
                or body["confirm"] is not True
            ):
                raise ValueError("请确认插件启停操作")
            before = runtime.is_enabled(info)
            enabled = body["enabled"]
            restart_reason = None
            request.state.webui_action = "enable" if enabled else "disable"
            if info.source == "external":
                if enabled and (reason := service.market.blocked_reason(info.name)):
                    raise PermissionError(f"插件已被插件目录封禁：{reason}")
                # 扩展插件默认关闭，状态由 PluginRuntime 写入状态文件。
                if not await runtime.set_enabled(info, enabled):
                    raise RuntimeError("插件当前未能切换状态")
                if enabled:
                    restart_reason = await service.market.manager.load_now(
                        runtime, info
                    )
            else:
                disabled = set(await repository.setting("disabled-plugins") or [])
                if enabled:
                    disabled.discard(info.name)
                else:
                    disabled.add(info.name)
                if not await runtime.set_enabled(info, enabled):
                    raise RuntimeError("插件当前未加载，无法切换状态")
                try:
                    await repository.save_setting("disabled-plugins", sorted(disabled))
                except Exception:
                    await runtime.set_enabled(info, before)
                    raise
            return {
                "plugin": info.name,
                "enabled": enabled,
                "restart_required": runtime.restart_required(info),
                "restart_reason": restart_reason,
            }

        return await service.manage(request, change)

    async def manage_feature(self, request: Request) -> JSONResponse:
        service = self.service
        feature_service = service.feature_service

        async def change():
            service.require_management()
            if not feature_service.ready:
                raise RuntimeError("功能状态尚未就绪")
            info = self._plugin(request.path_params["plugin"])
            body = await read_json(request)
            if set(body) == {"controlled"}:
                # Master 覆盖插件声明的受控状态；null 恢复插件声明。
                value = body["controlled"]
                if value is not None and type(value) is not bool:
                    raise ValueError("controlled 必须是布尔值或 null")
                overrides = dict(feature_service.controlled_overrides)
                if value is None:
                    overrides.pop(info.name, None)
                else:
                    overrides[info.name] = value
                await service.require_management().repository.save_setting(
                    "controlled-plugins", overrides
                )
                feature_service.controlled_overrides = overrides
                return {
                    "plugin": info.name,
                    "controlled": feature_service.is_controlled(info.name),
                }
            if set(body) == {"maintenance"} and type(body["maintenance"]) is bool:
                feature_service.set_maintenance(info.name, body["maintenance"])
            elif set(body) == {"group_id", "enabled"} and body["group_id"] is None:
                if body["enabled"] is None:
                    feature_service.reset_global(info.name)
                elif type(body["enabled"]) is bool:
                    feature_service.set_global_enabled(info.name, body["enabled"])
                else:
                    raise ValueError("全局开关必须为布尔值或 null")
            else:
                if (
                    set(body) != {"group_id", "enabled"}
                    or type(body["enabled"]) is not bool
                ):
                    raise ValueError("必须指定群 ID 与布尔开关值")
                if self._plugin_scope(info) == "global":
                    raise ValueError("此插件使用全局开关")
                group_id = body["group_id"]
                if (
                    not isinstance(group_id, str)
                    or group_id not in service.accounts.group_ids
                ):
                    raise ValueError("群 ID 不在已知路由中")
                state = feature_service.state.get(info.name, {})
                if state.get("maintenance") or "global_enabled" in state:
                    raise RuntimeError("请先在插件页解除维护或全局覆盖，再修改群级开关")
                feature_service.set_enabled(info.name, group_id, body["enabled"])
            await feature_service.persist_state()
            return {
                "plugin": info.name,
                "state": dict(feature_service.state.get(info.name, {})),
            }

        return await service.manage(request, change)


__all__ = ["FeaturePanel"]
