"""detached watcher 调用的自动回滚状态记录函数。"""

from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from loguru import logger
from packaging.version import Version

from .audit import AuditLogger
from .layout import ReleasePointer, UpgradeLayout
from .models import InvalidVersionError, UpgradeConfigError, _redact_text, parse_version


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
