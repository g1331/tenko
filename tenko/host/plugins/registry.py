"""插件目录（tenko-registry）客户端：拉取 ``index.json``、缓存、选择兼容版本。

目录地址按配置顺序回退；结果在内存和 ``.tenko/plugins/registry/`` 各缓存一份，
一小时内不重复联网，联网失败时用磁盘缓存，离线也能搜索和安装已收录插件。

只认识 schema v1 里的字段，未知字段一律忽略，不参与身份、来源和安全判断；格式不对的
单个条目跳过并告警，不影响其余条目。图标随目录一起下载到本地，WebUI 只从 Tenko 自己的
接口读取，不直连外部地址。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .manager import (
    PluginManager,
    PluginManagerError,
    PluginSource,
    parse_source,
)

DEFAULT_URLS = (
    "https://g1331.github.io/tenko-registry/index.json",
    "https://cdn.jsdelivr.net/gh/g1331/tenko-registry@gh-pages/index.json",
)
SCHEMA_VERSION = 1
TTL_SECONDS = 3600
_MAX_INDEX_BYTES = 8 * 1024 * 1024
_MAX_ICON_BYTES = 256 * 1024
_TIMEOUT = 15
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_ICON = re.compile(
    r"^icons/(?P<name>(?P<id>[a-z][a-z0-9_]*)-(?P<sha>[0-9a-f]{8})\.png)$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")

Fetch = Callable[[str, int], Awaitable[bytes]]


class RegistryError(RuntimeError):
    """目录不可用或条目不满足要求。"""


@dataclass(frozen=True, slots=True)
class RegistryVersion:
    version: str
    sha256: tuple[str, ...]
    commit: str | None
    requires_api: str | None
    requires_python: str | None
    valid: bool
    yanked: str | None
    # 目录最近一次实测所用的 Tenko 版本和时间（每天在最新的 Tenko 上重测）。
    tested_on: str | None = None
    tested_at: str | None = None


@dataclass(frozen=True, slots=True)
class RegistryPlugin:
    id: str
    name: str
    description: str
    authors: tuple[str, ...]
    homepage: str | None
    license: str | None
    tags: tuple[str, ...]
    tier: str
    icon: str | None  # 目录里的相对路径 icons/<id>-<sha8>.png，本地文件见 icon_path
    source_type: str
    package: str | None
    git_url: str | None
    subdirectory: str | None
    versions: tuple[RegistryVersion, ...]
    # 分类的显示名；目录较旧、没有分类时为 None。
    category: str | None = None

    @property
    def dist(self) -> str | None:
        return canonicalize_name(self.package) if self.package else None


@dataclass(frozen=True, slots=True)
class RegistryIndex:
    plugins: Mapping[str, RegistryPlugin]
    blocked: Mapping[str, str]  # 插件 ID 或规范化分发名 -> 原因
    url: str | None
    categories: tuple[str, ...] = ()  # 分类显示名，按目录给出的展示顺序

    def find(self, name: str) -> RegistryPlugin | None:
        """按插件 ID 或 PyPI 分发名查找。"""

        if found := self.plugins.get(name.strip().lower()):
            return found
        dist = canonicalize_name(name)
        return next((p for p in self.plugins.values() if p.dist == dist), None)

    def blocked_reason(self, plugin_id: str | None, dist: str | None) -> str | None:
        for key in (plugin_id, dist and canonicalize_name(dist)):
            if key and key in self.blocked:
                return self.blocked[key]
        return None

    def search(self, keyword: str = "") -> list[RegistryPlugin]:
        """按关键词过滤，官方在前，其次是能装在当前宿主上的。"""

        word = keyword.strip().lower()
        hits = [
            p
            for p in self.plugins.values()
            if not word
            or word in p.id
            or word in p.name.lower()
            or word in p.description.lower()
            or any(word in t.lower() for t in p.tags)
            or word == (p.category or "").lower()
        ]
        return sorted(
            hits, key=lambda p: (p.tier != "official", compatible(p) is None, p.id)
        )


def _host_api() -> Version:
    from tenko_api import __version__

    return Version(__version__)


def _fits(spec: str | None, version: Version) -> bool:
    if spec is None:
        return False
    try:
        return SpecifierSet(spec).contains(version, prereleases=True)
    except InvalidSpecifier:
        return False


def why_incompatible(
    version: RegistryVersion, api: Version | None = None
) -> str | None:
    """这个版本不能装在当前宿主（或 tenko-api 为 ``api`` 的宿主）上的原因；可以装时返回 ``None``。"""

    api = api or _host_api()
    if version.yanked is not None:
        return f"已撤回：{version.yanked}"
    if not version.valid:
        if version.tested_on:
            return f"在 Tenko {version.tested_on} 上实测未通过"
        return "还没通过目录的实测"
    if not _fits(version.requires_api, api):
        return f"要求 tenko-api {version.requires_api}，宿主是 {api}"
    if not _fits(version.requires_python or ">=0", Version(platform.python_version())):
        return f"要求 Python {version.requires_python}"
    return None


def tested_text(version: RegistryVersion) -> str:
    """可安装版本的说明：带上目录实测所用的 Tenko 版本。"""

    return (
        f"可安装（Tenko {version.tested_on} 实测通过）"
        if version.tested_on
        else "可安装"
    )


def compatible(
    plugin: RegistryPlugin, version: str | None = None
) -> RegistryVersion | None:
    """指定版本或最新的可安装版本（有效、未撤回、tenko-api 和 Python 都兼容）。"""

    for item in plugin.versions:
        if (version is None or item.version == version) and why_incompatible(
            item
        ) is None:
            return item
    return None


def installable(plugin: RegistryPlugin, api: Version | None = None) -> list[str]:
    """能装在当前宿主（或 tenko-api 为 ``api`` 的宿主）上的全部版本，按目录顺序。"""

    return [v.version for v in plugin.versions if why_incompatible(v, api) is None]


def source_for(plugin: RegistryPlugin, version: RegistryVersion) -> PluginSource:
    """把目录条目变成安装来源：固定到版本加文件哈希，或固定到提交号。"""

    if plugin.source_type == "pypi":
        source = parse_source(f"{plugin.package}=={version.version}")
    else:
        fragment = f"#subdirectory={plugin.subdirectory}" if plugin.subdirectory else ""
        source = parse_source(f"{plugin.git_url}@{version.commit}{fragment}")
    return replace(
        source, trust=plugin.tier, expected_id=plugin.id, pins=version.sha256
    )


def installed_notes(
    index: RegistryIndex, plugin_id: str, dist: str, version: str, trust: str
) -> dict[str, str | None]:
    """已安装插件在目录里的状态：封禁原因、当前版本的撤回原因、可更新到的版本。"""

    notes: dict[str, str | None] = {"blocked": None, "yanked": None, "update": None}
    notes["blocked"] = index.blocked_reason(plugin_id, dist)
    entry = index.plugins.get(plugin_id)
    if entry is None or trust == "unlisted":
        return notes
    installed = next((v for v in entry.versions if v.version == version), None)
    if installed is not None:
        notes["yanked"] = installed.yanked
    latest = compatible(entry)
    if latest and Version(latest.version) > Version(version):
        notes["update"] = latest.version
    return notes


async def registry_updates(
    manager: PluginManager, plugin_id: str | None = None
) -> dict[str, PluginSource]:
    """已收录的插件在目录里有更新的可用版本时，给出新来源（固定版本和哈希）。"""

    rows = [
        r
        for r in manager.status()
        if r.trust != "unlisted" and plugin_id in (None, r.plugin_id)
    ]
    if not rows:
        return {}
    try:
        index = await get_registry().load()
    except RegistryError as error:
        logger.warning("{}", error)
        return {}
    sources = {}
    for row in rows:
        entry = index.plugins.get(row.plugin_id)
        latest = entry and compatible(entry)
        if latest and Version(latest.version) > Version(row.version):
            sources[row.plugin_id] = source_for(entry, latest)
    return sources


async def resume_source(
    plugin_id: str, held: Mapping[str, Any], version: str | None = None
) -> PluginSource:
    """恢复停用插件时装的来源：目录里能装在当前宿主上的指定或最新版本；未收录的沿用原来源。"""

    record = held["record"]
    if record["trust"] != "unlisted" and (
        found := await get_registry().resolve(
            f"{plugin_id}@{version}" if version else plugin_id
        )
    ):
        return source_for(*found)
    if version:
        raise PluginManagerError(
            "预检", f"{plugin_id} 不在插件目录里，只能按原来的来源恢复"
        )
    return PluginSource(record["requirement"], record["source"], record["trust"])


# ------------------------------------------------------------------ 解析


def _text(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"应为字符串: {value!r}")
    return value


def _https(value: Any) -> str | None:
    text = _text(value, optional=True)
    if text is not None and urlsplit(text).scheme != "https":
        raise ValueError(f"只接受 https 地址: {text!r}")
    return text


def _version(raw: Mapping[str, Any], source_type: str) -> RegistryVersion:
    version = _text(raw["version"])
    Version(version)
    pin = raw["pin"]
    if source_type == "pypi":
        hashes = tuple(pin["sha256"])
        if not hashes or not all(
            isinstance(h, str) and _SHA256.match(h) for h in hashes
        ):
            raise ValueError("pin.sha256 应为非空的 sha256 列表")
        commit = None
    else:
        hashes, commit = (), _text(pin["commit"])
        if not _COMMIT.match(commit):
            raise ValueError("pin.commit 应为 40 位提交号")
    yanked = raw.get("yanked")
    tested = raw.get("tested") if isinstance(raw.get("tested"), Mapping) else {}
    return RegistryVersion(
        version,
        hashes,
        commit,
        _text(raw.get("requires_api"), optional=True),
        _text(raw.get("requires_python"), optional=True),
        raw.get("valid") is True,
        None if yanked is None else str(yanked.get("reason") or "未说明原因"),
        # 只用于展示：格式不对就当没测过，不连累整个条目。
        *(
            v if isinstance(v, str) else None
            for v in (tested.get("tenko"), tested.get("at"))
        ),
    )


def _plugin(
    plugin_id: str, raw: Mapping[str, Any], categories: Mapping[str, str]
) -> RegistryPlugin:
    if not _ID.match(plugin_id):
        raise ValueError("插件 ID 不合法")
    tier = raw["tier"]
    if tier not in ("official", "listed"):
        raise ValueError(f"未知的 tier: {tier!r}")
    source = raw["source"]
    source_type = source["type"]
    if source_type == "pypi":
        package, git_url, subdirectory = _text(source["package"]), None, None
    elif source_type == "git":
        package, git_url = None, _https(source["url"])
        subdirectory = _text(source.get("subdirectory"), optional=True)
    else:
        raise ValueError(f"未知的来源类型: {source_type!r}")
    icon = _text(raw.get("icon"), optional=True)
    if icon is not None:
        matched = _ICON.match(icon)
        if matched is None or matched["id"] != plugin_id:
            raise ValueError(f"图标路径不合法: {icon!r}")
    versions = tuple(_version(v, source_type) for v in raw.get("versions") or ())
    return RegistryPlugin(
        plugin_id,
        _text(raw["name"]),
        _text(raw.get("description") or ""),
        tuple(str(a.get("name", "")) for a in raw.get("authors") or ()),
        _https(raw.get("homepage")),
        _text(raw.get("license"), optional=True),
        tuple(str(t) for t in raw.get("tags") or ()),
        tier,
        icon,
        source_type,
        package,
        git_url,
        subdirectory,
        tuple(sorted(versions, key=lambda v: Version(v.version), reverse=True)),
        categories.get(raw.get("category"))
        if isinstance(raw.get("category"), str)
        else None,
    )


def parse_index(data: Any, url: str | None = None) -> RegistryIndex:
    version = data.get("schema_version") if isinstance(data, dict) else None
    if version != SCHEMA_VERSION:
        # 更高的版本由新宿主处理；load() 会退回磁盘上的旧缓存。
        raise RegistryError(f"不支持的目录格式 schema_version={version!r}")
    raw_categories = data.get("categories")
    categories = {
        str(key): str(label)
        for key, label in (
            raw_categories if isinstance(raw_categories, dict) else {}
        ).items()
    }
    plugins: dict[str, RegistryPlugin] = {}
    for plugin_id, raw in (data.get("plugins") or {}).items():
        try:
            plugins[plugin_id] = _plugin(plugin_id, raw, categories)
        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            InvalidVersion,
        ) as error:
            logger.warning("插件目录条目 {} 无效，已跳过: {}", plugin_id, error)
    blocked: dict[str, str] = {}
    for plugin_id, raw in (data.get("blocked") or {}).items():
        reason = str((raw or {}).get("reason") or "已被目录封禁")
        blocked[plugin_id] = reason
        for dist in (raw or {}).get("dists") or ():
            blocked[canonicalize_name(str(dist))] = reason
    return RegistryIndex(plugins, blocked, url, tuple(categories.values()))


# ------------------------------------------------------------------ 拉取


async def _http_fetch(url: str, limit: int) -> bytes:
    if urlsplit(url).scheme != "https":
        raise RegistryError(f"只接受 https 地址: {url}")
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if urlsplit(str(response.url)).scheme != "https":
                raise RegistryError(f"重定向到了非 https 地址: {response.url}")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise RegistryError(f"{url} 超过 {limit} 字节")
                chunks.append(chunk)
    return b"".join(chunks)


class Registry:
    """进程内共享的目录客户端。"""

    def __init__(
        self,
        urls: Sequence[str] = DEFAULT_URLS,
        cache_dir: str | Path = Path(".tenko/plugins/registry"),
        *,
        fetch: Fetch | None = None,
        ttl: float = TTL_SECONDS,
    ) -> None:
        self.urls = tuple(urls)
        self.cache_dir = Path(cache_dir)
        self.fetch = fetch or _http_fetch
        self.ttl = ttl
        self._index: RegistryIndex | None = None
        self._loaded_at = 0.0

    @property
    def cache_file(self) -> Path:
        return self.cache_dir / "index.json"

    @property
    def icon_dir(self) -> Path:
        return self.cache_dir / "icons"

    def icon_path(self, plugin: RegistryPlugin) -> Path | None:
        """已下载的图标文件；没有图标或尚未下载成功时返回 ``None``。"""

        if plugin.icon is None:
            return None
        path = self.icon_dir / _ICON.match(plugin.icon)["name"]  # type: ignore[index]
        return path if path.is_file() else None

    def cached(self) -> RegistryIndex | None:
        """只读磁盘缓存，不联网。"""

        if self._index is not None:
            return self._index
        try:
            saved = json.loads(self.cache_file.read_text(encoding="utf-8"))
            self._index = parse_index(saved["index"], saved.get("url"))
            self._loaded_at = self.cache_file.stat().st_mtime
        except (OSError, ValueError, KeyError, RegistryError):
            return None
        return self._index

    async def load(self, *, refresh: bool = False) -> RegistryIndex:
        """返回目录；缓存过期或 ``refresh`` 时按顺序尝试各地址，都失败时用磁盘缓存。"""

        cached = self.cached()
        if (
            cached is not None
            and not refresh
            and time.time() - self._loaded_at < self.ttl
        ):
            return cached
        errors = []
        for url in self.urls:
            try:
                raw = json.loads(await self.fetch(url, _MAX_INDEX_BYTES))
                index = parse_index(raw, url)
            except Exception as error:  # noqa: BLE001 - 换下一个地址
                errors.append(f"{url}: {error}")
                continue
            self._save(raw, url)
            self._index, self._loaded_at = index, time.time()
            await self._download_icons(index)
            return index
        if cached is not None:
            logger.warning("插件目录拉取失败，使用缓存: {}", "；".join(errors))
            return cached
        raise RegistryError("插件目录不可用：" + "；".join(errors))

    def _save(self, raw: Any, url: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temp = self.cache_file.with_name("index.json.tmp")
        temp.write_text(
            json.dumps({"url": url, "index": raw}, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temp, self.cache_file)

    async def _download_icons(self, index: RegistryIndex) -> None:
        """下载缺少的图标；文件名带内容哈希前缀，下载后核对，不一致的丢弃。"""

        wanted = {p.icon for p in index.plugins.values() if p.icon}
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        for relative in wanted:
            matched = _ICON.match(relative)
            path = self.icon_dir / matched["name"]
            if path.is_file():
                continue
            try:
                data = await self.fetch(
                    urljoin(index.url or "", relative), _MAX_ICON_BYTES
                )
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RegistryError("不是 PNG")
                if not hashlib.sha256(data).hexdigest().startswith(matched["sha"]):
                    raise RegistryError("内容与文件名里的哈希不符")
            except Exception as error:  # noqa: BLE001 - 图标缺失不影响目录使用
                logger.warning("插件图标 {} 下载失败: {}", relative, error)
                continue
            path.write_bytes(data)
        for stale in self.icon_dir.iterdir():
            if f"icons/{stale.name}" not in wanted:
                stale.unlink(missing_ok=True)

    async def resolve(self, spec: str) -> tuple[RegistryPlugin, RegistryVersion] | None:
        """把 ``<ID或包名>[@版本]`` 解析成目录条目和要装的版本；不在目录里返回 ``None``。"""

        name, _, version = spec.strip().partition("@")
        try:
            index = await self.load()
        except RegistryError as error:
            logger.warning("{}", error)
            return None
        plugin = index.find(name)
        if plugin is None:
            return None
        if reason := index.blocked_reason(plugin.id, plugin.dist):
            raise PluginManagerError("预检", f"插件 {plugin.id} 已被目录封禁：{reason}")
        chosen = compatible(plugin, version or None)
        if chosen is None:
            listed = next((v for v in plugin.versions if v.version == version), None)
            detail = (
                why_incompatible(listed)
                if listed
                else (
                    f"目录里没有版本 {version}"
                    if version
                    else "没有能装在当前宿主上的版本"
                )
            )
            raise PluginManagerError("预检", f"插件 {plugin.id}：{detail}")
        return plugin, chosen


_registry: Registry | None = None


def configure_registry(urls: Sequence[str]) -> Registry:
    global _registry
    _registry = Registry(urls or DEFAULT_URLS)
    return _registry


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry
