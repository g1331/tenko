"""平台动作失败的无状态分类。

只读取 ``ActionFailure`` 的字段，不依赖 ``ActionService`` 的能力学习状态。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .actions import ActionFailure

_PERMISSION_ERROR_CODES = frozenset(
    {
        "ERR_NOT_GROUP_ADMIN",
        "ERR_NOT_GROUP_OWNER",
        "ERR_NOT_FRIEND",
    }
)
# NapCat 当前的 ``SetGroupBan`` 源码明确记录了
# bot 没有群管理员权限时对应的 ``120101005 / ERR_NOT_GROUP_ADMIN``：
# 参考源码链接：https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/group/SetGroupBan.ts
# OneBot 会将底层 ``errMsg`` 作为自由格式的 ``message`` / ``wording`` 字段
# 传输，而不是标准的符号枚举。下面另外两个名称是兼容变体，并不表示完整
# 的官方列表；由于 NapCat 的源码没有发布稳定的符号错误码枚举，因此保留
# 通用的 ERR_NOT_* 和 *_ADMIN/*_OWNER 规则。
_PERMISSION_ERROR_CODE_PATTERN = re.compile(
    r"\bERR_NOT_[A-Z0-9_]+\b|\b[A-Z0-9_]+_(?:ADMIN|OWNER)\b"
)


def _is_permission_failure(failure: ActionFailure) -> bool:
    """识别群内权限不足，避免把账号能力错误锁定到全局。"""

    error_type = (failure.error_type or "").lower()
    if any(word in error_type for word in ("forbidden", "permission", "unauthorized")):
        return True
    status = (failure.status or "").lower()
    if status in {"forbidden", "unauthorized", "permission_denied", "403"}:
        return True
    details = " ".join(
        value.upper()
        for value in (
            failure.error_type,
            failure.message,
            failure.wording,
            failure.detail,
        )
        if value
    )
    error_codes = _PERMISSION_ERROR_CODE_PATTERN.findall(details)
    if any(
        code in _PERMISSION_ERROR_CODES
        or code.startswith("ERR_NOT_")
        or code.endswith(("_ADMIN", "_OWNER"))
        for code in error_codes
    ):
        return True
    details = details.lower()
    return any(
        phrase in details
        for phrase in (
            "permission",
            "forbidden",
            "unauthorized",
            "access denied",
            "not allowed",
            "no permission",
            "权限",
            "无权",
            "不允许",
            "禁止",
            "管理员权限",
        )
    )


def _is_transient_failure(failure: ActionFailure) -> bool:
    """识别连接/超时等暂态错误，避免它们污染账号能力学习结果。"""

    raw = failure.raw
    if isinstance(raw, ConnectionError | TimeoutError | OSError):
        return True
    error_type = (failure.error_type or "").lower()
    if any(
        word in error_type for word in ("connection", "timeout", "network", "tempor")
    ):
        return True
    details = " ".join(
        value.lower()
        for value in (failure.message, failure.wording, failure.detail)
        if value
    )
    return any(
        phrase in details
        for phrase in ("connection timed out", "network unavailable", "暂时不可用")
    )


def _is_unsupported_failure(failure: ActionFailure) -> bool:
    """只识别协议端明确表示“不支持该 action”的失败（OneBot 1404 等）。

    目标不存在（如撤回已删除的消息）、参数错误等同样是平台失败，但不说明
    账号缺少该能力，不能据此永久关闭它。
    """

    if str(failure.retcode) == "1404" or isinstance(failure.raw, NotImplementedError):
        return True
    error_type = (failure.error_type or "").lower()
    if "methodnotallowed" in error_type or "notimplemented" in error_type:
        return True
    details = " ".join(
        value.lower()
        for value in (failure.message, failure.wording, failure.detail)
        if value
    )
    return any(
        phrase in details
        for phrase in ("unsupported action", "unknown action", "不支持")
    )
