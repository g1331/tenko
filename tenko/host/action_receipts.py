"""OneBot 11 回执与响应的无状态解析。

这些函数只做字符串/mapping 到标准字段的转换，不依赖 ``ActionService`` 状态。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping


def _retcode(value: object) -> int | str | None:
    if value is None or isinstance(value, int | str):
        return value
    return str(value)


def _is_failed_receipt(value: Mapping[str, object]) -> bool:
    status = str(value.get("status", "")).lower()
    if status in {"failed", "error"}:
        return True
    retcode = value.get("retcode")
    return retcode is not None and str(retcode).lower() not in {"0", "ok"}


def _mapping_from_text(detail: str) -> Mapping[str, object] | None:
    """从 ``ActionFailed`` 文本中的 Python 字典字面量取出回执。"""

    start = detail.find("{")
    end = detail.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = ast.literal_eval(detail[start : end + 1])
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _mapping_from(
    value: object, *, failed_only: bool = False
) -> Mapping[str, object] | None:
    """从 Satori ActionFailed 或测试回执中取出 OneBot 风格 mapping。"""

    candidates: list[object] = []
    if isinstance(value, Mapping):
        candidates.append(value)
    elif isinstance(value, BaseException):
        candidates.extend(value.args)

    for candidate in candidates:
        if isinstance(candidate, Mapping):
            if not failed_only or _is_failed_receipt(candidate):
                return candidate
        if isinstance(candidate, str):
            parsed = _mapping_from_text(candidate)
            if parsed is not None and (not failed_only or _is_failed_receipt(parsed)):
                return parsed
        if isinstance(candidate, BaseException):
            nested = _mapping_from(candidate, failed_only=failed_only)
            if nested is not None:
                return nested
    return None


def _fields_from_text(detail: str) -> tuple[str | None, int | str | None]:
    status_match = re.search(
        r"['\"]?status['\"]?\s*[=:]\s*['\"]?([\w-]+)",
        detail,
        re.IGNORECASE,
    )
    retcode_match = re.search(
        r"['\"]?retcode['\"]?\s*[=:]\s*['\"]?([\w-]+)",
        detail,
        re.IGNORECASE,
    )
    status = status_match.group(1).lower() if status_match else None
    raw_retcode = retcode_match.group(1) if retcode_match else None
    if raw_retcode is None:
        prefix_match = re.match(r"\s*(\d+)\s*:", detail)
        raw_retcode = prefix_match.group(1) if prefix_match else None
    if raw_retcode is not None and raw_retcode.isdigit():
        return status, int(raw_retcode)
    return status, raw_retcode
