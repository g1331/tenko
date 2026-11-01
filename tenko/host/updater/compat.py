"""配置兼容性检查与候选版本 tenko-api 版本读取。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from packaging.version import Version

from .models import (
    ConfigurationCompatibilityError,
    InvalidVersionError,
    Release,
    parse_version,
)


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    current_version: Version
    required_version: Version | None
    compatible: bool
    reason: str
    legacy: bool = False


class ConfigCompatibilityChecker:
    """比较用户配置版本和新制品声明的最低兼容版本。"""

    manifest_names = ("tenko/upgrade-manifest.json", "upgrade-manifest.json")

    @classmethod
    def _manifest_path(cls, candidate: Path) -> Path | None:
        for relative in cls.manifest_names:
            path = candidate / relative
            if path.is_file():
                return path
        return None

    @classmethod
    def _required_from_manifest(cls, candidate: Path) -> Version | None:
        path = cls._manifest_path(candidate)
        if path is not None:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigurationCompatibilityError(
                    CompatibilityResult(
                        parse_version("0.0.0"),
                        None,
                        False,
                        f"升级兼容性清单不可读取: {path}",
                    )
                ) from exc
            if not isinstance(value, Mapping):
                raise ConfigurationCompatibilityError(
                    CompatibilityResult(
                        parse_version("0.0.0"),
                        None,
                        False,
                        f"升级兼容性清单必须是 object: {path}",
                    )
                )
            required = value.get(
                "min_config_version", value.get("required_config_version")
            )
            if required is None:
                return None
            try:
                return parse_version(required)
            except InvalidVersionError as exc:
                raise ConfigurationCompatibilityError(
                    CompatibilityResult(
                        parse_version("0.0.0"),
                        None,
                        False,
                        f"升级兼容性清单中的版本非法: {required!r}",
                    )
                ) from exc
        return None

    def check(
        self,
        current_config_version: str | Version,
        required_config_version: str | Version | None,
    ) -> CompatibilityResult:
        current = parse_version(current_config_version)
        required = (
            parse_version(required_config_version)
            if required_config_version is not None
            else None
        )
        if required is None:
            return CompatibilityResult(
                current,
                None,
                True,
                "新版本未声明更高的最低配置版本",
            )
        if current < required:
            return CompatibilityResult(
                current,
                required,
                False,
                f"当前配置版本 {current} 低于新版本要求的最低配置版本 {required}",
            )
        return CompatibilityResult(
            current,
            required,
            True,
            f"当前配置版本 {current} 满足最低要求 {required}",
        )

    def check_candidate(
        self,
        candidate: Path,
        current_config_version: str | Version,
        release: Release,
    ) -> CompatibilityResult:
        required_versions = [
            version
            for version in (
                release.required_config_version,
                self._required_from_manifest(candidate),
            )
            if version is not None
        ]
        required = max(required_versions) if required_versions else None
        result = self.check(current_config_version, required)
        if not required_versions and self._manifest_path(candidate) is None:
            result = replace(
                result,
                legacy=True,
                reason=f"legacy: {result.reason}",
            )
        if not result.compatible:
            raise ConfigurationCompatibilityError(result)
        return result


def tenko_api_version(host_root: Path) -> str | None:
    """读取某个宿主版本目录里 tenko-api 的版本（用于升级前预检）。"""

    path = host_root / "packages/tenko-api/src/tenko_api/__init__.py"
    try:
        matched = re.search(
            r'^__version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M
        )
    except OSError:
        return None
    return matched.group(1) if matched else None
