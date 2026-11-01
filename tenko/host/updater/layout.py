"""升级状态目录与 active/previous/pending/handoff 指针。"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from packaging.version import Version

from ..atomic import atomic_write_text
from .models import InvalidVersionError, UpgradeConfigError, parse_version


_FAILED_HANDOFF_RETENTION = 10


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    )


@dataclass(frozen=True, slots=True)
class ReleasePointer:
    version: Version
    path: Path


@dataclass(frozen=True, slots=True)
class UpgradeLayout:
    """升级状态目录。

    ``active.json`` 是外部启动器唯一需要读取的指针。它通过临时文件加
    ``os.replace`` 更新，启动器不会读到半写入的 JSON，也不会在应用目录中
    同时混用新旧文件。
    """

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"

    @property
    def active_file(self) -> Path:
        return self.root / "active.json"

    @property
    def previous_file(self) -> Path:
        return self.root / "previous.json"

    @property
    def pending_file(self) -> Path:
        return self.root / "pending.json"

    @property
    def handoff_file(self) -> Path:
        return self.root / "handoff.json"

    @property
    def failed_handoffs_dir(self) -> Path:
        return self.root / "failed-handoffs"

    @property
    def restart_watchers_dir(self) -> Path:
        return self.root / "restart-watchers"

    def restart_watcher_file(self, old_pid: int) -> Path:
        if old_pid <= 0:
            raise ValueError("旧进程 PID 必须大于 0")
        return self.restart_watchers_dir / f"{old_pid}.json"

    @property
    def audit_file(self) -> Path:
        return self.root / "audit.jsonl"

    @property
    def recovery_notice_file(self) -> Path:
        """自动回滚成功后交给新进程消费的一次性通知记录。"""

        return self.root / "recovery-notice.json"

    def ensure(self) -> None:
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def new_staging_dir(self) -> Path:
        self.ensure()
        return Path(tempfile.mkdtemp(prefix="stage-", dir=self.staging_dir))

    def new_version_path(self, version: Version, label: str = "") -> Path:
        # 目录名只需唯一；同一 tag 重复准备不能撞目录。
        return self.versions_dir / f"{version}-{label}{uuid.uuid4().hex[:12]}"

    def promote(self, staged: Path, version: Version) -> Path:
        self.ensure()
        staged = staged.resolve()
        if not staged.is_dir() or not staged.is_relative_to(self.staging_dir.resolve()):
            raise UpgradeConfigError("只能提升升级状态目录下的 staging 制品")
        target = self.new_version_path(version)
        os.replace(staged, target)
        return target

    def _pointer(self, path: Path) -> ReleasePointer | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pointer_path = Path(data["path"]).expanduser().resolve()
            version = parse_version(data["version"])
        except (
            OSError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            InvalidVersionError,
        ) as exc:
            raise UpgradeConfigError(f"升级指针损坏: {path}") from exc
        self._validate_version_path(pointer_path)
        return ReleasePointer(version, pointer_path)

    def _validate_version_path(self, path: Path) -> Path:
        path = path.resolve()
        versions_root = self.versions_dir.resolve()
        try:
            relative = path.relative_to(versions_root)
        except ValueError as exc:
            raise UpgradeConfigError(
                f"升级指针必须指向 versions 下的版本目录: {path}"
            ) from exc
        if len(relative.parts) != 1 or not path.is_dir():
            raise UpgradeConfigError(f"升级版本目录无效: {path}")
        return path

    def read_active(self) -> ReleasePointer | None:
        return self._pointer(self.active_file)

    def read_previous(self) -> ReleasePointer | None:
        return self._pointer(self.previous_file)

    def write_pointer(
        self, target: Path, version: Version, *, previous: bool = False
    ) -> None:
        target = self._validate_version_path(target)
        _atomic_write_json(
            self.previous_file if previous else self.active_file,
            {"version": str(version), "path": str(target)},
        )

    def read_pending(self) -> Mapping[str, Any] | None:
        if not self.pending_file.is_file():
            return None
        try:
            data = json.loads(self.pending_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeConfigError(
                f"pending 升级记录损坏: {self.pending_file}"
            ) from exc
        if not isinstance(data, Mapping):
            raise UpgradeConfigError("pending 升级记录必须是 object")
        try:
            parse_version(data["version"])
            candidate = Path(str(data["path"])).expanduser().resolve()
            self._validate_version_path(candidate)
        except (KeyError, TypeError, InvalidVersionError) as exc:
            raise UpgradeConfigError("pending 升级记录的版本或路径无效") from exc
        return data

    def write_pending(self, data: Mapping[str, Any]) -> None:
        _atomic_write_json(self.pending_file, data)

    def write_handoff(self, data: Mapping[str, Any]) -> None:
        _atomic_write_json(self.handoff_file, data)

    def read_handoff(self) -> Mapping[str, Any] | None:
        if not self.handoff_file.is_file():
            return None
        try:
            data = json.loads(self.handoff_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeConfigError(f"handoff 记录损坏: {self.handoff_file}") from exc
        if not isinstance(data, Mapping):
            raise UpgradeConfigError("handoff 记录必须是 object")
        return data

    def clear_pending(self) -> None:
        self.pending_file.unlink(missing_ok=True)

    def clear_handoff(self) -> None:
        self.handoff_file.unlink(missing_ok=True)

    def isolate_handoff(self) -> Path | None:
        """隔离一次不可处理的 handoff，并保留有限数量的诊断副本。"""

        if not self.handoff_file.is_file():
            return None
        self.failed_handoffs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = (
            self.failed_handoffs_dir / f"handoff-{timestamp}-{uuid.uuid4().hex}.json"
        )
        os.replace(self.handoff_file, target)
        files = [path for path in self.failed_handoffs_dir.iterdir() if path.is_file()]
        if len(files) > _FAILED_HANDOFF_RETENTION:
            files.sort(key=lambda path: path.stat().st_mtime_ns)
            for path in files[:-_FAILED_HANDOFF_RETENTION]:
                path.unlink(missing_ok=True)
        return target

    def clear_previous(self) -> None:
        self.previous_file.unlink(missing_ok=True)

    def write_recovery_notice(
        self,
        version: str | Version,
        *,
        reason: str = "startup failure",
        message: str | None = None,
    ) -> None:
        target_version = parse_version(version)
        _atomic_write_json(
            self.recovery_notice_file,
            {
                "version": str(target_version),
                "message": message or f"已自动回滚至 {target_version} 版本",
                "reason": reason,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    def clear_recovery_notice(self) -> None:
        self.recovery_notice_file.unlink(missing_ok=True)
