"""升级器的基础类型：错误、枚举、版本解析、``Release`` 与版本源协议。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from packaging.version import InvalidVersion as PackagingInvalidVersion
from packaging.version import Version


if TYPE_CHECKING:
    from .compat import CompatibilityResult


class UpdaterError(RuntimeError):
    """升级流程的公共错误基类。"""


class InvalidVersionError(UpdaterError, ValueError):
    """版本字符串不符合严格的 SemVer 形状。"""


class UpdateSourceError(UpdaterError):
    """版本源不可用或返回了不符合契约的数据。"""


class ArtifactAcquisitionError(UpdaterError):
    """制品无法获取或无法解包。"""


class ArtifactVerificationError(UpdaterError):
    """制品的强校验失败。"""


class ConfigurationCompatibilityError(UpdaterError):
    """当前用户配置低于新版本声明的最低兼容版本。"""

    def __init__(self, result: CompatibilityResult):
        self.result = result
        super().__init__(result.reason)


class HealthCheckFailed(UpdaterError):
    """制品在切换前或切换后健康检查失败。"""


class DependencySyncError(UpdaterError):
    """按版本锁文件同步依赖失败。"""


class PluginCompatibilityError(UpdaterError):
    """已安装的扩展插件与新版本的宿主依赖不兼容；``reasons`` 是插件 ID 到原因。

    ``api_version`` 是新版本宿主的 tenko-api 版本，用来挑选能装在新版本上的插件版本。
    """

    def __init__(
        self,
        version: Version,
        reasons: Mapping[str, str],
        api_version: str | None = None,
    ):
        self.reasons = dict(reasons)
        self.api_version = api_version
        lines = [
            f"- {'插件组合冲突' if pid == '*' else pid}：{_redact_text(why)[-300:]}"
            for pid, why in self.reasons.items()
        ]
        super().__init__(
            f"以下插件与 {version} 不兼容，已停止升级：\n" + "\n".join(lines)
        )


class UpgradeConfigError(UpdaterError, ValueError):
    """升级器配置无法构造。"""


class NoUpdateAvailable(UpdaterError):
    """当前通道没有高于本地版本的候选版本。"""


class NoRollbackAvailable(UpdaterError):
    """没有可以回滚到的上一版本。"""


class UpdateChannel(str, Enum):
    """版本通道。

    ``prerelease`` 通道同时允许正式版和预发布版；正式版通道显式排除
    SemVer 预发布版本，避免测试版本覆盖生产实例。
    """

    STABLE = "stable"
    PRERELEASE = "prerelease"
    PRE_RELEASE = "prerelease"

    @classmethod
    def parse(cls, value: str | UpdateChannel) -> UpdateChannel:
        if isinstance(value, cls):
            return value
        normalized = value.strip().lower().replace("_", "-")
        if normalized in {"stable", "release"}:
            return cls.STABLE
        if normalized in {"prerelease", "pre-release", "preview", "beta"}:
            return cls.PRERELEASE
        raise UpgradeConfigError("channel 必须是 stable 或 prerelease")


class UpdatePolicy(str, Enum):
    """自动化档位：只检查、自动下载、自动请求外部安装。"""

    CHECK_ONLY = "check"
    AUTO_DOWNLOAD = "download"
    AUTO_INSTALL = "install"
    MANUAL = "check"

    @classmethod
    def parse(cls, value: str | UpdatePolicy) -> UpdatePolicy:
        if isinstance(value, cls):
            return value
        normalized = value.strip().lower().replace("_", "-")
        aliases = {
            "check": cls.CHECK_ONLY,
            "check-only": cls.CHECK_ONLY,
            "manual": cls.CHECK_ONLY,
            "download": cls.AUTO_DOWNLOAD,
            "auto-download": cls.AUTO_DOWNLOAD,
            "install": cls.AUTO_INSTALL,
            "auto-install": cls.AUTO_INSTALL,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise UpgradeConfigError(
                "policy 必须是 check、download 或 install"
            ) from exc


class UpdateRelation(str, Enum):
    SAME = "same"
    UPDATE_AVAILABLE = "update-available"
    CURRENT_AHEAD = "current-ahead"


# ReDoS 缓解（CodeQL py/redos）：预发布段不允许嵌套量词回溯，改用
# 一次性匹配点分隔段（每段 [0-9A-Za-z-]+，须含字母或连字符），最后在
# Python 侧统一校验段语义，替代高回溯交替 (0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-]...)。
_VERSION_PATTERN = re.compile(
    r"^(?:[vV])?"
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
# 预发布段：每段非空、不含首尾点（正则已保证按点切分非空），且必须
# 包含至少一个字母或连字符（与 SemVer 规范一致，纯数字段也允许）。
_PRE_SEGMENT_PATTERN = re.compile(r"^[0-9A-Za-z-]+$")
_PRE_HAS_ALPHA = re.compile(r"[A-Za-z-]")


def _pre_release_valid(pre: str) -> bool:
    """校验预发布段：点分段每段合法，且至少一段含字母或连字符。"""

    segments = pre.split(".")
    if not all(_PRE_SEGMENT_PATTERN.fullmatch(s) for s in segments):
        return False
    return any(_PRE_HAS_ALPHA.search(s) for s in segments)


_HEX_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


_URL_CREDENTIALS_PATTERN = re.compile(
    r"(?P<prefix>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@"
)
_SCP_CREDENTIALS_PATTERN = re.compile(
    r"(?<![\w/@])[^/@\s:]+(?::[^/@\s]*)?@(?=[^/@\s:]+:)"
)


_RESTART_WATCHER_TIMEOUT = 300
_RESTART_WATCHER_INTERVAL = 0.2
_STARTUP_GRACE_PERIOD = 60.0
_STARTUP_FAILURE_THRESHOLD = 2


def _redact_text(value: object) -> str:
    """隐藏错误文本和地址中的凭据。"""

    text = str(value)
    text = _URL_CREDENTIALS_PATTERN.sub(r"\g<prefix>***@", text)
    return _SCP_CREDENTIALS_PATTERN.sub("***@", text)


def parse_version(value: str | Version) -> Version:
    """解析严格 SemVer 形状，并交由 packaging 提供版本比较。"""

    if isinstance(value, Version):
        return value
    if not isinstance(value, str):
        raise InvalidVersionError(f"版本必须是字符串: {value!r}")
    try:
        parsed = Version(value)
    except PackagingInvalidVersion as exc:
        raise InvalidVersionError(f"非法版本号: {value!r}") from exc
    # 原始输入继续要求三段 SemVer；同时接受 packaging 为持久化指针生成的
    # 规范形式（例如 ``4.0.0rc10``），避免规范化后的状态无法再次读取。
    matched = _VERSION_PATTERN.fullmatch(value)
    if matched is not None:
        pre = matched.group("pre")
        if pre is not None and not _pre_release_valid(pre):
            raise InvalidVersionError(f"非法版本号: {value!r}")
    elif len(parsed.release) != 3 or str(parsed) != value:
        raise InvalidVersionError(f"非法版本号: {value!r}")
    return parsed


def _version_text(value: str | Version) -> str:
    """校验版本并保留调用方传入的原始字符串用于外部记录。"""

    parsed = parse_version(value)
    return value if isinstance(value, str) else str(parsed)


def _pending_release_tag(pending: Mapping[str, Any], fallback: str) -> str:
    """从 pending 记录取原始 tag；旧记录缺失时回退到规范版本。"""

    tag = pending.get("tag")
    release = pending.get("release")
    if not isinstance(tag, str) and isinstance(release, Mapping):
        tag = release.get("tag")
    return tag if isinstance(tag, str) and tag else fallback


def compare_versions(current: str | Version, remote: str | Version) -> UpdateRelation:
    current_version = parse_version(current)
    remote_version = parse_version(remote)
    if remote_version == current_version:
        return UpdateRelation.SAME
    if remote_version > current_version:
        return UpdateRelation.UPDATE_AVAILABLE
    return UpdateRelation.CURRENT_AHEAD


@dataclass(frozen=True, slots=True)
class Release:
    """一个候选发布及其可验证制品信息。"""

    version: Version
    tag: str
    source: str = "unknown"
    commit_sha: str | None = None
    artifact_url: str | None = None
    artifact_sha256: str | None = None
    artifact_name: str | None = None
    prerelease: bool = False
    required_config_version: Version | None = None
    notes: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", parse_version(self.version))
        if not isinstance(self.tag, str) or not self.tag:
            raise ValueError("release tag 不能为空")
        if self.commit_sha is not None and (
            not isinstance(self.commit_sha, str)
            or not _HEX_SHA_PATTERN.fullmatch(self.commit_sha)
        ):
            raise ValueError("commit_sha 必须是完整 Git SHA")
        if self.artifact_sha256 is not None:
            if not isinstance(self.artifact_sha256, str):
                raise ValueError("artifact_sha256 必须是 SHA-256 十六进制摘要")
            digest = self.artifact_sha256.removeprefix("sha256:").lower()
            if not _SHA256_PATTERN.fullmatch(digest):
                raise ValueError("artifact_sha256 必须是 SHA-256 十六进制摘要")
            object.__setattr__(self, "artifact_sha256", digest)
        for value, name in (
            (self.artifact_url, "artifact_url"),
            (self.artifact_name, "artifact_name"),
        ):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} 必须是字符串")
        if self.required_config_version is not None:
            object.__setattr__(
                self,
                "required_config_version",
                parse_version(self.required_config_version),
            )
        object.__setattr__(
            self, "prerelease", self.prerelease or self.version.is_prerelease
        )
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        *,
        source: str = "manifest",
        default_tag: str | None = None,
    ) -> Release:
        try:
            version = data["version"]
        except KeyError as exc:
            raise UpdateSourceError("manifest release 缺少 version") from exc
        tag = data.get("tag", default_tag or str(version))
        if not isinstance(tag, str):
            raise UpdateSourceError("manifest release 的 tag 必须是字符串")
        sha = data.get("artifact_sha256", data.get("sha256"))
        commit_sha = data.get("commit_sha", data.get("commit"))
        required = data.get("required_config_version", data.get("min_config_version"))
        return cls(
            version=parse_version(version),
            tag=tag,
            source=source,
            commit_sha=commit_sha,
            artifact_url=data.get("artifact_url"),
            artifact_sha256=sha,
            artifact_name=data.get("artifact_name"),
            prerelease=bool(data.get("prerelease", False)),
            required_config_version=required,
            notes=str(data.get("notes", "")),
            metadata=data,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.tag,
            "tag": self.tag,
            "source": self.source,
            "commit_sha": self.commit_sha,
            "artifact_url": self.artifact_url,
            "artifact_sha256": self.artifact_sha256,
            "artifact_name": self.artifact_name,
            "prerelease": self.prerelease,
            "required_config_version": (
                str(self.required_config_version)
                if self.required_config_version
                else None
            ),
            "notes": self.notes,
        }


def select_release(
    releases: Sequence[Release],
    current: str | Version,
    channel: str | UpdateChannel,
) -> Release | None:
    """在指定通道中选择高于当前版本的最高候选。"""

    current_version = parse_version(current)
    selected_channel = UpdateChannel.parse(channel)
    candidates = [
        release
        for release in releases
        if release.version > current_version
        and (selected_channel is UpdateChannel.PRERELEASE or not release.prerelease)
    ]
    if not candidates:
        return None
    # 相同版本的多个 tag 不是升级理由；用 tag 排序使结果稳定、可审计。
    return max(candidates, key=lambda release: (release.version, release.tag))


class VersionSource(Protocol):
    """可插拔版本源与制品获取协议。"""

    name: str

    async def discover(self, channel: UpdateChannel) -> Sequence[Release]:
        """发现候选发布。"""

    async def acquire(self, release: Release, destination: Path) -> Path:
        """把发布制品物化到 ``destination`` 下并返回应用根目录。"""
