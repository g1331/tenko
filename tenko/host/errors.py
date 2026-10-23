"""异常聚合：同一错误只立即报告一次，之后按小时汇总。

指纹是“异常类型 + 插件代码里最深一帧的 文件:行号”，不含异常消息：消息里常带群号、
用户名等变化内容，按消息去重会失效。只存在进程内存，重启（包括升级、更新插件）后
同一错误会重新报告一次。
"""

from __future__ import annotations

import itertools
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType

SUMMARY_INTERVAL = 3600.0
MAX_ERRORS = 200
# 失败调用的原始输入只留在内存里，供在沙盒重放。
MAX_FAILURES = 50
_failure_ids = itertools.count(1)


@dataclass(frozen=True, slots=True)
class Failure:
    """一次失败调用的原始输入。"""

    fingerprint: str
    at: datetime
    platform: str
    account_id: str
    chat: str  # group / private
    group_id: str
    user_id: str
    content: str  # Satori XML
    preview: str
    id: int = field(default_factory=lambda: next(_failure_ids))


@dataclass(slots=True)
class ErrorRecord:
    fingerprint: str
    kind: str
    location: str
    message: str
    traceback: str
    first_seen: datetime
    last_seen: datetime
    total: int = 1
    # 上次报告或汇总之后又发生的次数、涉及的群。
    pending: int = 0
    groups: set[str] = field(default_factory=set)
    reported_at: float = field(default_factory=time.monotonic)


def _in_modules(name: str, modules: tuple[str, ...]) -> bool:
    return any(name == m or name.startswith(m + ".") for m in modules)


def error_location(exception: BaseException, modules: tuple[str, ...]) -> str:
    """``modules`` 是插件模块名；没有插件帧时退回最深一帧。"""

    found = last = None
    tb: TracebackType | None = exception.__traceback__
    while tb is not None:
        last = tb
        if _in_modules(str(tb.tb_frame.f_globals.get("__name__", "")), modules):
            found = tb
        tb = tb.tb_next
    tb = found or last
    if tb is None:
        return "?"
    return f"{tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}"


class ErrorBook:
    def __init__(self) -> None:
        self._records: dict[str, ErrorRecord] = {}
        self.failures: deque[Failure] = deque(maxlen=MAX_FAILURES)

    def clear(self) -> None:
        self._records.clear()
        self.failures.clear()

    def failure(self, failure_id: int) -> Failure | None:
        return next((f for f in self.failures if f.id == failure_id), None)

    def records(self) -> list[ErrorRecord]:
        return sorted(self._records.values(), key=lambda r: r.last_seen, reverse=True)

    def record(
        self,
        exception: BaseException,
        modules: tuple[str, ...] = (),
        group: str | None = None,
        failure: dict[str, str] | None = None,
    ) -> bool:
        """登记一次异常，返回是否该立即报告（指纹首次出现）。

        ``failure`` 是触发它的消息（:class:`Failure` 除指纹、时间外的字段），留作重放。
        """

        kind = f"{type(exception).__module__}.{type(exception).__qualname__}"
        location = error_location(exception, modules)
        fingerprint = f"{kind}@{location}"
        if failure is not None:
            at = datetime.now().astimezone()
            self.failures.append(Failure(fingerprint, at, **failure))
        now = datetime.now().astimezone()
        message = str(exception)
        stack = "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
        record = self._records.get(fingerprint)
        if record is None:
            if len(self._records) >= MAX_ERRORS:
                oldest = min(self._records.values(), key=lambda r: r.last_seen)
                del self._records[oldest.fingerprint]
            self._records[fingerprint] = ErrorRecord(
                fingerprint, kind, location, message, stack, now, now
            )
            return True
        record.total += 1
        record.pending += 1
        record.last_seen = now
        record.message, record.traceback = message, stack
        if group:
            record.groups.add(group)
        return False

    def due(self, now: float | None = None) -> list[ErrorRecord]:
        """距上次报告满一小时、期间又发生过的错误；取出即视为已汇总。"""

        now = time.monotonic() if now is None else now
        due = [
            r
            for r in self._records.values()
            if r.pending and now - r.reported_at >= SUMMARY_INTERVAL
        ]
        for record in due:
            record.reported_at = now
        return due

    def settle(self, record: ErrorRecord) -> None:
        record.pending = 0
        record.groups.clear()


def summary_text(record: ErrorRecord) -> str:
    groups = f"，涉及 {len(record.groups)} 个群" if record.groups else ""
    return (
        "[Tenko 异常汇总]\n"
        f"{record.kind}（{record.location}）上次报告后又发生 {record.pending} 次{groups}。\n"
        f"最近一次 {record.last_seen:%m-%d %H:%M}：{record.message[:200]}"
    )


error_book = ErrorBook()

__all__ = [
    "ErrorBook",
    "ErrorRecord",
    "Failure",
    "error_book",
    "error_location",
    "summary_text",
]
