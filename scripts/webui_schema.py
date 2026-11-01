"""把 ``tenko.webui.contracts`` 导出为 JSON Schema，供前端生成 TypeScript 类型。

用法：``uv run python -m scripts.webui_schema``，结果写到标准输出。前端的
``npm run gen:types`` 调用它，再用 json-schema-to-typescript 生成
``webui/src/api/generated.ts``。测试用 ``adapter`` 按同一份契约严格校验真实响应。

pydantic 只是开发依赖：运行中的宿主不导入本模块。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from functools import cache
from typing import Any

from pydantic import ConfigDict, TypeAdapter
from typing_extensions import TypeAliasType, is_typeddict

from tenko.webui import contracts

# 字段文档字符串写进 schema 的 description；契约之外的字段一律视为错误。
_CONFIG = ConfigDict(use_attribute_docstrings=True, extra="forbid")


def contract_types() -> dict[str, Any]:
    """契约模块里定义的全部 TypedDict 和命名类型别名，按名称排序。"""

    found: dict[str, Any] = {}
    for name, value in vars(contracts).items():
        if getattr(value, "__module__", None) != contracts.__name__:
            continue
        if is_typeddict(value):
            value.__pydantic_config__ = _CONFIG
            found[name] = value
        elif isinstance(value, TypeAliasType):
            found[name] = value
    return dict(sorted(found.items()))


@cache
def adapter(contract: Any) -> TypeAdapter[Any]:
    contract_types()  # 先给所有 TypedDict 装上配置，嵌套的类型才会同样严格
    return TypeAdapter(contract)


def _subschemas(node: Any) -> Iterator[dict[str, Any]]:
    """遍历 schema 节点；properties、$defs 这类映射本身不是节点，只遍历它们的值。"""

    if isinstance(node, list):
        for value in node:
            yield from _subschemas(value)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key, value in node.items():
        if key in ("properties", "$defs"):
            for child in value.values():
                yield from _subschemas(child)
        elif key not in ("enum", "const", "default", "examples", "required"):
            yield from _subschemas(value)


def _normalize(schema: dict[str, Any]) -> None:
    """把 pydantic 的输出整理成 json-schema-to-typescript 能直接用的形式。"""

    for node in _subschemas(schema):
        # 字段上的 title 会让生成器给每个字段造一个类型别名（Version、Rss……）。
        if node.get("type") != "object" or "properties" not in node:
            node.pop("title", None)
        # 生成器只认 draft-07 的元组写法。
        if "prefixItems" in node:
            node["items"] = node.pop("prefixItems")
        # 空 TypedDict 在 TypeScript 里写成 {} 会接受任何非空值。
        if node.get("type") == "object" and node.get("properties") == {}:
            node["tsType"] = "Record<string, never>"


def build_schema() -> dict[str, Any]:
    types = contract_types()
    _, schema = TypeAdapter.json_schemas(
        [(name, "validation", adapter(value)) for name, value in types.items()],
        ref_template="#/$defs/{model}",
    )
    _normalize(schema)
    return {"title": "WebuiContracts", "type": "object", **schema}


def main() -> None:
    json.dump(build_schema(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
