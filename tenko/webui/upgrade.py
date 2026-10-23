"""面板里的宿主升级：查看版本、检查更新、升级和回滚。

只调用 ``tenko.host.updater`` 已有的控制平面，流程与 updater 插件的
``/检查更新``、``/升级``、``/回滚`` 相同；下载校验在后台任务里进行，前端
轮询状态，进入重启后再等面板恢复。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from packaging.version import InvalidVersion, Version
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..host.plugins.manager import PluginManagerError, PluginSource
from ..host.plugins.registry import (
    RegistryError,
    installable,
    source_for,
    why_incompatible,
)
from ..host.updater import (
    CheckResult,
    NoRollbackAvailable,
    PluginCompatibilityError,
    UpdaterError,
    UrllibHttpClient,
    parse_version,
)
from .auth import error_response, read_json, success_response

if TYPE_CHECKING:
    from .plugins import PluginMarket

_HISTORY_LIMIT = 10
_HISTORY_SCAN_BYTES = 256 * 1024
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# 历史只列最终结果；“开始”“已请求”这类中间记录不展示。
_HISTORY_RESULTS = {
    "install": {"success", "failed"},
    "rollback": {"success", "failed"},
    "startup": {"failed"},
    "download": {"failed"},
    "restart": {"requested"},
}


@dataclass
class UpgradeJob:
    action: str  # upgrade | rollback
    state: str = "running"  # running | restarting | succeeded | failed
    stage: str = ""
    message: str = ""
    target: str | None = None
    incompatible: dict[str, str] = field(default_factory=dict)
    # 不兼容插件在新版本上可装的版本（新到旧），供 Master 选择；空表示只能停用。
    options: dict[str, list[str]] = field(default_factory=dict)


def _clean(text: object, limit: int = 300) -> str:
    return _ANSI.sub("", str(text or "")).strip()[-limit:]


def _display(version) -> str:
    """按发布标签的写法展示版本（4.0.0-rc26），与侧栏和升级记录一致。"""
    text = ".".join(map(str, version.release))
    return f"{text}-{version.pre[0]}{version.pre[1]}" if version.pre else text


def _check_data(result: CheckResult) -> dict[str, Any]:
    release = result.candidate
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": result.status,
        "current_version": _display(result.current_version),
        "channel": result.channel.value,
        "candidate": None
        if release is None
        else {
            "tag": release.tag,
            "version": str(release.version),
            "prerelease": release.prerelease,
            "source": release.source,
            "notes": release.notes,
        },
    }


_GITHUB_URL = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
# 一次跨很多版本时只列最近的这些，每份说明也截断，避免把面板撑得过长。
_CHANGELOG_LIMIT = 20
_NOTES_LIMIT = 20_000


def _github(updater) -> tuple[str, str, str | None, str] | None:
    """升级源对应的 GitHub 仓库：``(owner/repo, API 地址, token, tag 前缀)``。"""

    for source in getattr(updater, "sources", ()):
        repository = str(getattr(source, "repository", ""))
        prefix = getattr(source, "tag_prefix", "v")
        if getattr(source, "name", None) == "github_release":
            return repository, source.api_base_url, source.token, prefix
        if match := _GITHUB_URL.search(repository):
            return f"{match[1]}/{match[2]}", "https://api.github.com", None, prefix
    return None


def _in_range(tag: str, prefix: str, low, high, prerelease: bool) -> Any:
    try:
        version = parse_version(tag.removeprefix(prefix))
    except ValueError:
        return None
    if not low < version <= high or (version.is_prerelease and not prerelease):
        return None
    return version


class HostUpgrade:
    """挂在 :class:`PluginMarket` 旁边：共用它的升级管理器、插件任务和重启状态。"""

    def __init__(self, market: PluginMarket) -> None:
        self.market = market
        self.job: UpgradeJob | None = None
        self.last_check: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self.http = UrllibHttpClient(timeout=10)
        # 当前版本的发布说明：版本只在重启后变，读到一次就够了。
        self._current_notes: tuple[str, dict[str, Any] | None] | None = None

    @property
    def busy(self) -> bool:
        return self.job is not None and self.job.state in {"running", "restarting"}

    def routes(self):
        return (
            ("/api/manage/upgrade", ["GET", "POST"], self.upgrade),
            ("/api/manage/upgrade/check", ["POST"], self.check),
            ("/api/manage/upgrade/notes", ["GET"], self.notes),
        )

    # ------------------------------------------------------------------ 状态

    def _history(self) -> list[dict[str, Any]]:
        path = self.market.updater.layout.audit_file
        try:
            with path.open("rb") as file:
                file.seek(max(0, path.stat().st_size - _HISTORY_SCAN_BYTES))
                lines = file.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return []
        entries: deque[dict[str, Any]] = deque(maxlen=_HISTORY_LIMIT)
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # 截断的首行或损坏的行
            if not isinstance(record, dict):
                continue
            if record.get("result") not in _HISTORY_RESULTS.get(
                record.get("action"), ()
            ):
                continue
            entries.append(
                {
                    "timestamp": record.get("timestamp"),
                    "action": record["action"],
                    "result": record["result"],
                    "from": record.get("current_version"),
                    "to": record.get("target_version"),
                    "automatic": str(record.get("automatic")) == "True",
                    "error": _clean(record.get("error") or record.get("reason"))
                    or None,
                }
            )
        return list(reversed(entries))

    def _status(self) -> dict[str, Any]:
        updater = self.market.updater
        previous = updater.layout.read_previous()
        return {
            "enabled": bool(updater.enabled),
            "current_version": _display(updater.current_version),
            "channel": updater.channel.value,
            "policy": updater.policy.value,
            "check_interval_hours": updater.check_interval_hours,
            "previous_version": _display(previous.version) if previous else None,
            "release_url": self._release_url(updater),
            "last_check": self.last_check,
            "job": asdict(self.job) if self.job else None,
            "history": self._history(),
        }

    # ------------------------------------------------------------------ 接口

    async def check(self, request: Request) -> JSONResponse:
        # 联网检查可能要几秒，不占用面板的写锁；升级管理器自己会串行化。
        if await read_json(request) != {}:
            return error_response("invalid_request", "包含未知字段", 400)
        request.state.webui_action = "upgrade-check"
        try:
            result = await self.market.updater.check()
        except UpdaterError as error:
            return error_response("upgrade_check_failed", _clean(error), 502)
        data = _check_data(result)
        if result.candidate is not None:
            data["changelog"], data["changelog_error"] = await self._changelog(result)
        self.last_check = data
        return success_response(self.last_check)

    @staticmethod
    def _release_url(updater) -> str | None:
        found = _github(updater)
        if found is None or found[1] != "https://api.github.com":
            return None
        repository, _, _, prefix = found
        tag = prefix + _display(updater.current_version)
        return f"https://github.com/{repository}/releases/tag/{tag}"

    async def _releases(
        self,
    ) -> tuple[list[tuple[str, dict[str, Any]]], str, str | None]:
        """GitHub Releases 里的已发布版本：``([(tag, 说明)], tag 前缀, 失败原因)``。

        升级源不在 GitHub 上时返回空列表。
        """

        found = _github(self.market.updater)
        if found is None:
            return [], "v", None
        repository, api, token, prefix = found
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            payload = await self.http.get_json(
                f"{api}/repos/{repository}/releases?per_page=100", headers=headers
            )
        except UpdaterError as error:
            return [], prefix, _clean(error)
        if not isinstance(payload, list):
            return [], prefix, "GitHub Releases 返回格式不对"
        releases = []
        for item in payload:
            if not isinstance(item, dict) or item.get("draft"):
                continue
            tag = str(item.get("tag_name") or "")
            note = {
                "tag": tag,
                "name": str(item.get("name") or tag),
                "published_at": item.get("published_at"),
                "prerelease": bool(item.get("prerelease")),
                "url": f"https://github.com/{repository}/releases/tag/{tag}"
                if api == "https://api.github.com"
                else None,
                "body": str(item.get("body") or "")[:_NOTES_LIMIT],
            }
            releases.append((tag, note))
        return releases, prefix, None

    async def _changelog(
        self, result: CheckResult
    ) -> tuple[list[dict[str, Any]], str | None]:
        """当前版本之后、直到候选版本的各版发布说明（新到旧），取自 GitHub Releases。

        读取失败不影响检查结果，只返回原因。
        """

        releases, prefix, error = await self._releases()
        high = result.candidate.version
        prerelease = result.channel.value == "prerelease" or result.candidate.prerelease
        entries = []
        for tag, note in releases:
            version = _in_range(tag, prefix, result.current_version, high, prerelease)
            if version is not None:
                entries.append((version, note))
        entries.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in entries[:_CHANGELOG_LIMIT]], error

    async def notes(self, request: Request) -> JSONResponse:
        """当前运行版本的发布说明；GitHub 上没有这个版本时 note 为 null。"""

        current = _display(self.market.updater.current_version)
        if self._current_notes is None or self._current_notes[0] != current:
            releases, prefix, error = await self._releases()
            if error is not None:
                return error_response("release_notes_failed", error, 502)
            note = next((n for tag, n in releases if tag == prefix + current), None)
            self._current_notes = (current, note)
        return success_response({"note": self._current_notes[1]})

    async def upgrade(self, request: Request) -> JSONResponse:
        if request.method == "GET":
            return await self.market.service._read("upgrade", self._status)

        async def start():
            body = await read_json(request)
            action = body.get("action")
            choices = body.get("plugins", {})
            if action not in {"upgrade", "rollback"} or body.get("confirm") is not True:
                raise ValueError("请确认要执行的升级操作")
            if set(body) - {"action", "confirm", "plugins"}:
                raise ValueError("包含未知字段")
            if not isinstance(choices, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v
                for k, v in choices.items()
            ):
                raise ValueError("plugins 必须是插件 ID 到 suspend 或版本号的映射")
            if not self.market.updater.enabled:
                raise RuntimeError("升级系统未启用")
            if self.busy:
                raise RuntimeError("已有升级操作正在进行")
            if self.market.restarting:
                raise RuntimeError("宿主正在重启")
            if self.market._latest_running() is not None:
                raise RuntimeError("插件任务正在执行，完成后再升级")
            request.state.webui_action = action
            self.job = UpgradeJob(action)
            run = self._upgrade if action == "upgrade" else self._rollback
            self._task = asyncio.create_task(
                self._run(self.job, run(self.job, choices))
            )
            return asdict(self.job)

        return await self.market.service._manage(request, start)

    # ------------------------------------------------------------------ 后台

    async def _run(self, job: UpgradeJob, work) -> None:
        try:
            await work
        except (UpdaterError, PluginManagerError, RegistryError, ValueError) as error:
            job.state, job.message = "failed", _clean(error, 600)
        except Exception as error:  # noqa: BLE001 - 结果写进任务，由前端展示
            logger.exception("WebUI 宿主{}失败", job.action)
            job.state, job.message = "failed", f"{type(error).__name__}: {error}"

    def _restart(self, job: UpgradeJob, message: str) -> None:
        try:
            self.market.schedule_restart()
        except RuntimeError as error:
            job.state, job.message = "failed", f"{message}，但{error}"
            return
        job.state, job.message = "restarting", message

    async def _options(self, error: PluginCompatibilityError) -> dict[str, list[str]]:
        """不兼容插件在新版本宿主上可装的目录版本；目录不可用或未收录时为空。"""

        try:
            api = Version(error.api_version or "")
            index = await self.market.registry.load()
        except (InvalidVersion, RegistryError):
            return {pid: [] for pid in error.reasons if pid != "*"}
        return {
            pid: installable(entry, api) if (entry := index.plugins.get(pid)) else []
            for pid in error.reasons
            if pid != "*"
        }

    async def _target_source(
        self, plugin_id: str, version: str, api_version: str | None
    ) -> PluginSource:
        """新版本宿主上要装回的来源；版本必须在目录里、且能装在新版本上。"""

        index = await self.market.registry.load()
        entry = index.plugins.get(plugin_id)
        item = entry and next((v for v in entry.versions if v.version == version), None)
        if item is None:
            raise ValueError(f"插件目录里没有 {plugin_id} {version}")
        if reason := why_incompatible(item, Version(api_version or "0")):
            raise ValueError(f"{plugin_id} {version} 不能装在新版本上：{reason}")
        return source_for(entry, item)

    async def _upgrade(self, job: UpgradeJob, choices: dict[str, str]) -> None:
        updater = self.market.updater
        job.stage = "检查新版本"
        checked = await updater.check()
        self.last_check = _check_data(checked)
        if checked.candidate is None:
            job.state, job.message = "failed", "当前已是最新版本"
            return
        job.target = checked.candidate.tag
        job.stage = f"下载并校验 {job.target}"
        try:
            await updater.prepare(checked.candidate)
        except PluginCompatibilityError as error:
            # 每个不兼容的插件都要有选择（停用，或新版本上要装回的版本）才继续；
            # 组合冲突（"*"）说不清该停哪个，交给 Master 处理。
            if "*" in error.reasons or not set(error.reasons) <= set(choices):
                job.incompatible = {k: _clean(v) for k, v in error.reasons.items()}
                job.options = await self._options(error)
                raise
            job.stage = "停用不兼容插件"
            sources = {
                pid: None
                if choices[pid] == "suspend"
                else await self._target_source(pid, choices[pid], error.api_version)
                for pid in error.reasons
            }
            manager = self.market.manager
            await manager.suspend(sources, reasons=error.reasons, actor="webui")
            job.stage = f"下载并校验 {job.target}"
            try:
                await updater.prepare(checked.candidate)
            except Exception:
                # 宿主没升级成，插件也不该停着：撤销刚才的停用。
                await manager.rollback(actor="webui")
                raise
        job.stage = "切换版本"
        await updater.request_install()
        self._restart(job, f"{job.target} 已下载并校验通过，正在重启进入新版本")

    async def _rollback(self, job: UpgradeJob, choices: dict[str, str]) -> None:
        del choices
        job.stage = "准备回滚"
        try:
            result = await self.market.updater.rollback()
        except NoRollbackAvailable:
            job.state, job.message = "failed", "没有可回滚的上一个版本"
            return
        job.target = str(result.target_version)
        if result.applied:
            job.state, job.message = "succeeded", f"已回滚到 {job.target}"
            return
        self._restart(job, f"正在重启并回滚到 {job.target}")
