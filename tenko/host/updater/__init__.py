"""可验证、可回滚的 Tenko 宿主升级器。

这个模块只负责宿主升级的控制平面：发现版本、获取并校验制品、准备独立
目录、写入原子状态指针以及生成外部重启接管记录。它不会在当前 Python
进程中导入新目录或替换 ``sys.modules``；正在运行的宿主必须由外部启动器
调用 :meth:`UpgradeManager.apply_handoff` 完成重启后的切换。

插件分发不是本模块的职责。需要支持新的制品源时，实现 ``VersionSource``
协议并注入 ``UpgradeManager`` 即可，不需要改动升级状态机。
"""

from __future__ import annotations

from packaging.version import Version as Version

from .audit import (
    AuditLogger as AuditLogger,
)
from .compat import (
    CompatibilityResult as CompatibilityResult,
    ConfigCompatibilityChecker as ConfigCompatibilityChecker,
    tenko_api_version as tenko_api_version,
)
from .database import (
    backup_sqlite_database as backup_sqlite_database,
    DATABASE_BACKUP_FREE_MARGIN as DATABASE_BACKUP_FREE_MARGIN,
    DATABASE_BACKUPS_KEEP as DATABASE_BACKUPS_KEEP,
    resolve_sqlite_database_path as resolve_sqlite_database_path,
)
from ..uv import find_uv as find_uv
from .deps import UvDependencySync as UvDependencySync
from .health import (
    DefaultHealthChecker as DefaultHealthChecker,
    HealthCheckResult as HealthCheckResult,
)
from .layout import (
    ReleasePointer as ReleasePointer,
    UpgradeLayout as UpgradeLayout,
)
from .manager import (
    CheckResult as CheckResult,
    configure_updater as configure_updater,
    get_upgrade_manager as get_upgrade_manager,
    get_upgrade_permission_checker as get_upgrade_permission_checker,
    HandoffResult as HandoffResult,
    InstallResult as InstallResult,
    PrepareResult as PrepareResult,
    read_project_version as read_project_version,
    RollbackResult as RollbackResult,
    UpgradeManager as UpgradeManager,
)
from .models import (
    _redact_text as _redact_text,
    ArtifactAcquisitionError as ArtifactAcquisitionError,
    ArtifactVerificationError as ArtifactVerificationError,
    compare_versions as compare_versions,
    ConfigurationCompatibilityError as ConfigurationCompatibilityError,
    DependencySyncError as DependencySyncError,
    HealthCheckFailed as HealthCheckFailed,
    InvalidVersionError as InvalidVersionError,
    NoRollbackAvailable as NoRollbackAvailable,
    NoUpdateAvailable as NoUpdateAvailable,
    parse_version as parse_version,
    PluginCompatibilityError as PluginCompatibilityError,
    Release as Release,
    select_release as select_release,
    UpdateChannel as UpdateChannel,
    UpdatePolicy as UpdatePolicy,
    UpdateRelation as UpdateRelation,
    UpdaterError as UpdaterError,
    UpdateSourceError as UpdateSourceError,
    UpgradeConfigError as UpgradeConfigError,
    VersionSource as VersionSource,
)
from .recovery import (
    record_automatic_rollback_failure as record_automatic_rollback_failure,
    record_automatic_startup_failure as record_automatic_startup_failure,
    request_automatic_rollback as request_automatic_rollback,
)
from .sources import (
    GitHubReleaseSource as GitHubReleaseSource,
    GitTagSource as GitTagSource,
    HttpClient as HttpClient,
    UrllibHttpClient as UrllibHttpClient,
    UrlManifestSource as UrlManifestSource,
)
from .watcher import (
    spawn_restart_watcher as spawn_restart_watcher,
)


__all__ = [
    "ArtifactAcquisitionError",
    "ArtifactVerificationError",
    "AuditLogger",
    "backup_sqlite_database",
    "CheckResult",
    "CompatibilityResult",
    "ConfigurationCompatibilityError",
    "ConfigCompatibilityChecker",
    "DefaultHealthChecker",
    "DependencySyncError",
    "GitTagSource",
    "GitHubReleaseSource",
    "HealthCheckFailed",
    "HealthCheckResult",
    "HandoffResult",
    "InstallResult",
    "InvalidVersionError",
    "NoRollbackAvailable",
    "NoUpdateAvailable",
    "PrepareResult",
    "Release",
    "ReleasePointer",
    "RollbackResult",
    "UpdateChannel",
    "UpdatePolicy",
    "UpdateRelation",
    "UpdateSourceError",
    "UpdaterError",
    "UpgradeConfigError",
    "UpgradeLayout",
    "UpgradeManager",
    "UrlManifestSource",
    "UvDependencySync",
    "Version",
    "VersionSource",
    "compare_versions",
    "configure_updater",
    "get_upgrade_manager",
    "get_upgrade_permission_checker",
    "parse_version",
    "record_automatic_rollback_failure",
    "record_automatic_startup_failure",
    "request_automatic_rollback",
    "resolve_sqlite_database_path",
    "select_release",
    "spawn_restart_watcher",
]
