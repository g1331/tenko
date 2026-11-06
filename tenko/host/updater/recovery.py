"""外部启动链路调用的启动失败观察与自动回滚函数。

非 systemd 部署由 detached watcher 观察新进程；systemd 部署由
``scripts/launcher.sh`` 在每次启动前调用 :func:`track_systemd_startup`。
两条路径都在稳定根目录的独立 Python 进程中运行，不加载候选版本的代码。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from loguru import logger
from packaging.version import Version

from .audit import AuditLogger
from .layout import ReleasePointer, UpgradeLayout
from .models import (
    _STARTUP_FAILURE_THRESHOLD,
    InvalidVersionError,
    UpgradeConfigError,
    _redact_text,
    parse_version,
)


# launcher 确认自己就是 systemd 服务的 ExecStart 进程后设置，Tenko 继承。
# 只看 INVOCATION_ID 不够：在任何 systemd 服务里开的 shell 都会继承它。
SYSTEMD_ENVIRONMENT_VARIABLE = "TENKO_SYSTEMD"


def systemd_managed(environ: Mapping[str, str] = os.environ) -> bool:
    """当前进程是否由 systemd 服务经标准启动器拉起，退出后由 systemd 重启。"""

    return environ.get(SYSTEMD_ENVIRONMENT_VARIABLE) == "1"


def _best_effort_external_audit(
    audit: AuditLogger,
    action: str,
    *,
    current_version: Version | None = None,
    target_version: Version | None = None,
    result: str,
    **details: Any,
) -> None:
    try:
        audit.record(
            action,
            current_version=current_version,
            target_version=target_version,
            result=result,
            **details,
        )
    except OSError as exc:
        logger.error("Tenko automatic upgrade audit write failed: {}", exc)


def _external_layout_pointer(
    layout: UpgradeLayout,
    method_name: str,
) -> ReleasePointer | None:
    try:
        return getattr(layout, method_name)()
    except UpgradeConfigError:
        return None


def record_automatic_startup_failure(
    upgrade_root: str | Path,
    *,
    target_version: str | Version | None,
    reason: str,
    failed_attempts: int,
) -> None:
    """记录 watcher 观察到的候选版本启动失败。

    该函数由 detached watcher 在稳定根目录的独立 Python 进程中调用；审计
    失败只能影响留痕，不能让 watcher 因日志问题继续重启坏制品。
    """

    layout = UpgradeLayout(Path(upgrade_root))
    audit = AuditLogger(layout.audit_file)
    active = _external_layout_pointer(layout, "read_active")
    try:
        target = parse_version(target_version) if target_version is not None else None
    except InvalidVersionError:
        target = None
    _best_effort_external_audit(
        audit,
        "startup",
        current_version=active.version if active else None,
        target_version=target,
        result="failed",
        error=_redact_text(reason),
        failed_attempts=failed_attempts,
        watcher=True,
    )
    logger.error(
        "Tenko candidate startup failed (attempt {}): {}",
        failed_attempts,
        _redact_text(reason),
    )


def request_automatic_rollback(
    upgrade_root: str | Path,
    *,
    reason: str,
    failed_attempts: int,
) -> bool:
    """为 detached watcher 写入一次自动回滚 handoff。"""

    layout = UpgradeLayout(Path(upgrade_root))
    audit = AuditLogger(layout.audit_file)
    current = _external_layout_pointer(layout, "read_active")
    previous = _external_layout_pointer(layout, "read_previous")
    if current is None or previous is None:
        error = "自动回滚缺少 active 或 previous 指针"
        _best_effort_external_audit(
            audit,
            "rollback",
            current_version=current.version if current else None,
            target_version=previous.version if previous else None,
            result="failed",
            error=error,
            automatic=True,
            failed_attempts=failed_attempts,
        )
        logger.error("Tenko automatic rollback cannot start: {}", error)
        return False
    if current.path == previous.path:
        error = "自动回滚目标已经是 active 版本"
        _best_effort_external_audit(
            audit,
            "rollback",
            current_version=current.version,
            target_version=previous.version,
            result="failed",
            error=error,
            automatic=True,
            failed_attempts=failed_attempts,
        )
        logger.error("Tenko automatic rollback cannot start: {}", error)
        return False

    try:
        existing = layout.read_handoff()
    except UpgradeConfigError:
        existing = None
    if existing is not None and existing.get("action") == "rollback":
        if existing.get("automatic_rollback", False):
            return True

    try:
        layout.write_handoff(
            {
                "action": "rollback",
                "version": str(previous.version),
                "path": str(previous.path),
                "from_version": str(current.version),
                "from_path": str(current.path),
                "automatic_rollback": True,
                "reason": _redact_text(reason),
                "failed_attempts": failed_attempts,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
    except OSError as exc:
        error = str(exc) or exc.__class__.__name__
        _best_effort_external_audit(
            audit,
            "rollback",
            current_version=current.version,
            target_version=previous.version,
            result="failed",
            error=_redact_text(error),
            automatic=True,
            failed_attempts=failed_attempts,
        )
        logger.error("Tenko automatic rollback handoff write failed: {}", error)
        return False

    _best_effort_external_audit(
        audit,
        "rollback",
        current_version=current.version,
        target_version=previous.version,
        result="requested",
        handoff_path=str(layout.handoff_file),
        automatic=True,
        failed_attempts=failed_attempts,
        reason=_redact_text(reason),
    )
    return True


def record_automatic_rollback_failure(
    upgrade_root: str | Path,
    *,
    target_version: str | Version | None,
    reason: str,
    failed_attempts: int,
) -> None:
    """记录回滚目标也未能在宽限期内存活，并隔离剩余 handoff。"""

    layout = UpgradeLayout(Path(upgrade_root))
    audit = AuditLogger(layout.audit_file)
    active = _external_layout_pointer(layout, "read_active")
    previous = _external_layout_pointer(layout, "read_previous")
    try:
        target = parse_version(target_version) if target_version is not None else None
    except InvalidVersionError:
        target = None
    if target is None and previous is not None:
        target = previous.version
    failed_handoff = None
    try:
        failed_handoff = layout.isolate_handoff()
    except OSError as exc:
        logger.error("Tenko failed rollback handoff quarantine failed: {}", exc)
    try:
        layout.clear_pending()
    except OSError as exc:
        logger.error("Tenko failed rollback pending cleanup failed: {}", exc)
    _best_effort_external_audit(
        audit,
        "rollback",
        current_version=active.version if active else None,
        target_version=target,
        result="failed",
        error=_redact_text(reason),
        automatic=True,
        failed_attempts=failed_attempts,
        failed_handoff_path=str(failed_handoff) if failed_handoff else None,
    )
    logger.error(
        "Tenko automatic rollback failed; manual recovery required: {}", reason
    )


def track_systemd_startup(
    upgrade_root: str | Path,
    *,
    threshold: int = _STARTUP_FAILURE_THRESHOLD,
) -> None:
    """systemd 部署下每次启动 Tenko 前调用，统计切换后候选版本的启动次数。

    看到 activate handoff 时开始观察，本次启动记为第 1 次。Tenko 在启动宽限期后
    仍在运行就清除观察记录；下次启动时记录还在，说明上一次没有撑过宽限期。
    连续 ``threshold`` 次都是这样时写入自动回滚 handoff，由本次启动的引导流程
    切回上一版本，并沿用回滚审计和“已自动回滚”播报。
    """

    layout = UpgradeLayout(Path(upgrade_root))
    try:
        handoff = layout.read_handoff()
    except UpgradeConfigError:
        # 损坏的 handoff 交给启动引导隔离，这里不据此开始观察。
        handoff = None
    if handoff is not None:
        if handoff.get("action") == "activate":
            try:
                target = ReleasePointer(
                    parse_version(handoff["version"]),
                    Path(str(handoff["path"])).expanduser().resolve(),
                )
            except (KeyError, InvalidVersionError):
                layout.clear_startup_probation()
                return
            layout.write_startup_probation(target, 1)
        else:
            layout.clear_startup_probation()
        return

    probation = layout.read_startup_probation()
    if probation is None:
        layout.clear_startup_probation()
        return
    candidate, attempts = probation
    active = _external_layout_pointer(layout, "read_active")
    if active is None or active.path != candidate.path:
        # 切换没有生效（例如切换后健康检查失败已恢复）或已被人工改动。
        layout.clear_startup_probation()
        return

    record_automatic_startup_failure(
        layout.root,
        target_version=candidate.version,
        reason="进程在启动宽限期内退出",
        failed_attempts=attempts,
    )
    if attempts < threshold:
        layout.write_startup_probation(candidate, attempts + 1)
        return
    layout.clear_startup_probation()
    reason = f"候选版本在启动宽限期内连续 {attempts} 次退出"
    if not request_automatic_rollback(
        layout.root, reason=reason, failed_attempts=attempts
    ):
        previous = _external_layout_pointer(layout, "read_previous")
        record_automatic_rollback_failure(
            layout.root,
            target_version=previous.version if previous else None,
            reason="无法写入自动回滚 handoff",
            failed_attempts=attempts,
        )


def main(argv: list[str] | None = None) -> int:
    """``scripts/launcher.sh`` 的入口：``python -c ... <upgrade_root>``。"""

    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("用法: track_systemd_startup <upgrade_root>", file=sys.stderr)
        return 2
    track_systemd_startup(arguments[0])
    return 0
