"""版本源：HTTP 客户端、Git tag、GitHub Release、URL manifest 与归档处理。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen

from packaging.version import Version

from .models import (
    ArtifactAcquisitionError,
    ArtifactVerificationError,
    InvalidVersionError,
    Release,
    UpdateChannel,
    UpdateSourceError,
    UpdaterError,
    UpgradeConfigError,
    _HEX_SHA_PATTERN,
    _redact_text,
    parse_version,
)


_GIT_SCP_LIKE_PATTERN = re.compile(r"^(?:[^/@\s:]+@)?[^/@\s:]+:.+$")
_GIT_REMOTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class HttpClient(Protocol):
    async def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Any:
        """获取 JSON。"""

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """下载到指定文件。"""


class UrllibHttpClient:
    """标准库 HTTP 实现，网络调用放到线程避免阻塞 Entari 事件循环。"""

    def __init__(self, timeout: float = 30) -> None:
        if timeout <= 0:
            raise ValueError("HTTP timeout 必须大于 0")
        self.timeout = timeout

    @staticmethod
    def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        result = {"User-Agent": "Tenko-Updater/1"}
        if headers:
            result.update(headers)
        return result

    def _get_json_sync(self, url: str, headers: Mapping[str, str] | None) -> Any:
        request = Request(url, headers=self._headers(headers))
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    async def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> Any:
        try:
            return await asyncio.to_thread(self._get_json_sync, url, headers)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise UpdateSourceError(f"无法读取 {url}: {exc}") from exc

    def _download_sync(
        self,
        url: str,
        destination: Path,
        headers: Mapping[str, str] | None,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.part"
        )
        request = Request(url, headers=self._headers(headers))
        try:
            with (
                urlopen(request, timeout=self.timeout) as response,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(self._download_sync, url, destination, headers)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ArtifactAcquisitionError(f"无法下载 {url}: {exc}") from exc


_GIT_TIMEOUT = 120
_GIT_CLONE_TIMEOUT = 600


def _run_git(
    executable: str,
    arguments: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = _GIT_TIMEOUT,
) -> str:
    try:
        completed = subprocess.run(
            [executable, *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            # 挂起的凭据/ssh 提示会一直占用升级操作锁，所以禁止交互。
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateSourceError(f"Git 命令超时（{timeout:g} 秒）") from exc
    except FileNotFoundError as exc:
        raise UpdateSourceError(f"找不到 Git 可执行文件: {executable}") from exc
    except OSError as exc:
        raise UpdateSourceError(f"无法运行 Git: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        detail = _redact_text((exc.stderr or exc.stdout or "").strip())
        raise UpdateSourceError(f"Git 命令失败: {detail or exc.returncode}") from exc
    return completed.stdout.strip()


def _is_git_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(parts.scheme and ("://" in value or parts.scheme.lower() == "file"))


def _normalize_git_repository(
    repository: str | Path,
    stable_root: Path,
    *,
    git_executable: str = "git",
) -> str:
    """把 Git 配置值解析成固定的本地路径或远端 URL。"""

    configured = str(repository)
    repository_path = Path(configured).expanduser()
    if not repository_path.is_absolute():
        repository_path = stable_root / repository_path
    if repository_path.exists():
        return str(repository_path.resolve())
    if _is_git_url(configured) or _GIT_SCP_LIKE_PATTERN.fullmatch(configured):
        return configured
    if not _GIT_REMOTE_NAME_PATTERN.fullmatch(configured):
        return configured

    display_value = _redact_text(configured)
    try:
        resolved = _run_git(
            git_executable,
            ["remote", "get-url", configured],
            cwd=stable_root,
        )
    except UpdaterError as exc:
        reason = _redact_text(str(exc) or exc.__class__.__name__)
        raise UpgradeConfigError(
            f"无法解析 upgrade.repository={display_value!r}："
            f"在 Git 工作树 {stable_root} 中执行 "
            f"git remote get-url {display_value!r} 失败：{reason}。"
            "请检查该 remote，或将 upgrade.repository 改为完整 URL/本地路径。"
        ) from exc
    if not resolved:
        raise UpgradeConfigError(
            f"无法解析 upgrade.repository={display_value!r}："
            f"在 Git 工作树 {stable_root} 中执行 "
            f"git remote get-url {display_value!r} 未返回 URL。"
            "请检查该 remote，或将 upgrade.repository 改为完整 URL/本地路径。"
        )
    return resolved


class GitTagSource:
    """从 Git 远端 tag 发现版本，并以 commit SHA 校验浅克隆制品。"""

    name = "git_tag"

    def __init__(
        self,
        repository: str | Path,
        *,
        tag_prefix: str = "v",
        git_executable: str = "git",
    ) -> None:
        if not tag_prefix:
            raise ValueError("tag_prefix 不能为空")
        self.repository = str(repository)
        self.tag_prefix = tag_prefix
        self.git_executable = git_executable

    def _version_from_tag(self, tag: str) -> Version | None:
        if not tag.startswith(self.tag_prefix):
            return None
        try:
            return parse_version(tag[len(self.tag_prefix) :])
        except InvalidVersionError:
            # Git 仓库中可以存在非 SemVer 的历史 tag；它们不应阻断有效版本。
            return None

    async def discover(self, channel: UpdateChannel) -> Sequence[Release]:
        selected_channel = UpdateChannel.parse(channel)
        output = await asyncio.to_thread(
            _run_git,
            self.git_executable,
            ["ls-remote", "--tags", self.repository],
        )
        tag_shas: dict[str, str] = {}
        peeled_shas: dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            sha, ref = parts
            if not _HEX_SHA_PATTERN.fullmatch(sha):
                continue
            if ref.endswith("^{}"):
                peeled_shas[ref[:-3]] = sha
            elif ref.startswith("refs/tags/"):
                tag_shas[ref.removeprefix("refs/tags/")] = sha

        releases: list[Release] = []
        for tag, tag_sha in tag_shas.items():
            version = self._version_from_tag(tag)
            if version is None:
                continue
            if selected_channel is UpdateChannel.STABLE and version.is_prerelease:
                continue
            releases.append(
                Release(
                    version=version,
                    tag=tag,
                    source=self.name,
                    # Annotated tag 会报告 object SHA 和 peeled commit SHA；后者
                    # 是 clone + rev-parse 能够验证的值。
                    commit_sha=peeled_shas.get(f"refs/tags/{tag}", tag_sha),
                    prerelease=version.is_prerelease,
                )
            )
        return tuple(
            sorted(releases, key=lambda release: (release.version, release.tag))
        )

    async def acquire(self, release: Release, destination: Path) -> Path:
        if release.commit_sha is None:
            raise ArtifactVerificationError("Git 发布缺少 commit SHA，禁止安装")
        destination = destination.resolve()
        if destination.exists():
            raise ArtifactAcquisitionError(f"制品目录已存在: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(
                _run_git,
                self.git_executable,
                [
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    release.tag,
                    "--single-branch",
                    self.repository,
                    str(destination),
                ],
                timeout=_GIT_CLONE_TIMEOUT,
            )
            actual_sha = await asyncio.to_thread(
                _run_git,
                self.git_executable,
                ["rev-parse", "HEAD"],
                cwd=destination,
            )
            if actual_sha.lower() != release.commit_sha.lower():
                raise ArtifactVerificationError(
                    "Git 制品 commit SHA 校验失败: "
                    f"expected={release.commit_sha}, actual={actual_sha}"
                )
            return destination
        except UpdaterError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        except OSError as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise ArtifactAcquisitionError(f"无法准备 Git 制品: {exc}") from exc


class _ArchiveSource:
    def __init__(self, client: HttpClient | None = None) -> None:
        self.client = client or UrllibHttpClient()

    async def _acquire_archive(
        self,
        release: Release,
        destination: Path,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Path:
        if not release.artifact_url:
            raise ArtifactAcquisitionError(f"{release.tag} 没有可下载的 artifact_url")
        if not release.artifact_sha256:
            raise ArtifactVerificationError(f"{release.tag} 没有 SHA-256，禁止继续安装")
        destination = destination.resolve()
        if destination.exists():
            raise ArtifactAcquisitionError(f"制品目录已存在: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        filename = Path(
            release.artifact_name or urlsplit(release.artifact_url).path or "artifact"
        ).name
        if not filename or filename in {".", ".."}:
            filename = "artifact"
        archive_path = destination.parent / f".{filename}.{uuid.uuid4().hex}.download"
        try:
            await self.client.download(
                release.artifact_url,
                archive_path,
                headers=headers,
            )
            actual_digest = await asyncio.to_thread(_sha256_file, archive_path)
            if actual_digest.lower() != release.artifact_sha256.lower():
                raise ArtifactVerificationError(
                    "release asset SHA-256 校验失败: "
                    f"expected={release.artifact_sha256}, actual={actual_digest}"
                )
            await asyncio.to_thread(_extract_archive, archive_path, destination)
            return destination
        except UpdaterError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise ArtifactAcquisitionError(f"无法解包 release asset: {exc}") from exc
        finally:
            archive_path.unlink(missing_ok=True)


class GitHubReleaseSource(_ArchiveSource):
    """读取 GitHub Releases API，并以资产 digest 校验下载文件。

    GitHub 官方 Releases API 的资产对象提供 ``browser_download_url`` 和
    ``digest``（``sha256:<64 hex>``）字段；这里不信任 tag 或文件名作为强校验。
    """

    name = "github_release"

    def __init__(
        self,
        repository: str,
        *,
        client: HttpClient | None = None,
        token: str | None = None,
        asset_name: str | None = None,
        tag_prefix: str = "v",
        api_base_url: str = "https://api.github.com",
    ) -> None:
        super().__init__(client)
        parts = repository.strip().strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("GitHub repository 必须是 owner/repository")
        if not tag_prefix:
            raise ValueError("tag_prefix 不能为空")
        self.repository = "/".join(parts)
        self.token = token or None
        self.asset_name = asset_name or None
        self.tag_prefix = tag_prefix
        self.api_base_url = api_base_url.rstrip("/")

    @property
    def releases_url(self) -> str:
        owner, repository = self.repository.split("/")
        return (
            f"{self.api_base_url}/repos/{quote(owner)}/{quote(repository)}"
            "/releases?per_page=100"
        )

    @property
    def headers(self) -> Mapping[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _parse_version(self, tag: str) -> Version | None:
        if not tag.startswith(self.tag_prefix):
            return None
        try:
            return parse_version(tag[len(self.tag_prefix) :])
        except InvalidVersionError:
            return None

    def _asset(self, value: Mapping[str, Any]) -> Mapping[str, Any] | None:
        assets = value.get("assets", [])
        if not isinstance(assets, list):
            raise UpdateSourceError("GitHub release 的 assets 必须是数组")
        if self.asset_name:
            return next(
                (
                    asset
                    for asset in assets
                    if isinstance(asset, Mapping)
                    and asset.get("name") == self.asset_name
                ),
                None,
            )
        candidates = [
            asset
            for asset in assets
            if isinstance(asset, Mapping) and asset.get("browser_download_url")
        ]
        # 当发布包含校验和或多个构建变体时，优先选择已经可验证的制品；否则
        # 保留第一个可下载制品，以便安装过程明确报告缺少 digest。
        return next((asset for asset in candidates if self._digest(asset)), None) or (
            candidates[0] if candidates else None
        )

    @staticmethod
    def _digest(asset: Mapping[str, Any] | None) -> str | None:
        if asset is None:
            return None
        value = asset.get("digest", asset.get("sha256"))
        if not isinstance(value, str):
            return None
        return value.removeprefix("sha256:")

    async def discover(self, channel: UpdateChannel) -> Sequence[Release]:
        selected_channel = UpdateChannel.parse(channel)
        payload = await self.client.get_json(self.releases_url, headers=self.headers)
        if not isinstance(payload, list):
            raise UpdateSourceError("GitHub Releases API 返回值不是数组")
        releases: list[Release] = []
        for item in payload:
            if not isinstance(item, Mapping) or item.get("draft", False):
                continue
            tag = item.get("tag_name")
            if not isinstance(tag, str):
                continue
            version = self._parse_version(tag)
            if version is None:
                continue
            is_prerelease = bool(item.get("prerelease", False)) or version.is_prerelease
            if selected_channel is UpdateChannel.STABLE and is_prerelease:
                continue
            asset = self._asset(item)
            if asset is None:
                # 发布后附件由 CI 上传，上传完成前和早期没有附件的版本都不算可安装。
                continue
            commit = item.get("target_commitish")
            commit_sha = (
                commit
                if isinstance(commit, str) and _HEX_SHA_PATTERN.fullmatch(commit)
                else None
            )
            required = item.get(
                "required_config_version", item.get("min_config_version")
            )
            if required is not None and not isinstance(required, str):
                raise UpdateSourceError(f"GitHub release {tag} 的配置版本无效")
            releases.append(
                Release(
                    version=version,
                    tag=tag,
                    source=self.name,
                    commit_sha=commit_sha,
                    artifact_url=asset.get("browser_download_url"),
                    artifact_sha256=self._digest(asset),
                    artifact_name=asset.get("name"),
                    prerelease=is_prerelease,
                    required_config_version=(
                        parse_version(required) if required is not None else None
                    ),
                    notes=str(item.get("body", "") or ""),
                    metadata=item,
                )
            )
        return tuple(
            sorted(releases, key=lambda release: (release.version, release.tag))
        )

    async def acquire(self, release: Release, destination: Path) -> Path:
        return await self._acquire_archive(
            release,
            destination,
            headers=self.headers,
        )


class UrlManifestSource(_ArchiveSource):
    """从任意 URL 读取 JSON manifest 的扩展源。

    支持 ``[{...release...}]``、``{"releases": [...]}`` 和单个 release object；
    每个 release 必须提供 artifact URL 与 SHA-256，避免 manifest 只提供版本号
    却无法验证下载内容。
    """

    name = "manifest"

    def __init__(
        self,
        url: str,
        *,
        client: HttpClient | None = None,
        tag_prefix: str = "v",
    ) -> None:
        super().__init__(client)
        if not url:
            raise ValueError("manifest URL 不能为空")
        if not tag_prefix:
            raise ValueError("tag_prefix 不能为空")
        self.url = url
        self.tag_prefix = tag_prefix

    @staticmethod
    def _items(payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, list):
            values = payload
        elif isinstance(payload, Mapping) and isinstance(payload.get("releases"), list):
            values = payload["releases"]
        elif isinstance(payload, Mapping):
            values = [payload]
        else:
            raise UpdateSourceError("manifest 必须是 release 数组或 object")
        if not all(isinstance(item, Mapping) for item in values):
            raise UpdateSourceError("manifest releases 的每项必须是 object")
        return list(values)

    async def discover(self, channel: UpdateChannel) -> Sequence[Release]:
        selected_channel = UpdateChannel.parse(channel)
        payload = await self.client.get_json(self.url)
        releases: list[Release] = []
        for item in self._items(payload):
            version_value = item.get("version")
            parse_version(version_value)
            default_tag = (
                version_value
                if isinstance(version_value, str)
                and version_value.startswith(("v", "V"))
                else f"{self.tag_prefix}{version_value}"
            )
            release = Release.from_mapping(
                item,
                source=self.name,
                default_tag=default_tag,
            )
            if (
                selected_channel is UpdateChannel.STABLE
                and release.version.is_prerelease
            ):
                continue
            if release.artifact_url:
                release = replace(
                    release, artifact_url=urljoin(self.url, release.artifact_url)
                )
            releases.append(release)
        return tuple(
            sorted(releases, key=lambda release: (release.version, release.tag))
        )

    async def acquire(self, release: Release, destination: Path) -> Path:
        return await self._acquire_archive(release, destination)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_path(destination: Path, member_name: str) -> Path:
    pure = PurePosixPath(member_name)
    if pure.is_absolute() or ".." in pure.parts:
        raise ArtifactAcquisitionError(f"制品包含越界路径: {member_name!r}")
    relative = Path(*[part for part in pure.parts if part not in {"", "."}])
    target = (destination / relative).resolve()
    if not target.is_relative_to(destination.resolve()):
        raise ArtifactAcquisitionError(f"制品包含越界路径: {member_name!r}")
    return target


def _extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = _safe_archive_path(destination, member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ArtifactAcquisitionError("制品不允许包含符号链接")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as input_file, target.open("wb") as output:
                shutil.copyfileobj(input_file, output)


def _extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            target = _safe_archive_path(destination, member.name)
            if member.issym() or member.islnk() or member.isdev():
                raise ArtifactAcquisitionError("制品不允许包含链接或设备文件")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ArtifactAcquisitionError(
                    f"制品包含不支持的 tar 成员: {member.name}"
                )
            input_file = source.extractfile(member)
            if input_file is None:
                raise ArtifactAcquisitionError(f"无法读取 tar 成员: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with input_file, target.open("wb") as output:
                shutil.copyfileobj(input_file, output)


def _flatten_single_archive_root(destination: Path) -> None:
    children = list(destination.iterdir())
    if (
        len(children) != 1
        or not children[0].is_dir()
        or (destination / "tenko").exists()
    ):
        return
    source_root = children[0]
    for child in list(source_root.iterdir()):
        target = destination / child.name
        if target.exists():
            raise ArtifactAcquisitionError(f"制品根目录冲突: {target.name}")
        os.replace(child, target)
    source_root.rmdir()


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        if zipfile.is_zipfile(archive):
            _extract_zip(archive, destination)
        elif tarfile.is_tarfile(archive):
            _extract_tar(archive, destination)
        else:
            raise ArtifactAcquisitionError("只支持 zip 或 tar 制品")
        _flatten_single_archive_root(destination)
        if not any(destination.iterdir()):
            raise ArtifactAcquisitionError("制品为空")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
