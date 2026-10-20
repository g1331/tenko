from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from arclet.entari.exceptions import RegisterNotInPluginError as PluginInterfaceError
from arclet.entari.plugin import (
    Plugin,
    PluginMetadata,
    disable_plugin,
    enable_plugin,
    get_plugin_commands,
    get_plugins,
    load_plugin,
    unload_plugin,
    plugin_service,
)
from loguru import logger

from ..context import MessageContext

_DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins"
# 第三方插件入口组；不能用 Entari 的 ``entari.plugin``，它会被无条件自动加载。
EXTERNAL_ENTRY_POINT_GROUP = "tenko.plugins"
_DEFAULT_STATE_FILE = Path(".tenko/plugins/state.json")
_PLUGIN_ID = re.compile(r"[a-z][a-z0-9_]*")
_MODULE_PATH = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")
_LEGACY_PLUGIN_NAMESPACES = (
    "modules.required",
    "modules.self_contained",
    "modules.third_party",
)

__all__ = [
    "Plugin",
    "PluginInfo",
    "PluginInterfaceError",
    "PluginMetadata",
    "PluginRuntime",
    "external_plugin_for_module",
]


def external_plugin_for_module(module_name: str) -> str | None:
    """按模块名返回所属第三方插件的 ID。

    插件的入口模块及其子模块都归它所有；多个入口互为前缀时取最长的。
    不属于任何第三方插件（包括被发现阶段拒绝的入口）时返回 ``None``。
    """

    best: Any = None
    for entry in entry_points(group=EXTERNAL_ENTRY_POINT_GROUP):
        if not (
            _PLUGIN_ID.fullmatch(entry.name) and _MODULE_PATH.fullmatch(entry.value)
        ):
            continue
        if module_name != entry.value and not module_name.startswith(f"{entry.value}."):
            continue
        if best is None or len(entry.value) > len(best.value):
            best = entry
    if best is None:
        return None
    return best.name


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """插件目录项及其可传给 Entari 的导入名。"""

    name: str
    # 第三方插件在发现阶段不导入模块，不提供路径。
    path: Path | None
    is_package: bool
    qualified_name: str
    # builtin: tenko/plugins 下的内置插件；external: tenko.plugins 入口声明的
    # 第三方包；unmanaged: Entari 里已注册命令但未在 Tenko 登记的原生插件。
    source: Literal["builtin", "external", "unmanaged"] = "builtin"

    @property
    def display_name(self) -> str:
        """返回已加载插件的元数据名称，未加载时回退到目录名。"""

        names = set(self.lookup_names)
        native_plugin = next(
            (plugin for plugin in get_plugins(subplugged=True) if plugin.id in names),
            None,
        )
        metadata_name = getattr(getattr(native_plugin, "metadata", None), "name", None)
        return str(metadata_name) if metadata_name else self.name

    @property
    def rate_weight(self) -> int | None:
        """插件通过 ``declare`` 声明的限流权重；未声明时为 ``None``。"""

        native_plugin = PluginRuntime._native_plugin(self)
        return getattr(getattr(native_plugin, "metadata", None), "rate_weight", None)

    @property
    def group_level(self) -> int | None:
        """插件要求的群等级（``declare(level=...)`` 或内置插件直接设置）；默认 ``None``。"""

        native_plugin = PluginRuntime._native_plugin(self)
        return getattr(getattr(native_plugin, "metadata", None), "group_level", None)

    @property
    def lookup_names(self) -> tuple[str, ...]:
        names = [self.name, self.qualified_name]
        if self.source == "builtin":
            names.extend(
                f"{namespace}.{self.name}" for namespace in _LEGACY_PLUGIN_NAMESPACES
            )
        return tuple(dict.fromkeys(names))


class PluginRuntime:
    """把 Tenko 插件目录和第三方插件入口适配到 Entari 原生插件机制。"""

    def __init__(
        self,
        plugin_dir: str | Path | None = None,
        *,
        namespace: str = "tenko.plugins",
        state_file: str | Path | None = None,
    ) -> None:
        self.plugin_dir = Path(plugin_dir or _DEFAULT_PLUGIN_DIR)
        self.namespace = namespace
        # 第三方插件默认关闭，启用状态保存在这个 JSON 文件里：加载插件发生在
        # 数据库可用之前，所以不能放进数据库设置。
        self.state_file = Path(state_file or _DEFAULT_STATE_FILE)
        self._discovered: dict[str, PluginInfo] = {}

    def discover(self) -> tuple[PluginInfo, ...]:
        """发现内置插件和第三方插件入口，不导入任何插件模块。"""

        discovered = self._discover_builtin()
        discovered.update(self._discover_external(discovered))
        self._discovered = discovered
        return tuple(discovered.values())

    def _discover_builtin(self) -> dict[str, PluginInfo]:
        if not self.plugin_dir.exists():
            return {}
        if not self.plugin_dir.is_dir():
            raise NotADirectoryError(f"插件目录不是目录: {self.plugin_dir}")

        discovered: dict[str, PluginInfo] = {}
        for path in sorted(self.plugin_dir.iterdir(), key=lambda item: item.name):
            if path.name.startswith("_"):
                continue
            is_package = path.is_dir() and (path / "__init__.py").is_file()
            if not is_package and not (path.is_file() and path.suffix == ".py"):
                continue
            name = path.name if is_package else path.stem
            if name in discovered:
                raise ValueError(f"插件名称重复: {name}")
            qualified_name = f"{self.namespace}.{name}" if self.namespace else name
            discovered[name] = PluginInfo(name, path, is_package, qualified_name)
        return discovered

    @staticmethod
    def _discover_external(builtin: Mapping[str, PluginInfo]) -> dict[str, PluginInfo]:
        """按入口名登记第三方插件；非法、与内置冲突或重复的入口一律跳过。"""

        candidates: dict[str, list[Any]] = {}
        for entry in entry_points(group=EXTERNAL_ENTRY_POINT_GROUP):
            dist = getattr(getattr(entry, "dist", None), "name", None) or "未知"
            if not _PLUGIN_ID.fullmatch(entry.name):
                logger.error(
                    "忽略第三方插件入口 {!r}（分发包 {}）：插件 ID 须匹配 [a-z][a-z0-9_]*",
                    entry.name,
                    dist,
                )
            elif not _MODULE_PATH.fullmatch(entry.value):
                logger.error(
                    "忽略第三方插件 {}（分发包 {}）：入口值 {!r} 必须是纯模块路径",
                    entry.name,
                    dist,
                    entry.value,
                )
            elif entry.name in builtin:
                logger.error(
                    "忽略第三方插件 {}（分发包 {}）：与内置插件同名", entry.name, dist
                )
            else:
                candidates.setdefault(entry.name, []).append(entry)

        entari_modules = {entry.module for entry in entry_points(group="entari.plugin")}
        external: dict[str, PluginInfo] = {}
        for name, entries in candidates.items():
            if len(entries) > 1:
                dists = [getattr(e.dist, "name", None) or "未知" for e in entries]
                logger.error("忽略重复的第三方插件 ID {}（分发包 {}）", name, dists)
                continue
            module = entries[0].value
            if module in entari_modules:
                logger.warning(
                    "第三方插件 {} 同时声明了 entari.plugin 入口，Entari 会无视 "
                    "Tenko 的启用状态自动加载它",
                    name,
                )
            external[name] = PluginInfo(name, None, False, module, "external")
        return external

    def _read_state(self) -> dict[str, Any]:
        """读取状态文件；缺失视为空，损坏时记录错误并按“全部未启用”处理。"""

        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            enabled = state["enabled"]
            if not isinstance(enabled, list) or not all(
                isinstance(item, str) for item in enabled
            ):
                raise TypeError("enabled 必须是字符串列表")
            return state
        except FileNotFoundError:
            return {"enabled": []}
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.error(
                "第三方插件状态文件无效，视为全部未启用: {} ({}: {})",
                self.state_file,
                type(error).__name__,
                error,
            )
            return {"enabled": []}

    def _enabled_external_ids(self) -> frozenset[str]:
        return frozenset(self._read_state()["enabled"])

    def _write_external_enabled(self, name: str, enabled: bool) -> None:
        state = self._read_state()
        ids = set(state["enabled"])
        if enabled:
            ids.add(name)
        else:
            ids.discard(name)
        state["enabled"] = sorted(ids)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_name(f"{self.state_file.name}.tmp")
        temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.state_file)

    def _info(self, plugin: str | PluginInfo) -> PluginInfo:
        if isinstance(plugin, PluginInfo):
            return plugin
        if not self._discovered:
            self.discover()
        if plugin in self._discovered:
            return self._discovered[plugin]
        for info in self._discovered.values():
            if plugin in info.lookup_names:
                return info
        raise KeyError(f"未发现插件: {plugin}")

    def is_enabled(
        self,
        plugin: str | PluginInfo,
        context: MessageContext | str | int | None = None,
        *,
        group_id: str | int | None = None,
    ) -> bool:
        """查询当前插件是否仍被 Entari 注册并启用。"""

        del context, group_id
        info = self._info(plugin)
        native_plugin = self._native_plugin(info)
        if native_plugin is None:
            # 第三方插件默认关闭；未加载时以状态文件里的意愿为准。
            return (
                info.source != "external" or info.name in self._enabled_external_ids()
            )
        return native_plugin.is_available

    def restart_required(self, plugin: str | PluginInfo) -> bool:
        """第三方插件已被启用但尚未加载，需要重启后才会生效。"""

        info = self._info(plugin)
        return (
            info.source == "external"
            and info.name in self._enabled_external_ids()
            and self._native_plugin(info) is None
        )

    @staticmethod
    def _native_plugin(info: PluginInfo) -> Plugin | None:
        names = set(info.lookup_names)
        return next(
            (plugin for plugin in get_plugins(subplugged=True) if plugin.id in names),
            None,
        )

    @property
    def loaded_plugins(self) -> Mapping[str, Plugin]:
        if not self._discovered:
            self.discover()
        loaded = {
            info.name: plugin
            for info in self._discovered.values()
            if (plugin := self._native_plugin(info)) is not None
        }
        return MappingProxyType(loaded)

    def is_protected(self, plugin: str | PluginInfo) -> bool:
        """同时检查原生启停会级联影响的插件，防止间接关闭控制平面。"""

        info = self._info(plugin)
        if info.source != "builtin":
            return False
        if info.name == "feature_manager":
            return True
        native = self._native_plugin(info)
        pending = [native] if native is not None else []
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            if "host" in getattr(current.metadata, "classifier", ()):
                return True
            related = set(current.subplugins) | set(
                plugin_service.referents.get(current.path, ())
            )
            pending.extend(
                plugin_service.plugins[name]
                for name in related
                if name in plugin_service.plugins and name not in seen
            )
        return False

    def is_loaded(self, plugin: str | PluginInfo) -> bool:
        return self._native_plugin(self._info(plugin)) is not None

    def command_owner(self, command: str) -> PluginInfo | None:
        """按 Entari 的插件命令登记，返回命令名所属插件的目录项。

        ``command`` 是 Entari 已解析命令的 Alconna 名称（``Alconna.command``）；
        归属取自 ``get_plugin_commands``，不再按消息文本重新匹配，因此与 Entari
        对 @bot、前导空白、昵称前缀和 shortcut 的识别保持一致。

        命令属于 Tenko 未登记的原生插件时返回 ``source="unmanaged"`` 的目录项，
        由 ``CommandPolicy`` 拒绝执行（fail-closed）。找不到登记时返回 ``None``。
        """

        if not self._discovered:
            self.discover()
        for native_plugin in get_plugins(subplugged=True):
            try:
                registered = tuple(get_plugin_commands(native_plugin) or ())
            except (PluginInterfaceError, TypeError, AttributeError):
                continue
            if all(str(name) != command for _prefixes, name in registered):
                continue
            for info in self._discovered.values():
                # 子插件的 ID 以父插件模块名为前缀。
                if native_plugin.id in info.lookup_names or native_plugin.id.startswith(
                    f"{info.qualified_name}."
                ):
                    return info
            return PluginInfo(
                native_plugin.id, None, False, native_plugin.id, "unmanaged"
            )
        return None

    async def load(
        self,
        plugin: str | PluginInfo,
        *,
        config: dict[str, Any] | None = None,
    ) -> Plugin | None:
        """通过 Entari 加载插件。"""

        info = self._info(plugin)
        result = (
            load_plugin(info.qualified_name)
            if config is None
            else load_plugin(info.qualified_name, config)
        )
        # Entari 0.19 起返回结果对象：失败时 ``.plugin`` 会抛错，错误在 ``.error``。
        if not result:
            logger.error(
                "插件加载失败: {} ({}: {})",
                info.name,
                type(result.error).__name__,
                result.error,
            )
            return None
        return result.plugin

    async def load_all(
        self,
        configs: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        external_configs: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> Mapping[str, Plugin]:
        """发现并加载插件，可为指定插件传入 Entari 配置。

        第三方插件只加载状态文件里已启用的，配置取 ``external_configs``（即
        ``[plugin_config.<id>]``）；加载失败只记录日志，不影响启动。
        显式配置的插件先于其他插件加载，避免某个插件的依赖导入以默认
        配置抢先创建它，之后无法再替换已注册的 Entari 服务实例。
        """

        configs = {} if configs is None else configs
        infos = [info for info in self.discover() if info.source == "builtin"]
        configured_names = set(configs)
        for info in infos:
            if info.name not in configured_names:
                continue
            config = configs.get(info.name)
            await self.load(info, config=dict(config))
        for info in infos:
            if info.name in configured_names:
                continue
            await self.load(info)
        enabled = self._enabled_external_ids()
        external_configs = {} if external_configs is None else external_configs
        for info in self._discovered.values():
            if info.source != "external" or info.name not in enabled:
                continue
            config = external_configs.get(info.name)
            try:
                loaded = await self.load(
                    info, config=None if config is None else dict(config)
                )
            except Exception:
                logger.exception("第三方插件加载失败: {}", info.name)
            else:
                # Entari 导入失败（含配置校验失败）时 ``load`` 记录原因并返回 None。
                if loaded is None:
                    logger.error("第三方插件加载失败: {}", info.name)
        return self.loaded_plugins

    def unload(self, plugin: str | PluginInfo) -> bool:
        """通过 Entari 卸载插件。"""

        return unload_plugin(self._info(plugin).qualified_name)

    async def reload(
        self,
        plugin: str | PluginInfo,
        *,
        config: dict[str, Any] | None = None,
    ) -> Plugin | None:
        """按 Entari 的原生热重载方式卸载后重新加载。"""

        info = self._info(plugin)
        unload_plugin(info.qualified_name)
        return await self.load(info, config=config)

    async def enable(self, plugin: str | PluginInfo) -> bool:
        return await enable_plugin(self._info(plugin).qualified_name)

    async def disable(self, plugin: str | PluginInfo) -> bool:
        return await disable_plugin(self._info(plugin).qualified_name)

    async def set_enabled(self, plugin: str | PluginInfo, enabled: bool) -> bool:
        """启停插件；第三方插件的意愿同时写入状态文件。

        第三方插件未加载时只更新状态文件，启用要等重启后才加载，调用方用
        ``restart_required`` 判断是否需要提示重启。
        """

        if type(enabled) is not bool:
            raise TypeError("enabled 必须是布尔值")
        info = self._info(plugin)
        if info.source == "external":
            if self._native_plugin(info) is not None:
                result = await (self.enable(info) if enabled else self.disable(info))
                if not result:
                    return False
            self._write_external_enabled(info.name, enabled)
            return True
        return await (self.enable(info) if enabled else self.disable(info))
