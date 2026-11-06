"""升级管理器、结果类型与模块级单例。"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import sys
import tomllib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from loguru import logger
from packaging.version import Version

from ..perm import PermissionChecker
from ..sandbox import refuse_in_sandbox
from .audit import AuditLogger
from .compat import CompatibilityResult, ConfigCompatibilityChecker, tenko_api_version
from .database import (
    DATABASE_BACKUPS_KEEP,
    DATABASE_BACKUP_FREE_MARGIN,
    backup_sqlite_database,
    resolve_sqlite_database_path,
)
from .deps import UvDependencySync
from .health import DefaultHealthChecker, _health_ok, _health_reason
from .layout import ReleasePointer, UpgradeLayout
from .models import (
    ArtifactAcquisitionError,
    DependencySyncError,
    HealthCheckFailed,
    InvalidVersionError,
    NoRollbackAvailable,
    NoUpdateAvailable,
    PluginCompatibilityError,
    Release,
    UpdateChannel,
    UpdatePolicy,
    UpdateSourceError,
    UpdaterError,
    UpgradeConfigError,
    VersionSource,
    _RESTART_WATCHER_TIMEOUT,
    _STARTUP_FAILURE_THRESHOLD,
    _STARTUP_GRACE_PERIOD,
    _pending_release_tag,
    _redact_text,
    parse_version,
    select_release,
)
from .sources import (
    GitHubReleaseSource,
    GitTagSource,
    UrlManifestSource,
    _normalize_git_repository,
)
from .recovery import systemd_managed
from .watcher import spawn_restart_watcher


@dataclass(frozen=True, slots=True)
class CheckResult:
    current_version: Version
    channel: UpdateChannel
    source: str
    status: str
    candidate: Release | None = None
    releases_seen: int = 0
    error: str | None = None

    @property
    def update_available(self) -> bool:
        return self.candidate is not None


@dataclass(frozen=True, slots=True)
class PrepareResult:
    release: Release
    path: Path
    compatibility: CompatibilityResult


@dataclass(frozen=True, slots=True)
class HandoffResult:
    action: str
    target_version: Version
    path: Path
    applied: bool = False


@dataclass(frozen=True, slots=True)
class InstallResult:
    success: bool
    target_version: Version
    active_path: Path | None
    rolled_back: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RollbackResult:
    success: bool
    target_version: Version
    path: Path
    applied: bool
    reason: str


def read_project_version(project_root: str | Path) -> Version:
    path = Path(project_root) / "pyproject.toml"
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
        value = data["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise UpgradeConfigError(f"无法从 {path} 读取 project.version") from exc
    return parse_version(value)


class UpgradeManager:
    """协调升级的发现、准备、外部安装接管和回滚。"""

    def __init__(
        self,
        current_version: str | Version,
        source: VersionSource | None = None,
        *,
        sources: Sequence[VersionSource] = (),
        layout: UpgradeLayout | None = None,
        project_root: str | Path | None = None,
        config_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        config_version: str | Version = "1.0.0",
        channel: str | UpdateChannel = UpdateChannel.STABLE,
        policy: str | UpdatePolicy = UpdatePolicy.CHECK_ONLY,
        enabled: bool = True,
        check_interval_hours: int = 24,
        restart_watch_timeout: int = _RESTART_WATCHER_TIMEOUT,
        startup_grace_period: float = _STARTUP_GRACE_PERIOD,
        startup_failure_threshold: int = _STARTUP_FAILURE_THRESHOLD,
        database_path: str | Path | None = None,
        database_backup_enabled: bool = True,
        compatibility_checker: ConfigCompatibilityChecker | None = None,
        health_checker: Any | None = None,
        dependency_sync: Callable[[Path], Awaitable[None]] | None = None,
        plugin_sync: Callable[..., Awaitable[None]] | None = None,
        plugin_check: Callable[[Path], Awaitable[Mapping[str, str]]] | None = None,
        audit: AuditLogger | None = None,
        shutdown_callback: Callable[[], bool] | None = None,
    ) -> None:
        if source is not None and sources:
            raise ValueError("source 和 sources 只能指定一个")
        self.current_version = parse_version(current_version)
        self.sources = tuple(sources) if sources else ((source,) if source else ())
        self.project_root = Path(project_root or Path.cwd()).expanduser().resolve()
        self.layout = layout or UpgradeLayout(self.project_root / ".tenko/upgrades")
        self.config_path = (
            self._resolve_path(config_path) if config_path is not None else None
        )
        self.data_dir = self._resolve_path(data_dir) if data_dir is not None else None
        self.config_version = parse_version(config_version)
        self.channel = UpdateChannel.parse(channel)
        self.policy = UpdatePolicy.parse(policy)
        self.enabled = bool(enabled)
        if check_interval_hours <= 0:
            raise ValueError("check_interval_hours 必须大于 0")
        if restart_watch_timeout <= 0:
            raise ValueError("restart_watch_timeout 必须大于 0")
        if startup_grace_period <= 0:
            raise ValueError("startup_grace_period 必须大于 0")
        if type(startup_failure_threshold) is not int or startup_failure_threshold <= 0:
            raise ValueError("startup_failure_threshold 必须是正整数")
        self.check_interval_hours = check_interval_hours
        self.restart_watch_timeout = restart_watch_timeout
        self.startup_grace_period = float(startup_grace_period)
        self.startup_failure_threshold = startup_failure_threshold
        self.database_path = (
            Path(database_path).expanduser().resolve()
            if database_path is not None
            else self.project_root / ".tenko" / "tenko.db"
        )
        self.database_backup_enabled = bool(database_backup_enabled)
        # from_config 构造版本源失败的原因，检查更新时原样报给用户。
        self.source_error: str | None = None
        self.compatibility_checker = (
            compatibility_checker or ConfigCompatibilityChecker()
        )
        self.health_checker = health_checker or DefaultHealthChecker(
            config_path=self.config_path,
            data_dir=self.data_dir,
            upgrade_root=self.layout.root,
            stable_root=self.project_root,
        )
        # 切换到某个版本目录前后，把共享虚拟环境同步到该目录的锁文件。
        self.dependency_sync = dependency_sync
        # 宿主依赖同步之后重新应用并校验插件锁（见 PluginManager.reconcile）。
        self.plugin_sync = plugin_sync
        # 准备新版本时用它的宿主锁预检插件（见 PluginManager.incompatible）。
        self.plugin_check = plugin_check
        self.audit = audit or AuditLogger(self.layout.audit_file)
        self._shutdown_callback = shutdown_callback
        self._operation_lock = asyncio.Lock()

    def _resolve_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.project_root / path).resolve()

    def restart_command(self) -> tuple[str, ...]:
        """返回 watcher 在稳定根目录中应执行的一次性启动命令。"""

        arguments = tuple(sys.argv[1:])
        launcher = self.project_root / "scripts" / "launcher.sh"
        if os.name == "posix" and launcher.is_file():
            return (str(launcher), *arguments)
        return (sys.executable, "-m", "tenko", *arguments)

    def spawn_restart_watcher(self, *, old_pid: int | None = None) -> bool:
        """安排当前进程退出后重新拉起，返回退出后是否会自动启动。

        systemd 管理时由 systemd 按 ``Restart=always`` 拉起，启动失败回滚由
        启动引导负责；这里不再起 watcher，否则两边会各拉起一个 Tenko。
        其他部署 arm 一次 detached watcher。
        """

        if systemd_managed():
            return True
        return spawn_restart_watcher(
            old_pid if old_pid is not None else os.getpid(),
            self.restart_command(),
            stable_root=self.project_root,
            watcher_dir=self.layout.restart_watchers_dir,
            timeout=self.restart_watch_timeout,
            upgrade_root=self.layout.root,
            startup_grace_period=self.startup_grace_period,
            startup_failure_threshold=self.startup_failure_threshold,
        )

    def confirm_startup(self) -> None:
        """启动宽限期过后仍在运行：结束 systemd 部署下的候选版本启动观察。"""

        try:
            self.layout.clear_startup_probation()
        except OSError as exc:
            logger.error("Tenko startup probation cleanup failed: {}", exc)

    def request_graceful_shutdown(self) -> bool:
        """请求运行时沿 Launart 生命周期退出，返回是否已提交请求。"""

        if self._shutdown_callback is None:
            return False
        return bool(self._shutdown_callback())

    def configure_shutdown(self, callback: Callable[[], bool] | None) -> None:
        """注入当前运行时的优雅退出回调。"""

        self._shutdown_callback = callback

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(
            getattr(source, "name", source.__class__.__name__)
            for source in self.sources
        )

    def _source_for(self, release: Release) -> VersionSource:
        for source in self.sources:
            if getattr(source, "name", None) == release.source:
                return source
        raise UpdateSourceError(f"找不到发布来源 {release.source!r}")

    async def _check_unlocked(self) -> CheckResult:
        if not self.enabled:
            result = CheckResult(
                self.current_version,
                self.channel,
                "+".join(self.source_names),
                "disabled",
            )
            self.audit.record(
                "check",
                current_version=self.current_version,
                result="disabled",
                channel=self.channel.value,
                sources=self.source_names,
            )
            return result
        if not self.sources:
            error = self.source_error or "未配置升级版本源"
            self.audit.record(
                "check",
                current_version=self.current_version,
                result="failed",
                error=error,
            )
            raise UpgradeConfigError(error)
        try:
            releases: list[Release] = []
            for source in self.sources:
                releases.extend(await source.discover(self.channel))
            candidate = select_release(releases, self.current_version, self.channel)
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            error = str(exc) or exc.__class__.__name__
            self.audit.record(
                "check",
                current_version=self.current_version,
                result="failed",
                channel=self.channel.value,
                sources=self.source_names,
                error=error,
            )
            if isinstance(exc, UpdaterError):
                raise
            raise UpdateSourceError(error) from exc

        status = "available" if candidate else "current"
        self.audit.record(
            "check",
            current_version=self.current_version,
            target_version=candidate.version if candidate else None,
            result=status,
            channel=self.channel.value,
            sources=self.source_names,
            releases_seen=len(releases),
            target_tag=candidate.tag if candidate else None,
        )
        return CheckResult(
            self.current_version,
            self.channel,
            "+".join(self.source_names),
            status,
            candidate,
            len(releases),
        )

    async def check(self) -> CheckResult:
        async with self._operation_lock:
            return await self._check_unlocked()

    async def prepare(self, release: Release | None = None) -> PrepareResult:
        refuse_in_sandbox("下载新版本")
        async with self._operation_lock:
            if release is None:
                checked = await self._check_unlocked()
                release = checked.candidate
            if release is None:
                raise NoUpdateAvailable("当前通道没有可安装的新版本")
            if release.version <= self.current_version:
                raise NoUpdateAvailable(
                    f"目标版本 {release.tag} 不高于当前版本 {self.current_version}"
                )
            if self.channel is UpdateChannel.STABLE and release.version.is_prerelease:
                raise NoUpdateAvailable("stable 通道不允许安装预发布版本")
            if (pending := self.layout.read_pending()) is not None:
                # 同一版本已准备好（预下载或 request_install 失败后重试）时直接复用。
                if parse_version(pending["version"]) != release.version:
                    raise UpgradeConfigError(
                        "已有待安装版本，请先完成或清理现有 handoff"
                    )
                recorded = pending.get("compatibility") or {}
                required = recorded.get("required")
                return PrepareResult(
                    release,
                    Path(str(pending["path"])).resolve(),
                    CompatibilityResult(
                        self.current_version,
                        parse_version(required) if required else None,
                        True,
                        "沿用已准备的待安装版本",
                    ),
                )
            stage_root = self.layout.new_staging_dir()
            candidate_path = stage_root / "release"
            final_path: Path | None = None
            pending_written = False
            self.audit.record(
                "download",
                current_version=self.current_version,
                target_version=release.version,
                result="started",
                source=release.source,
                target_tag=release.tag,
            )
            try:
                source = self._source_for(release)
                acquired = await source.acquire(release, candidate_path)
                acquired = Path(acquired).resolve()
                if not acquired.is_dir() or not acquired.is_relative_to(
                    stage_root.resolve()
                ):
                    raise ArtifactAcquisitionError(
                        "版本源返回了 staging 目录之外的制品"
                    )
                compatibility = self.compatibility_checker.check_candidate(
                    acquired, self.config_version, release
                )
                health = await self.health_checker.check(
                    acquired,
                    phase="pre-switch",
                )
                if not _health_ok(health):
                    raise HealthCheckFailed(_health_reason(health))
                if self.plugin_check is not None and (
                    reasons := await self.plugin_check(acquired)
                ):
                    raise PluginCompatibilityError(
                        release.version, reasons, tenko_api_version(acquired)
                    )
                final_path = self.layout.promote(acquired, release.version)
                pending = {
                    # 指针字段保存可稳定解析的规范版本；原始 tag 保存在
                    # release 记录中，供审计和外部展示使用。
                    "version": str(release.version),
                    "path": str(final_path),
                    "release": release.as_dict(),
                    "prepared_at": datetime.now(UTC).isoformat(),
                    "config_path": str(self.config_path) if self.config_path else None,
                    "data_dir": str(self.data_dir) if self.data_dir else None,
                    "compatibility": {
                        "current": str(compatibility.current_version),
                        "required": (
                            str(compatibility.required_version)
                            if compatibility.required_version
                            else None
                        ),
                    },
                }
                self.layout.write_pending(pending)
                pending_written = True
                self.audit.record(
                    "download",
                    current_version=self.current_version,
                    target_version=release.version,
                    result="success",
                    source=release.source,
                    target_tag=release.tag,
                    target_path=str(final_path),
                    verification=(
                        "commit_sha" if release.commit_sha else "asset_sha256"
                    ),
                    compatibility=compatibility.reason,
                    compatibility_legacy=compatibility.legacy,
                )
                return PrepareResult(release, final_path, compatibility)
            except Exception as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self.audit.record(
                    "download",
                    current_version=self.current_version,
                    target_version=release.version,
                    result="failed",
                    source=release.source,
                    target_tag=release.tag,
                    error=str(exc) or exc.__class__.__name__,
                )
                if final_path is not None and not pending_written:
                    shutil.rmtree(final_path, ignore_errors=True)
                shutil.rmtree(stage_root, ignore_errors=True)
                raise

    def _record_backup_audit(
        self,
        target_version: Version,
        result: str,
        **details: Any,
    ) -> None:
        try:
            self.audit.record(
                "backup",
                current_version=self.current_version,
                target_version=target_version,
                result=result,
                **details,
            )
        except OSError:
            logger.exception("Tenko database backup audit write failed")

    def _prune_database_backups(self, keep: int) -> str | None:
        source = self.database_path
        try:
            candidates = sorted(
                (
                    path
                    for path in source.parent.glob(f"{source.name}.bak-*")
                    if path.is_file()
                ),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            for path in candidates[keep:]:
                path.unlink()
        except OSError as exc:
            return str(exc) or exc.__class__.__name__
        return None

    def _backup_before_install(self, target_version: Version) -> Path | None:
        if not self.database_backup_enabled:
            self._record_backup_audit(
                target_version,
                "skipped",
                reason="database URL is not a file-backed SQLite database",
            )
            return None

        source = self.database_path
        if not source.is_file():
            self._record_backup_audit(
                target_version,
                "skipped",
                source_path=str(source),
                reason="database file does not exist",
            )
            return None

        # 先腾出位置再写新备份，磁盘上同时最多只有 DATABASE_BACKUPS_KEEP 份。
        retention_error = self._prune_database_backups(DATABASE_BACKUPS_KEEP - 1)
        if retention_error:
            logger.error(
                "Tenko database backup retention cleanup failed: {}",
                retention_error,
            )

        try:
            needed = source.stat().st_size + DATABASE_BACKUP_FREE_MARGIN
            free = shutil.disk_usage(source.parent).free
        except OSError:
            needed = free = 0
        if free < needed:
            logger.error(
                "Tenko database backup skipped: {} free, {} needed", free, needed
            )
            self._record_backup_audit(
                target_version,
                "skipped",
                source_path=str(source),
                reason=f"insufficient disk space: {free} bytes free, {needed} needed",
            )
            return None

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = source.with_name(
            f"{source.name}.bak-{target_version}-{timestamp}"
        )
        try:
            backup_sqlite_database(source, destination)
        except (OSError, sqlite3.Error) as exc:
            reason = str(exc) or exc.__class__.__name__
            logger.error("Tenko database backup failed for {}: {}", source, reason)
            self._record_backup_audit(
                target_version,
                "failed",
                source_path=str(source),
                backup_path=str(destination),
                error=reason,
            )
            return None

        details: dict[str, Any] = {
            "source_path": str(source),
            "backup_path": str(destination),
        }
        if retention_error:
            details["retention_error"] = retention_error
        self._record_backup_audit(target_version, "success", **details)
        return destination

    async def request_install(self) -> HandoffResult:
        refuse_in_sandbox("升级 Tenko")
        async with self._operation_lock:
            pending = self.layout.read_pending()
            if pending is None:
                raise NoUpdateAvailable("没有已通过校验的待安装版本")
            version_value = pending["version"]
            version = parse_version(version_value)
            version_text = str(version)
            target_tag = _pending_release_tag(pending, version_text)
            target = Path(str(pending["path"])).resolve()
            backup_path = self._backup_before_install(version)
            handoff = {
                "action": "activate",
                "version": version_text,
                "path": str(target),
                "automatic_rollback": True,
                "created_at": datetime.now(UTC).isoformat(),
                "config_path": str(self.config_path) if self.config_path else None,
                "data_dir": str(self.data_dir) if self.data_dir else None,
            }
            self.layout.write_handoff(handoff)
            self.audit.record(
                "install",
                current_version=self.current_version,
                target_version=version,
                result="requested",
                handoff_path=str(self.layout.handoff_file),
                restart_required=True,
                target_tag=target_tag,
                database_backup_path=str(backup_path) if backup_path else None,
            )
            return HandoffResult("activate", version, target)

    async def rollback(self) -> RollbackResult:
        refuse_in_sandbox("回滚 Tenko")
        async with self._operation_lock:
            previous = self.layout.read_previous()
            if previous is None:
                self.audit.record(
                    "rollback",
                    current_version=self.current_version,
                    result="failed",
                    error="没有可回滚版本",
                )
                raise NoRollbackAvailable("没有可回滚的上一可用版本")
            current = self.layout.read_active()
            if current is None:
                self.audit.record(
                    "rollback",
                    current_version=self.current_version,
                    target_version=previous.version,
                    result="failed",
                    error="回滚前缺少 active 指针",
                )
                raise UpgradeConfigError("回滚前缺少 active 指针")
            self.layout.write_handoff(
                {
                    "action": "rollback",
                    "version": str(previous.version),
                    "path": str(previous.path),
                    "from_version": str(current.version),
                    "from_path": str(current.path),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            self.audit.record(
                "rollback",
                current_version=self.current_version,
                target_version=previous.version,
                result="requested",
                handoff_path=str(self.layout.handoff_file),
                restart_required=True,
            )
            return RollbackResult(
                True,
                previous.version,
                previous.path,
                False,
                "已生成外部重启回滚记录",
            )

    def _snapshot_current(self) -> ReleasePointer:
        if not self.project_root.is_dir():
            raise UpgradeConfigError(f"当前项目目录不存在: {self.project_root}")
        self.layout.ensure()
        baseline = self.layout.new_version_path(self.current_version, "baseline-")
        excluded = {
            ".git",
            ".venv",
            ".venv-entari",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
        }
        for path in (self.config_path, self.data_dir, self.layout.root):
            if path is None:
                continue
            try:
                relative = path.resolve().relative_to(self.project_root)
            except ValueError:
                continue
            if relative.parts:
                excluded.add(relative.parts[0])

        def ignore(_directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in excluded}

        try:
            shutil.copytree(self.project_root, baseline, ignore=ignore)
        except Exception:
            shutil.rmtree(baseline, ignore_errors=True)
            raise
        return ReleasePointer(self.current_version, baseline)

    async def _health(self, candidate: Path, *, phase: str) -> None:
        result = await self.health_checker.check(candidate, phase=phase)
        if not _health_ok(result):
            raise HealthCheckFailed(_health_reason(result))

    async def _sync_dependencies(
        self,
        release: Path,
        *,
        strict: bool = True,
        leaving: Path | None = None,
        restore: bool = False,
    ) -> None:
        """同步宿主依赖，再重新应用并校验插件锁。

        ``strict`` 时插件环境不兼容会让切换失败并恢复旧版本（升级的默认行为）；
        回滚是紧急出口，不能被插件挡住，所以只记录审计，由 Master 事后处理。
        ``leaving`` 和 ``restore`` 交给插件管理器：保存离开版本的插件锁，回到
        某个版本时恢复它当时的插件锁。
        """

        if self.dependency_sync is not None:
            logger.info("Tenko 正在按 {} 的 uv.lock 同步依赖", release)
            await self.dependency_sync(release)
        if self.plugin_sync is None:
            return
        try:
            await self.plugin_sync(release, leaving=leaving, restore=restore)
        except Exception as exc:
            reason = _redact_text(str(exc) or exc.__class__.__name__)
            if strict:
                raise DependencySyncError(f"插件环境检查失败: {reason}") from exc
            logger.error("插件环境检查失败（已忽略，请执行 /同步插件环境）: {}", reason)
            self.audit.record("plugin-sync", result="failed", error=reason)

    async def _restore_dependencies(self, release: Path, leaving: Path) -> str:
        """切换失败后尽力把依赖恢复到仍在运行的版本；返回要追加到原因里的说明。"""

        try:
            await self._sync_dependencies(
                release, strict=False, leaving=leaving, restore=True
            )
        except Exception as exc:
            error = _redact_text(str(exc) or exc.__class__.__name__)
            logger.error(
                "Tenko 恢复旧版本依赖失败，环境可能与运行版本不一致: {}", error
            )
            return f"；恢复旧版本依赖也失败: {error}"
        return ""

    @staticmethod
    def _same_pointer(left: ReleasePointer, right: ReleasePointer) -> bool:
        return left.path == right.path and left.version == right.version

    @staticmethod
    def _pointer_data(pointer: ReleasePointer) -> dict[str, str]:
        return {
            "version": str(pointer.version),
            "path": str(pointer.path),
        }

    def _pointer_from_data(
        self, data: Mapping[str, Any], *, label: str
    ) -> ReleasePointer:
        try:
            version = parse_version(data["version"])
            path_value = data["path"]
            if not isinstance(path_value, str) or not path_value:
                raise TypeError("path 必须是非空字符串")
            path = Path(path_value).expanduser().resolve()
            self.layout._validate_version_path(path)
        except (KeyError, TypeError, InvalidVersionError, ValueError) as exc:
            raise UpgradeConfigError(f"{label} 的版本或路径无效") from exc
        return ReleasePointer(version, path)

    def _activation_recovery(self, pending: Mapping[str, Any]) -> ReleasePointer | None:
        value = pending.get("activation_recovery")
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise UpgradeConfigError("pending 激活恢复记录必须是 object")
        source = value.get("from")
        if source is None:
            source = {
                "version": value.get("from_version"),
                "path": value.get("from_path"),
            }
        if not isinstance(source, Mapping):
            raise UpgradeConfigError("pending 激活恢复记录缺少旧版本指针")
        return self._pointer_from_data(source, label="pending 激活恢复记录")

    def _record_matches_pointer(
        self, record: Mapping[str, Any], pointer: ReleasePointer, *, label: str
    ) -> bool:
        try:
            candidate = self._pointer_from_data(record, label=label)
        except UpgradeConfigError:
            return False
        return self._same_pointer(candidate, pointer)

    def _write_activation_recovery(
        self, pending: Mapping[str, Any], source: ReleasePointer
    ) -> None:
        updated = dict(pending)
        updated["activation_recovery"] = {
            "phase": "before-switch",
            "from": self._pointer_data(source),
        }
        self.layout.write_pending(updated)

    def _write_automatic_recovery_notice(
        self,
        version: Version,
        *,
        reason: str,
    ) -> None:
        try:
            self.layout.write_recovery_notice(version, reason=reason)
        except OSError as exc:
            logger.error(
                "Tenko automatic rollback notification marker write failed: {}",
                exc,
            )

    def _rollback_source(
        self,
        handoff: Mapping[str, Any] | None,
        current: ReleasePointer,
        target: ReleasePointer,
    ) -> ReleasePointer:
        if handoff is None:
            if current.path == target.path:
                raise UpgradeConfigError("回滚恢复记录缺少原 active 指针")
            return current

        has_from = "from_version" in handoff or "from_path" in handoff
        if not has_from:
            if current.path == target.path:
                raise UpgradeConfigError("回滚 handoff 缺少原 active 指针")
            return current
        source = self._pointer_from_data(
            {
                "version": handoff.get("from_version"),
                "path": handoff.get("from_path"),
            },
            label="回滚 handoff 的原 active 指针",
        )
        if source.path == target.path:
            raise UpgradeConfigError("回滚 handoff 的原 active 指针不能等于目标版本")
        if not (
            self._same_pointer(current, source) or self._same_pointer(current, target)
        ):
            raise UpgradeConfigError("回滚 handoff 与当前 active 指针不一致")
        return source

    def _rollback_handoff(
        self,
        handoff: Mapping[str, Any] | None,
        target: ReleasePointer,
        source: ReleasePointer,
    ) -> Mapping[str, Any]:
        data = dict(handoff or {})
        data.update(
            {
                "action": "rollback",
                "version": str(target.version),
                "path": str(target.path),
                "from_version": str(source.version),
                "from_path": str(source.path),
                "phase": "before-switch",
            }
        )
        self.layout.write_handoff(data)
        return data

    def _discard_live_handoff(self, *, isolate: bool) -> tuple[Path | None, str | None]:
        if not isolate:
            self.layout.clear_handoff()
            return None, None
        try:
            return self.layout.isolate_handoff(), None
        except OSError as exc:
            error = _redact_text(str(exc) or exc.__class__.__name__)
            try:
                self.layout.clear_handoff()
            except OSError as clear_exc:
                error = (
                    f"{error}; 清除 live handoff 也失败："
                    f"{_redact_text(str(clear_exc) or clear_exc.__class__.__name__)}"
                )
            return None, error

    def _failed_handoff_result(self, reason: str) -> InstallResult:
        active_path = None
        target_version = self.current_version
        try:
            active = self.layout.read_active()
        except UpgradeConfigError:
            active = None
        if active is not None:
            target_version = active.version
            active_path = active.path
        return InstallResult(
            False,
            target_version,
            active_path,
            False,
            _redact_text(reason),
        )

    def _quarantine_handoff(
        self,
        reason: str,
        *,
        action: object = None,
        clear_pending: bool = False,
    ) -> InstallResult:
        failed_path, quarantine_error = self._discard_live_handoff(isolate=True)
        pending_error = None
        if clear_pending:
            try:
                self.layout.clear_pending()
            except OSError as exc:
                pending_error = _redact_text(str(exc) or exc.__class__.__name__)
        safe_reason = _redact_text(reason)
        details: dict[str, Any] = {
            "handoff_action": _redact_text(action) if action is not None else None,
            "failed_handoff_path": str(failed_path) if failed_path else None,
        }
        if quarantine_error:
            details["quarantine_error"] = quarantine_error
        if pending_error:
            details["pending_cleanup_error"] = pending_error
        self.audit.record(
            "rollback" if action == "rollback" else "install",
            current_version=self.current_version,
            result="failed",
            error=safe_reason,
            **details,
        )
        return self._failed_handoff_result(safe_reason)

    async def activate_pending(self) -> InstallResult:
        """由外部启动器在旧进程退出后调用，原子激活 pending 制品。"""

        async with self._operation_lock:
            pending = self.layout.read_pending()
            if pending is None:
                raise NoUpdateAvailable("没有待激活版本")
            target_version_value = pending["version"]
            target_version = parse_version(target_version_value)
            target_version_text = str(target_version)
            target_tag = _pending_release_tag(pending, target_version_text)
            target_path = Path(str(pending["path"])).resolve()
            self.layout._validate_version_path(target_path)
            target = ReleasePointer(target_version, target_path)
            handoff = self.layout.read_handoff()
            automatic_rollback = bool(
                handoff is not None and handoff.get("automatic_rollback", False)
            )
            recovery = self._activation_recovery(pending)
            old = self.layout.read_active()
            previous_before = self.layout.read_previous()
            if old is None:
                old = self._snapshot_current()
                self.layout.write_pointer(old.path, old.version)
            already_active = self._same_pointer(old, target)
            if old.path == target.path and not already_active:
                raise UpgradeConfigError("active 指针与待安装版本路径冲突")
            if already_active:
                if recovery is None or recovery.path == target.path:
                    raise UpgradeConfigError(
                        "active 已指向待安装版本，但 pending 的旧版本恢复信息无效"
                    )
                source = recovery
            else:
                source = old
                # 先把恢复指针写入 pending，再替换 active。这样 active 已切换
                # 但整理尚未完成时，下一次启动仍能恢复到真正的旧版本。
                self._write_activation_recovery(pending, source)
            self.audit.record(
                "install",
                current_version=source.version,
                target_version=target_version,
                result="started",
                target_path=str(target_path),
                target_tag=target_tag,
            )
            try:
                # active.json 的替换是唯一切换点；应用根目录从不被覆盖。
                if not already_active:
                    self.layout.write_pointer(target_path, target_version)
                await self._sync_dependencies(target_path, leaving=source.path)
                await self._health(target_path, phase="post-switch")
            except Exception as exc:
                self.layout.write_pointer(source.path, source.version)
                if previous_before is None:
                    self.layout.clear_previous()
                else:
                    self.layout.write_pointer(
                        previous_before.path,
                        previous_before.version,
                        previous=True,
                    )
                self.layout.clear_pending()
                self.layout.clear_handoff()
                reason = (str(exc) or exc.__class__.__name__) + (
                    await self._restore_dependencies(source.path, target_path)
                )
                logger.error(
                    "Tenko 升级到 {} 失败，已恢复 {}: {}",
                    target_tag,
                    source.version,
                    reason,
                )
                self.audit.record(
                    "install",
                    current_version=source.version,
                    target_version=target_version,
                    result="failed",
                    error=reason,
                    target_tag=target_tag,
                )
                self.audit.record(
                    "rollback",
                    current_version=target_tag,
                    target_version=source.version,
                    result="success",
                    reason=_redact_text(reason),
                    automatic=automatic_rollback,
                )
                if automatic_rollback:
                    self._write_automatic_recovery_notice(
                        source.version,
                        reason=_redact_text(reason),
                    )
                return InstallResult(False, target_version, source.path, True, reason)

            self.layout.write_pointer(source.path, source.version, previous=True)
            self.layout.clear_pending()
            self.layout.clear_handoff()
            self.audit.record(
                "install",
                current_version=source.version,
                target_version=target_version,
                result="success",
                active_path=str(target_path),
                previous_path=str(source.path),
                target_tag=target_tag,
            )
            return InstallResult(True, target_version, target_path, False, "升级已激活")

    async def _apply_rollback_unlocked(
        self,
        *,
        handoff: Mapping[str, Any] | None = None,
    ) -> RollbackResult:
        previous = self.layout.read_previous()
        current = self.layout.read_active()
        if previous is None:
            raise NoRollbackAvailable("没有可回滚的上一可用版本")
        if current is None:
            raise UpgradeConfigError("回滚前缺少 active 指针")
        source = self._rollback_source(handoff, current, previous)
        already_active = self._same_pointer(current, previous)
        if handoff is None:
            handoff = self._rollback_handoff(None, previous, source)
        else:
            handoff = self._rollback_handoff(handoff, previous, source)
        try:
            if not already_active:
                self.layout.write_pointer(previous.path, previous.version)
            await self._sync_dependencies(
                previous.path, strict=False, leaving=source.path, restore=True
            )
            await self._health(previous.path, phase="rollback")
        except Exception as exc:
            self.layout.write_pointer(source.path, source.version)
            failed_handoff, quarantine_error = self._discard_live_handoff(isolate=True)
            reason = (str(exc) or exc.__class__.__name__) + (
                await self._restore_dependencies(source.path, previous.path)
            )
            logger.error(
                "Tenko 回滚到 {} 失败，已恢复 {}: {}",
                previous.version,
                source.version,
                _redact_text(reason),
            )
            details: dict[str, Any] = {
                "error": _redact_text(reason),
                "failed_handoff_path": (
                    str(failed_handoff) if failed_handoff else None
                ),
                "automatic": bool(
                    handoff is not None and handoff.get("automatic_rollback", False)
                ),
            }
            if quarantine_error:
                details["quarantine_error"] = quarantine_error
            self.audit.record(
                "rollback",
                current_version=source.version,
                target_version=previous.version,
                result="failed",
                **details,
            )
            return RollbackResult(
                False,
                previous.version,
                previous.path,
                True,
                _redact_text(reason),
            )
        self.layout.write_pointer(source.path, source.version, previous=True)
        self.layout.clear_pending()
        self.layout.clear_handoff()
        self.audit.record(
            "rollback",
            current_version=source.version,
            target_version=previous.version,
            result="success",
            active_path=str(previous.path),
            previous_path=str(source.path),
            automatic=bool(
                handoff is not None and handoff.get("automatic_rollback", False)
            ),
        )
        if handoff is not None and handoff.get("automatic_rollback", False):
            self._write_automatic_recovery_notice(
                previous.version,
                reason=str(
                    handoff.get("reason", "startup failure") or "startup failure"
                ),
            )
        return RollbackResult(
            True, previous.version, previous.path, True, "已回滚到上一版本"
        )

    async def apply_handoff(self) -> InstallResult | RollbackResult:
        """应用外部启动器写入的 activate/rollback 记录。"""

        try:
            handoff = self.layout.read_handoff()
        except UpgradeConfigError as exc:
            return self._quarantine_handoff(str(exc))
        if handoff is None:
            raise UpgradeConfigError("没有待处理的 handoff")
        action = handoff.get("action")
        if action == "activate":
            try:
                pending = self.layout.read_pending()
                if pending is None or not self._record_matches_pointer(
                    handoff,
                    self._pointer_from_data(pending, label="pending 升级记录"),
                    label="activate handoff",
                ):
                    raise UpgradeConfigError("activate handoff 与 pending 记录不一致")
            except UpgradeConfigError as exc:
                return self._quarantine_handoff(
                    str(exc), action=action, clear_pending=True
                )
            try:
                return await self.activate_pending()
            except UpdaterError as exc:
                return self._quarantine_handoff(
                    str(exc), action=action, clear_pending=True
                )
        if action == "rollback":
            try:
                previous = self.layout.read_previous()
                if previous is None or not self._record_matches_pointer(
                    handoff, previous, label="rollback handoff"
                ):
                    raise UpgradeConfigError("rollback handoff 与上一版本指针不一致")
            except UpgradeConfigError as exc:
                return self._quarantine_handoff(str(exc), action=action)
            try:
                async with self._operation_lock:
                    return await self._apply_rollback_unlocked(handoff=handoff)
            except UpdaterError as exc:
                return self._quarantine_handoff(str(exc), action=action)
        return self._quarantine_handoff(
            f"未知 handoff action: {_redact_text(action)!r}", action=action
        )

    async def run_policy(self) -> CheckResult | PrepareResult | HandoffResult:
        """执行配置化自动策略，供 Ready/周期任务调用。"""

        checked = await self.check()
        if not checked.candidate or self.policy is UpdatePolicy.CHECK_ONLY:
            return checked
        prepared = await self.prepare(checked.candidate)
        if self.policy is UpdatePolicy.AUTO_DOWNLOAD:
            return prepared
        return await self.request_install()

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        project_root: str | Path | None = None,
        database_url: str | Path | None = None,
    ) -> UpgradeManager:
        """从 ``tenko.config.UpgradeConfig`` 构造真实运行时实例。

        ``data_dir`` 仅供自定义健康检查命令使用，是外部业务目录；它与
        Tenko 应用自身位于 ``.tenko/`` 下的持久化数据无关。
        """

        root = Path(project_root or Path.cwd()).expanduser().resolve()
        database_path: Path | None
        if database_url is None:
            database_path = root / ".tenko" / "tenko.db"
            database_backup_enabled = True
        else:
            database_path = resolve_sqlite_database_path(database_url, root)
            database_backup_enabled = database_path is not None
        configured_install_root = (
            os.environ.get("TENKO_UPGRADE_ROOT") or config.install_root
        )
        install_root = Path(configured_install_root).expanduser()
        if not install_root.is_absolute():
            install_root = root / install_root
        layout = UpgradeLayout(install_root)
        active = layout.read_active()
        stable_version: Version | None = None
        if active is not None:
            try:
                stable_version = read_project_version(root)
            except UpgradeConfigError:
                # 有效 active 指针本身足以支持旧版部署目录；稳定根缺少
                # pyproject.toml 时保留原有 active 优先行为。
                pass
        if (
            active is not None
            and stable_version is not None
            and active.version < stable_version
        ):
            current = stable_version
        elif active is not None:
            current = active.version
        else:
            current = (
                config.current_version or stable_version or read_project_version(root)
            )
        # 停用时不构造版本源；版本源配置错误（如 zip 解压而非 git clone、没装 git）
        # 只让检查更新不可用，不阻断启动。
        source: VersionSource | None = None
        source_error = None
        if config.enabled:
            try:
                source_name = config.source.strip().lower().replace("-", "_")
                if source_name in {"git", "git_tag", "tag"}:
                    source = GitTagSource(
                        _normalize_git_repository(config.repository, root),
                        tag_prefix=config.tag_prefix,
                    )
                elif source_name in {"github", "github_release", "release"}:
                    repository = config.github_repository or config.repository
                    if repository in {"", "."}:
                        raise UpgradeConfigError(
                            "github_release 需要 github_repository"
                        )
                    source = GitHubReleaseSource(
                        repository,
                        token=config.github_token,
                        asset_name=config.asset_name,
                        tag_prefix=config.tag_prefix,
                    )
                elif source_name in {"manifest", "url_manifest", "url"}:
                    if not config.manifest_url:
                        raise UpgradeConfigError("manifest 源需要 manifest_url")
                    source = UrlManifestSource(
                        config.manifest_url,
                        tag_prefix=config.tag_prefix,
                    )
                else:
                    raise UpgradeConfigError(f"未知升级 source: {config.source!r}")
            except UpgradeConfigError as error:
                logger.warning("升级版本源不可用，检查更新已停用: {}", error)
                source, source_error = None, f"升级版本源不可用：{error}"

        configured_config_path = (
            os.environ.get("TENKO_CONFIG_PATH") or config.config_path
        )
        config_path = Path(configured_config_path).expanduser()
        if not config_path.is_absolute():
            config_path = root / config_path
        configured_data_dir = os.environ.get("TENKO_DATA_DIR") or config.data_dir
        data_dir = Path(configured_data_dir).expanduser()
        if not data_dir.is_absolute():
            data_dir = root / data_dir
        # plugin_manager 依赖本模块，延迟导入避免循环。
        from ..plugins.manager import PluginManager

        plugins = PluginManager()
        health_checker = DefaultHealthChecker(
            config.health_command,
            timeout=config.health_timeout,
            config_path=config_path.resolve(),
            data_dir=data_dir.resolve(),
            upgrade_root=layout.root,
            stable_root=root,
            import_check=True,
        )
        manager = cls(
            current,
            source,
            layout=layout,
            project_root=root,
            config_path=config_path,
            data_dir=data_dir,
            config_version=config.config_version,
            channel=config.channel,
            policy=config.policy,
            enabled=config.enabled,
            check_interval_hours=config.check_interval_hours,
            restart_watch_timeout=config.restart_watch_timeout,
            database_path=database_path,
            database_backup_enabled=database_backup_enabled,
            health_checker=health_checker,
            dependency_sync=UvDependencySync(),
            plugin_sync=plugins.reconcile,
            plugin_check=plugins.incompatible,
        )
        manager.source_error = source_error
        return manager


def _default_manager() -> UpgradeManager:
    try:
        version = read_project_version(Path.cwd())
    except UpgradeConfigError:
        version = parse_version("0.0.0")
    return UpgradeManager(
        version,
        layout=UpgradeLayout(Path(".tenko/upgrades")),
        enabled=False,
    )


_configured_manager = _default_manager()
_configured_permission_checker = PermissionChecker(masters=())


def configure_updater(
    manager: UpgradeManager,
    *,
    superuser_ids: Sequence[str | int] = (),
    shutdown_callback: Callable[[], bool] | None = None,
) -> None:
    """运行时在加载插件前注入管理器、权限和生命周期回调。"""

    global _configured_manager, _configured_permission_checker
    manager.configure_shutdown(shutdown_callback)
    _configured_manager = manager
    _configured_permission_checker = PermissionChecker(masters=superuser_ids)


def get_upgrade_manager() -> UpgradeManager:
    return _configured_manager


def get_upgrade_permission_checker() -> PermissionChecker:
    return _configured_permission_checker
