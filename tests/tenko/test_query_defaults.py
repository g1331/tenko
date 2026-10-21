from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from arclet.entari.command.model import Query
from arclet.entari.command.provider import AlconnaProvider

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "tenko" / "plugins"


async def _provide(query: Query[str], **parsed: str) -> Query[str]:
    # 只模拟 Arparma.query；用真 Alconna 会污染全局命令表。
    result = SimpleNamespace(query=lambda path, default: parsed.get(path, default))
    provider = AlconnaProvider(
        "query", {"query": query, "anno": Query[str], "name": "mode"}
    )
    return await provider({"alc_result": SimpleNamespace(result=result)})


@pytest.mark.asyncio
async def test_query_default_makes_missing_argument_look_available() -> None:
    # Entari 只要 Query 带了非 Empty 默认值就把 available 设为 True，
    # 所以插件用 available 判断“参数是否给出”时不能写默认值。
    assert (await _provide(Query("mode", None))).available
    assert not (await _provide(Query("mode"))).available
    assert (await _provide(Query("mode"), mode="所有人")).result == "所有人"


def test_plugins_do_not_give_optional_queries_a_none_default() -> None:
    offenders = [
        f"{path.relative_to(PLUGIN_DIR)}:{number}"
        for path in PLUGIN_DIR.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r'Query\("[^"]*", None\)', line)
    ]
    assert offenders == []
