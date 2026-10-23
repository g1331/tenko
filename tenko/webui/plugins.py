"""WebUI 的插件市场、已安装插件、插件配置和重启接口。

安装、更新、卸载、回滚都交给 :class:`PluginManager` 的事务执行，这里只把它们包成后台
任务供前端轮询。面板只能安装插件目录里的插件；未收录来源只能由 Master 在聊天里二次
确认安装，避免面板会话被盗后直接执行任意包。
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from ..host.plugins.docs import plugin_command_help, plugin_icon_path
from ..host.plugins.manager import (
    PluginChange,
    PluginManager,
    PluginManagerError,
    get_plugin_manager,
)
from ..host.plugins.registry import (
    Registry,
    RegistryError,
    RegistryPlugin,
    compatible,
    get_registry,
    installable,
    installed_notes,
    registry_updates,
    resume_source,
    source_for,
    why_incompatible,
)
from ..host.startup import _project_version
from .auth import WEBUI_PATH, error_response, read_json
from .upgrade import HostUpgrade

if TYPE_CHECKING:
    from .service import WebUIService

_ICON_NAME = re.compile(r"^[a-z][a-z0-9_]*-[0-9a-f]{8}\.png$")
_TASK_KEEP = 20
_ACTIONS = {"install", "update", "resume", "uninstall", "rollback", "sync"}
# 表单能编辑的 JSON Schema 类型；其余字段只读，请在配置文件里修改。
_EDITABLE = {"string", "integer", "number", "boolean"}
_REPO = "https://github.com/g1331/tenko"
_TENKO_LICENSE = "GPL-3.0"


def _http(url: object) -> str | None:
    """只把 http(s) 链接交给前端，插件元数据里的 javascript: 等一律丢掉。"""

    text = str(url or "").strip()
    return text if text.lower().startswith(("https://", "http://")) else None


def _link_kind(label: str, url: str) -> str:
    key = label.lower()
    if key in {"home", "homepage", "documentation", "docs"}:
        return "home"
    if key in {"repo", "repository", "source", "code"} or any(
        host in url
        for host in ("github.com", "gitlab.com", "gitee.com", "codeberg.org")
    ):
        return "repo"
    return "link"


def _tenko_release() -> tuple[str | None, str]:
    """内置插件随 Tenko 发布：返回版本和对应的 git tag（4.0.0rc21 → v4.0.0-rc21）。"""

    version = _project_version()
    matched = re.fullmatch(r"(\d+\.\d+\.\d+)-?((?:a|b|rc)\d+)?", version)
    if matched is None or version == "0.0.0":  # 0.0.0：读不到版本
        return None, "main"
    release, pre = matched.groups()
    return version, f"v{release}-{pre}" if pre else f"v{release}"


def _authors(value: object) -> list[str]:
    items = value if isinstance(value, list | tuple) else [value] if value else []
    names = [
        str(item.get("name") or "") if isinstance(item, dict) else str(item)
        for item in items
    ]
    return [name for name in names if name]


@dataclass
class PluginTask:
    id: str
    action: str
    plugin_id: str | None
    state: str = "running"  # running | succeeded | failed
    message: str = ""
    changes: list[dict[str, Any]] = field(default_factory=list)
    restored: bool | None = None
    # 更新和恢复：每个插件仍需重启的原因，None 表示已在运行中生效。
    live: dict[str, str | None] = field(default_factory=dict)
    restart_required: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


def _change(change: PluginChange) -> dict[str, Any]:
    return {
        "plugin_id": change.plugin_id,
        "dist": change.dist,
        "from": change.old_version,
        "to": change.new_version,
    }


def _versions(entry: RegistryPlugin) -> list[dict[str, Any]]:
    return [
        {
            "version": item.version,
            "requires_api": item.requires_api,
            "problem": why_incompatible(item),
            "tested_on": item.tested_on,
            "tested_at": item.tested_at,
        }
        for item in entry.versions[:10]
    ]


def _field_ok(schema: dict[str, Any], value: Any) -> bool:
    kind = schema.get("type")
    if "enum" in schema and value not in schema["enum"]:
        return False
    if kind == "boolean":
        return type(value) is bool
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return type(value) in (int, float)
    return isinstance(value, str)


class PluginMarket:
    """挂在 :class:`WebUIService` 上的一组接口。"""

    def __init__(
        self,
        service: WebUIService,
        *,
        registry: Registry | None = None,
        manager: PluginManager | None = None,
        updater: Any | None = None,
    ) -> None:
        self.service = service
        self._registry = registry
        self._manager = manager
        self._updater = updater
        self.tasks: dict[str, PluginTask] = {}
        self._running: asyncio.Task | None = None
        self._restarting = False
        self.upgrade = HostUpgrade(self)

    @property
    def registry(self) -> Registry:
        return self._registry or get_registry()

    @property
    def manager(self) -> PluginManager:
        return self._manager or get_plugin_manager()

    @property
    def updater(self) -> Any:
        if self._updater is not None:
            return self._updater
        from ..host.updater import get_upgrade_manager

        return get_upgrade_manager()

    def routes(self) -> tuple[tuple[str, list[str], Callable], ...]:
        return (
            ("/api/market", ["GET"], self.market_view),
            ("/api/market/icons/{name}", ["GET"], self.icon),
            ("/api/plugins/installed", ["GET"], self.installed_view),
            ("/api/plugins/{plugin}/detail", ["GET"], self.plugin_detail),
            ("/api/plugins/{plugin}/icon", ["GET"], self.plugin_icon),
            ("/api/manage/plugin-tasks", ["POST"], self.create_task),
            ("/api/manage/plugin-tasks/{id}", ["GET"], self.task_view),
            ("/api/manage/plugin-config/{plugin}", ["GET", "POST"], self.plugin_config),
            ("/api/manage/restart", ["POST"], self.restart),
            *self.upgrade.routes(),
        )

    # ------------------------------------------------------------------ 只读

    def _icon_url(self, entry: RegistryPlugin | None) -> str | None:
        if entry is None or self.registry.icon_path(entry) is None:
            return None
        return f"{WEBUI_PATH}/api/market/icons/{entry.icon.removeprefix('icons/')}"

    async def market_view(self, request: Request) -> JSONResponse:
        async def read():
            from tenko_api import __version__ as api_version

            refresh = request.query_params.get("refresh") == "1"
            try:
                index = await self.registry.load(refresh=refresh)
            except RegistryError as error:
                return {"available": False, "error": str(error), "plugins": []}
            installed = {r.plugin_id: r.version for r in self.manager.status()}
            plugins = []
            for entry in index.search():
                latest = compatible(entry)
                plugins.append(
                    {
                        "id": entry.id,
                        "name": entry.name,
                        "description": entry.description,
                        "authors": list(entry.authors),
                        "homepage": entry.homepage,
                        "license": entry.license,
                        "tags": list(entry.tags),
                        "tier": entry.tier,
                        "icon_url": self._icon_url(entry),
                        "source": entry.package or entry.git_url,
                        "source_type": entry.source_type,
                        "latest": latest.version if latest else None,
                        "problem": None
                        if latest
                        else (
                            why_incompatible(entry.versions[0])
                            if entry.versions
                            else "目录里还没有版本"
                        ),
                        "versions": _versions(entry),
                        "installed_version": installed.get(entry.id),
                        "blocked": index.blocked_reason(entry.id, entry.dist),
                    }
                )
            return {
                "available": True,
                "url": index.url,
                "host_api": api_version,
                "plugins": plugins,
            }

        return await self.service._read("market", read)

    async def icon(self, request: Request) -> Response:
        name = request.path_params["name"]
        path = self.registry.icon_dir / name
        if not _ICON_NAME.match(name) or not path.is_file():
            return error_response("not_found", "Not Found", 404)
        return FileResponse(path, media_type="image/png")

    def _info(self, plugin_id: str):
        runtime = self.service.plugin_runtime
        if runtime is None:
            return None
        return next((i for i in runtime.discover() if i.name == plugin_id), None)

    def icon_of(self, name: str, source: str) -> dict[str, Any]:
        """插件图标：内置的是单色 SVG（glyph，前端按主题着色），目录图标是 PNG。"""

        path = plugin_icon_path(name, source, self.registry)
        return {
            "url": f"{WEBUI_PATH}/api/plugins/{name}/icon",
            "glyph": path.suffix == ".svg",
        }

    async def plugin_icon(self, request: Request) -> Response:
        info = self._info(request.path_params["plugin"])
        if info is None:
            return error_response("not_found", "Not Found", 404)
        path = plugin_icon_path(info.name, str(info.source), self.registry)
        media = "image/svg+xml" if path.suffix == ".svg" else "image/png"
        return FileResponse(path, media_type=media)

    async def plugin_detail(self, request: Request) -> JSONResponse:
        info = self._info(request.path_params["plugin"])
        if info is None:
            return error_response("not_found", "插件不存在", 404)

        def read():
            native = info.native_plugin
            meta = getattr(native, "metadata", None)
            index = self.registry.cached() if info.source == "external" else None
            entry = index.find(info.name) if index is not None else None
            rows = self.manager.status() if info.source == "external" else []
            row = next((r for r in rows if r.plugin_id == info.name), None)
            links: list[dict[str, str]] = []

            def link(kind: str, label: str, url: object) -> None:
                if (safe := _http(url)) and all(x["url"] != safe for x in links):
                    links.append({"kind": kind, "label": label, "url": safe})

            notes = {"blocked": None, "yanked": None, "update": None}
            if info.source == "builtin":
                version, ref = _tenko_release()
                link("repo", "源码", f"{_REPO}/tree/{ref}/tenko/plugins/{info.name}")
            else:
                version = row.version if row else getattr(meta, "version", None)
                if entry is not None:
                    link("home", "主页", entry.homepage)
                    link("repo", "源码仓库", entry.git_url)
                for label, url in (getattr(meta, "urls", None) or {}).items():
                    link(_link_kind(str(label), str(url)), str(label), url)
                if row is not None and row.dist:
                    link("pypi", "PyPI", f"https://pypi.org/project/{row.dist}/")
                if row is not None and index is not None:
                    notes = installed_notes(
                        index, row.plugin_id, row.dist, row.version, row.trust
                    )
            commands = [
                {"title": title, "fields": [{"label": k, "value": v} for k, v in f]}
                for title, f in (plugin_command_help(native) if native else [])
            ]
            return {
                "plugin": info.name,
                "name": info.display_name,
                "source": info.source,
                "local": bool(getattr(info, "is_local", False)),
                "description": getattr(meta, "description", None)
                or (entry.description if entry else ""),
                "version": version,
                "authors": _authors(getattr(meta, "author", None))
                or (list(entry.authors) if entry else []),
                "license": getattr(meta, "license", None)
                or (entry.license if entry else None)
                or (_TENKO_LICENSE if info.source == "builtin" else None),
                "tier": entry.tier if entry else None,
                "tags": list(entry.tags) if entry else [],
                "icon": self.icon_of(info.name, info.source),
                "links": links,
                "commands": commands,
                "installed": asdict(row) if row else None,
                "has_config": info.source == "external"
                and getattr(meta, "config", None) is not None,
                **notes,
            }

        return await self.service._read("plugin_detail", read)

    async def installed_view(self, request: Request) -> JSONResponse:
        async def read():
            index = self.registry.cached()  # 不联网；市场页刷新时会更新缓存
            runtime = self.service.plugin_runtime
            names = (
                {i.name: i.display_name for i in runtime.discover()} if runtime else {}
            )
            rows = []
            for row in self.manager.status():
                entry = index.plugins.get(row.plugin_id) if index else None
                notes = (
                    installed_notes(
                        index, row.plugin_id, row.dist, row.version, row.trust
                    )
                    if index
                    else {"blocked": None, "yanked": None, "update": None}
                )
                rows.append(
                    {
                        **asdict(row),
                        "name": entry.name if entry else names.get(row.plugin_id),
                        "icon_url": self._icon_url(entry),
                        **notes,
                    }
                )
            suspended = []
            for plugin_id, held in sorted(self.manager.suspended().items()):
                entry = index.plugins.get(plugin_id) if index else None
                record, resume = held["record"], held.get("resume")
                suspended.append(
                    {
                        "plugin_id": plugin_id,
                        "name": entry.name if entry else None,
                        "icon_url": self._icon_url(entry),
                        "dist": record["dist"],
                        "version": record["version"],
                        "trust": record["trust"],
                        "enabled": bool(held.get("enabled")),
                        "reason": held.get("reason") or "",
                        "suspended_at": held.get("suspended_at"),
                        # 已选定、下次启动自动装回的来源；装回失败时清空并记下原因。
                        "resume": resume["requirement"] if resume else None,
                        "error": held.get("error"),
                        "options": installable(entry) if entry else [],
                    }
                )
            drift = await asyncio.to_thread(self.manager.drift)
            history = self.manager.history_dir
            return {
                "plugins": rows,
                "suspended": suspended,
                "failures": self.load_failures(names),
                "drift": drift,
                "rollback_available": history.is_dir()
                and any(p.is_dir() for p in history.iterdir()),
                # 卸载留到重启才删包，pending_removal 非空也要提示重启。
                "restart_required": any(r["restart_required"] for r in rows)
                or bool(self._pending_removal()),
                "can_restart": bool(getattr(self.updater, "enabled", False)),
                "task": self._task_data(self._latest_running()),
            }

        return await self.service._read("installed_plugins", read)

    def load_failures(
        self, names: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """本次启动加载失败、且仍然安装着的插件（内置插件总是安装着）。"""

        runtime = self.service.plugin_runtime
        failures = getattr(runtime, "load_failures", None)
        if not failures:
            return []
        installed = {r.plugin_id for r in self.manager.status()}
        builtin = runtime.builtin_names()
        return [
            {
                "plugin_id": plugin_id,
                "name": (names or {}).get(plugin_id),
                "builtin": plugin_id in builtin,
                "reason": reason,
            }
            for plugin_id, reason in sorted(failures.items())
            if plugin_id in installed or plugin_id in builtin
        ]

    def attention(self) -> int:
        """需要 Master 处理的插件数：停用待恢复的和加载失败的。"""

        return len(self.manager.suspended()) + len(self.load_failures())

    def _pending_removal(self) -> list[str]:
        runtime = getattr(self.manager, "runtime", None)
        return (
            list(runtime.read_state().get("pending_removal") or []) if runtime else []
        )

    def blocked_reason(self, plugin_id: str) -> str | None:
        """按本地缓存的目录判断插件是否被封禁（不联网）。"""

        index = self.registry.cached()
        if index is None:
            return None
        row = next((r for r in self.manager.status() if r.plugin_id == plugin_id), None)
        return index.blocked_reason(plugin_id, row.dist if row else None)

    # ------------------------------------------------------------------ 任务

    def _latest_running(self) -> PluginTask | None:
        return next((t for t in self.tasks.values() if t.state == "running"), None)

    @staticmethod
    def _task_data(task: PluginTask | None) -> dict[str, Any] | None:
        return asdict(task) if task else None

    async def task_view(self, request: Request) -> JSONResponse:
        task = self.tasks.get(request.path_params["id"])
        if task is None:
            return error_response("not_found", "任务不存在", 404)
        return await self.service._read("plugin_task", lambda: asdict(task))

    async def _operation(
        self, action: str, body: dict[str, Any]
    ) -> Callable[[], Awaitable[Any]]:
        """校验请求并返回要在后台执行的操作；校验失败直接报错，不创建任务。"""

        plugin_id = body.get("plugin_id")
        if plugin_id is not None and not isinstance(plugin_id, str):
            raise ValueError("plugin_id 必须是字符串")
        manager, actor = self.manager, "webui"
        if action == "install":
            version = body.get("version")
            if not plugin_id or (version is not None and not isinstance(version, str)):
                raise ValueError("请指定目录里的插件 ID 和可选的版本")
            try:
                found = await self.registry.resolve(
                    f"{plugin_id}@{version}" if version else plugin_id
                )
            except PluginManagerError as error:
                raise ValueError(error.detail) from error
            if found is None:
                raise ValueError(
                    f"{plugin_id} 不在插件目录里；未收录的插件请由 Master 在聊天里安装"
                )
            source = source_for(*found)
            return lambda: manager.install(source, actor=actor)
        if action == "resume":
            version = body.get("version")
            if version is not None and not isinstance(version, str):
                raise ValueError("version 必须是字符串")
            held = manager.suspended().get(plugin_id or "")
            if held is None:
                raise KeyError(plugin_id)
            try:
                source = await resume_source(plugin_id, held, version)
            except PluginManagerError as error:
                raise ValueError(error.detail) from error
            return lambda: manager.resume(plugin_id, source, actor=actor)
        if action == "update":
            sources = await registry_updates(manager, plugin_id)
            return lambda: manager.update(plugin_id, sources=sources, actor=actor)
        if action == "uninstall":
            clear = body.get("clear_data", False)
            if not plugin_id or type(clear) is not bool:
                raise ValueError("请指定插件 ID，clear_data 必须是布尔值")
            row = next((r for r in manager.status() if r.plugin_id == plugin_id), None)
            if row is None and plugin_id not in manager.suspended():
                raise KeyError(plugin_id)
            if clear and row is not None and row.loaded:
                raise RuntimeError("插件正在运行，请先停用并重启，再清除数据卸载")
            return lambda: manager.uninstall(plugin_id, clear_data=clear, actor=actor)
        if action == "rollback":
            return lambda: manager.rollback(actor=actor)
        return lambda: manager.sync_env(actor=actor)

    async def create_task(self, request: Request) -> JSONResponse:
        async def start():
            body = await read_json(request)
            action = body.get("action")
            if action not in _ACTIONS or body.get("confirm") is not True:
                raise ValueError("请确认要执行的插件操作")
            if set(body) - {"action", "confirm", "plugin_id", "version", "clear_data"}:
                raise ValueError("包含未知字段")
            if self._latest_running() is not None:
                raise RuntimeError("已有插件任务正在执行，请稍后再试")
            if self.upgrade.busy or self._restarting:
                raise RuntimeError("宿主正在升级或重启，请稍后再试")
            request.state.webui_action = f"plugin-{action}"
            operation = await self._operation(action, body)
            task = PluginTask(uuid.uuid4().hex[:12], action, body.get("plugin_id"))
            self.tasks[task.id] = task
            for stale in list(self.tasks)[:-_TASK_KEEP]:
                if self.tasks[stale].state != "running":
                    del self.tasks[stale]
            self._running = asyncio.create_task(self._run(task, operation))
            return asdict(task)

        return await self.service._manage(request, start)

    async def _run(
        self, task: PluginTask, operation: Callable[[], Awaitable[Any]]
    ) -> None:
        try:
            result = await operation()
        except PluginManagerError as error:
            task.state, task.message, task.restored = (
                "failed",
                str(error),
                error.restored,
            )
        except Exception as error:  # noqa: BLE001 - 结果写进任务，由前端展示
            logger.exception("WebUI 插件任务 {} 失败", task.action)
            task.state, task.message = "failed", f"{type(error).__name__}: {error}"
        else:
            changes = [
                c
                for c in (result if isinstance(result, list) else [result])
                if isinstance(c, PluginChange)
            ]
            task.changes = [_change(c) for c in changes]
            runtime = self.service.plugin_runtime
            if task.action in {"update", "resume"} and changes and runtime is not None:
                task.live = await self.manager.go_live(runtime, changes)
            # 没加载的插件不在 live 里：没有旧代码在跑，启用时再加载即可。
            pending = {pid: why for pid, why in task.live.items() if why is not None}
            task.restart_required = task.action not in {"install", "sync"} and (
                bool(pending) or task.action in {"uninstall", "rollback"}
            )
            if task.action == "sync":
                task.message = (
                    "已重装：" + "、".join(result) if result else "环境与插件锁一致"
                )
            elif not task.changes:
                task.message = "已是最新版本"
            elif task.action == "install":
                task.message = "已安装，启用后生效"
            elif task.action in {"update", "resume"}:
                done = "已恢复" if task.action == "resume" else "已更新"
                task.message = (
                    f"{done}，已在运行中生效，无需重启"
                    if not pending
                    else f"{done}；重启后生效："
                    + "；".join(f"{pid}（{why}）" for pid, why in pending.items())
                )
            else:
                task.message = "完成，重启后生效"
            task.state = "succeeded"
        task.finished_at = time.time()

    # ------------------------------------------------------------------ 配置

    def _loaded_plugin(self, plugin_id: str):
        runtime = self.service.plugin_runtime
        if runtime is None:
            raise RuntimeError("插件运行时尚未就绪")
        info = next(
            (
                i
                for i in runtime.discover()
                if i.name == plugin_id and i.source == "external"
            ),
            None,
        )
        if info is None:
            raise KeyError(plugin_id)
        native = info.native_plugin
        model = getattr(getattr(native, "metadata", None), "config", None)
        return native, model

    async def plugin_config(self, request: Request) -> JSONResponse:
        from arclet.entari.config.action import config_model_schema

        async def action():
            plugin_id = request.path_params["plugin"]
            native, model = self._loaded_plugin(plugin_id)
            if native is None or model is None:
                return {"plugin_id": plugin_id, "available": False}
            schema = config_model_schema(model)
            properties = schema.get("properties", {})
            raw = self.service.raw_config().get("plugin_config", {}).get(plugin_id, {})
            env_backed = any(isinstance(v, str) and "${" in v for v in raw.values())
            current = {
                key: raw.get(key, spec.get("default"))
                for key, spec in properties.items()
            }
            if request.method == "POST":
                body = await read_json(request)
                values = body.get("values")
                if set(body) != {"values", "confirm"} or body["confirm"] is not True:
                    raise ValueError("请确认保存插件配置")
                if env_backed:
                    raise RuntimeError("该插件配置引用了环境变量，请在配置文件里修改")
                if not isinstance(values, dict) or set(values) - set(properties):
                    raise ValueError("包含未知的配置项")
                for key, value in values.items():
                    spec = properties[key]
                    if spec.get("type") not in _EDITABLE:
                        if value != current[key]:
                            raise ValueError(f"{key} 不能在面板里修改，请编辑配置文件")
                    elif not _field_ok(spec, value):
                        raise ValueError(f"{key} 的值类型不正确")
                request.state.webui_action = "plugin-config"
                merged = {**raw, **values}

                def write(changes: dict[str, Any]):
                    def mutate(data) -> None:
                        section = data.setdefault("plugin_config", {}).setdefault(
                            plugin_id, {}
                        )
                        for key in values:
                            if key in changes:
                                section[key] = changes[key]
                            else:
                                section.pop(key, None)

                    self.service.update_config(mutate)

                write(values)
                try:
                    await self.service.plugin_runtime.apply_config(plugin_id, merged)
                except Exception as error:
                    # 新配置让插件加载失败：文件改回去，免得下次启动时插件起不来。
                    write(raw)
                    raise RuntimeError(
                        f"新配置未能生效，已恢复原配置：{error}"
                    ) from error
                current = {key: merged.get(key, current[key]) for key in properties}
            return {
                "plugin_id": plugin_id,
                "available": True,
                "schema": schema,
                "values": current,
                "editable": [
                    k for k, v in properties.items() if v.get("type") in _EDITABLE
                ],
                "read_only_reason": "该插件配置引用了环境变量，请在配置文件里修改"
                if env_backed
                else None,
            }

        # GET 也要把“插件不存在”映射成 404，所以同样走 _manage。
        return await self.service._manage(request, action)

    # ------------------------------------------------------------------ 重启

    @property
    def restarting(self) -> bool:
        return self._restarting

    def schedule_restart(self) -> None:
        """启动重启接管并在响应发出后退出；接管未能启动时抛出 RuntimeError。"""

        updater = self.updater
        if not updater.spawn_restart_watcher():
            raise RuntimeError("自动重启未能启动，请手动重启")
        self._restarting = True
        # 先把响应发回去，再退出。
        asyncio.get_running_loop().call_later(1.0, updater.request_graceful_shutdown)

    async def restart(self, request: Request) -> JSONResponse:
        async def action():
            body = await read_json(request)
            if body != {"confirm": True}:
                raise ValueError("请确认重启")
            updater = self.updater
            if not getattr(updater, "enabled", False):
                raise RuntimeError("重启依赖升级系统，当前未启用；请手动重启")
            if self._restarting:
                raise RuntimeError("已在重启中")
            if self._latest_running() is not None:
                raise RuntimeError("插件任务正在执行，完成后再重启")
            if self.upgrade.busy:
                raise RuntimeError("宿主正在升级，请稍后再试")
            self.schedule_restart()
            request.state.webui_action = "restart"
            return {"restarting": True}

        return await self.service._manage(request, action)
