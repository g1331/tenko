"""插件目录客户端：解析、版本选择、地址回退与缓存、图标校验。"""

from __future__ import annotations

import hashlib
import json

import pytest

from tenko.host.plugins.manager import PluginManagerError
from tenko.host.plugins.registry import (
    Registry,
    RegistryError,
    compatible,
    parse_index,
    source_for,
)
from tenko_api import __version__ as API_VERSION

PNG = b"\x89PNG\r\n\x1a\n" + b"icon"
SHA8 = hashlib.sha256(PNG).hexdigest()[:8]
H1, H2 = "a" * 64, "b" * 64


def version(v: str, **extra) -> dict:
    return {
        "version": v,
        "pin": {"sha256": [H1]},
        "requires_api": f">={API_VERSION},<99",
        "requires_python": ">=3.11",
        "valid": True,
        "yanked": None,
        **extra,
    }


def index_data(**plugins) -> dict:
    return {
        "schema_version": 1,
        "plugins": {
            "example": {
                "name": "示例",
                "tier": "listed",
                "icon": f"icons/example-{SHA8}.png",
                "source": {"type": "pypi", "package": "tenko-plugin-example"},
                "future_field": "忽略",
                "versions": [
                    version("0.1.0"),
                    version("0.3.0", requires_api=">=99"),  # 宿主装不了
                    version("0.2.0", yanked={"reason": "数据损坏"}),
                ],
            },
            "broken": {"name": "x", "tier": "listed", "source": {"type": "ftp"}},
            **plugins,
        },
        "blocked": {"evil": {"reason": "恶意代码", "dists": ["tenko-plugin-evil"]}},
    }


def test_parse_selects_newest_installable_version_and_pins_it() -> None:
    index = parse_index(index_data())

    assert set(index.plugins) == {"example"}  # 无效条目跳过，不影响其余
    entry = index.find("tenko_plugin_example")  # 也能按分发名找
    assert entry is index.plugins["example"]
    assert compatible(entry).version == "0.1.0"  # 跳过不兼容的 0.3.0 和撤回的 0.2.0
    assert compatible(entry, "0.2.0") is None
    source = source_for(entry, compatible(entry))
    assert (source.requirement, source.trust, source.expected_id, source.pins) == (
        "tenko-plugin-example==0.1.0",
        "listed",
        "example",
        (H1,),
    )
    assert index.blocked_reason("other", "tenko-plugin-evil") == "恶意代码"
    with pytest.raises(RegistryError, match="schema_version=2"):
        parse_index({**index_data(), "schema_version": 2})


@pytest.mark.asyncio
async def test_load_falls_back_caches_and_verifies_icons(tmp_path) -> None:
    responses = {
        "https://mirror/index.json": json.dumps(index_data()).encode(),
        f"https://mirror/icons/example-{SHA8}.png": PNG,
    }
    calls = []

    async def fetch(url: str, _limit: int) -> bytes:
        calls.append(url)
        if url not in responses:
            raise OSError("unreachable")
        return responses[url]

    urls = ["https://primary/index.json", "https://mirror/index.json"]
    registry = Registry(urls, tmp_path, fetch=fetch)
    index = await registry.load()

    assert index.url == "https://mirror/index.json"
    assert registry.icon_path(index.plugins["example"]).read_bytes() == PNG

    # 离线的新进程：磁盘缓存可用，并且在有效期内不联网。
    calls.clear()
    offline = Registry(urls, tmp_path, fetch=fetch)
    assert "example" in (await offline.load()).plugins and calls == []
    responses.clear()
    assert "example" in (await offline.load(refresh=True)).plugins

    # 图标内容与文件名里的哈希不符时丢弃。
    (tmp_path / "icons" / f"example-{SHA8}.png").unlink()
    responses.update(
        {
            "https://mirror/index.json": json.dumps(index_data()).encode(),
            f"https://mirror/icons/example-{SHA8}.png": PNG + b"tampered",
        }
    )
    index = await offline.load(refresh=True)
    assert offline.icon_path(index.plugins["example"]) is None


@pytest.mark.asyncio
async def test_resolve_refuses_blocked_and_uninstallable(tmp_path) -> None:
    data = index_data(
        evil={
            "name": "坏",
            "tier": "listed",
            "source": {"type": "pypi", "package": "tenko-plugin-evil"},
            "versions": [version("1.0.0")],
        }
    )

    async def fetch(_url: str, _limit: int) -> bytes:
        return json.dumps(data).encode()

    registry = Registry(["https://r/index.json"], tmp_path, fetch=fetch)
    assert (await registry.resolve("example"))[1].version == "0.1.0"
    assert await registry.resolve("not-in-registry") is None
    with pytest.raises(PluginManagerError, match="封禁"):
        await registry.resolve("evil")
    with pytest.raises(PluginManagerError, match="数据损坏"):
        await registry.resolve("example@0.2.0")


def test_tested_host_is_shown_for_passed_and_failed_versions() -> None:
    from tenko.host.plugins.registry import tested_text, why_incompatible

    tested = {"tenko": "4.0.0-rc36", "at": "2026-10-23T03:17:00Z"}
    entry = parse_index(
        index_data(
            example={
                "name": "示例",
                "tier": "listed",
                "source": {"type": "pypi", "package": "tenko-plugin-example"},
                "versions": [
                    version("0.2.0", valid=False, tested=tested),
                    version("0.1.0", tested=tested),
                    version("0.0.1", tested="坏数据"),  # 只影响展示，不连累条目
                ],
            }
        )
    ).plugins["example"]
    failed, passed, odd = entry.versions

    assert why_incompatible(failed) == "在 Tenko 4.0.0-rc36 上实测未通过"
    assert tested_text(passed) == "可安装（Tenko 4.0.0-rc36 实测通过）"
    assert (odd.tested_on, tested_text(odd)) == (None, "可安装")
