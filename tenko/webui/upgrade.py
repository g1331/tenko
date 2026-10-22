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
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..host.plugins.manager import PluginManagerError
from ..host.updater import (
    CheckResult,
    NoRollbackAvailable,
    PluginCompatibilityError,
    UpdaterError,
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


class HostUpgrade:
    """挂在 :class:`PluginMarket` 旁边：共用它的升级管理器、插件任务和重启状态。"""

    def __init__(self, market: PluginMarket) -> None:
        self.market = market
        self.job: UpgradeJob | None = None
        self.last_check: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self.job is not None and self.job.state in {"running", "restarting"}

    def routes(self):
        return (
            ("/api/manage/upgrade", ["GET", "POST"], self.upgrade),
            ("/api/manage/upgrade/check", ["POST"], self.check),
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
        self.last_check = _check_data(result)
        return success_response(self.last_check)

    async def upgrade(self, request: Request) -> JSONResponse:
        if request.method == "GET":
            return await self.market.service._read("upgrade", self._status)

        async def start():
            body = await read_json(request)
            action = body.get("action")
            drop = body.get("drop_incompatible", False)
            if action not in {"upgrade", "rollback"} or body.get("confirm") is not True:
                raise ValueError("请确认要执行的升级操作")
            if set(body) - {"action", "confirm", "drop_incompatible"} or (
                type(drop) is not bool
            ):
                raise ValueError("包含未知字段")
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
            self._task = asyncio.create_task(self._run(self.job, run(self.job, drop)))
            return asdict(self.job)

        return await self.market.service._manage(request, start)

    # ------------------------------------------------------------------ 后台

    async def _run(self, job: UpgradeJob, work) -> None:
        try:
            await work
        except (UpdaterError, PluginManagerError) as error:
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

    async def _upgrade(self, job: UpgradeJob, drop: bool) -> None:
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
            # 组合冲突（"*"）说不清该卸哪个，交给 Master 决定。
            if not drop or "*" in error.reasons:
                job.incompatible = {k: _clean(v) for k, v in error.reasons.items()}
                raise
            for plugin_id in error.reasons:
                job.stage = f"卸载不兼容插件 {plugin_id}"
                await self.market.manager.uninstall(plugin_id, actor="webui")
            job.stage = f"下载并校验 {job.target}"
            await updater.prepare(checked.candidate)
        job.stage = "切换版本"
        await updater.request_install()
        self._restart(job, f"{job.target} 已下载并校验通过，正在重启进入新版本")

    async def _rollback(self, job: UpgradeJob, drop: bool) -> None:
        del drop
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
