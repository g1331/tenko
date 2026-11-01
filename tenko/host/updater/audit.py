"""追加式 JSON Lines 审计记录。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from loguru import logger
from packaging.version import Version

from .models import _version_text


class AuditLogger:
    """追加式 JSON Lines 审计记录。"""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.clock = clock or (lambda: datetime.now(UTC))

    def record(
        self,
        action: str,
        *,
        current_version: str | Version | None = None,
        target_version: str | Version | None = None,
        result: str,
        **details: Any,
    ) -> Mapping[str, Any]:
        timestamp = self.clock()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        target_tag = details.get("target_tag")
        payload: dict[str, Any] = {
            "timestamp": timestamp.astimezone(UTC).isoformat(),
            "action": action,
            "current_version": (
                _version_text(current_version) if current_version is not None else None
            ),
            "target_version": (
                target_tag
                if isinstance(target_tag, str)
                else (
                    _version_text(target_version)
                    if target_version is not None
                    else None
                )
            ),
            "result": result,
        }
        payload.update(details)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        logger.bind(upgrade_audit=payload).info("upgrade audit")
        return payload
