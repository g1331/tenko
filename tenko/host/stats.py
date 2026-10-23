"""调用统计：宿主自动记录每个插件处理函数的调用量、耗时、结果和平台动作。

插件不需要写任何代码。计时器挂在 letoderea 的 ``global_propagators`` 上，每个处理
函数注册时都会带上它，所以必须在插件加载前调用 :func:`install`。

- 命令：Alconna 匹配之后开始计时，被拒（含原因）、成功、报错都记一次。
- 被动监听（如复读）：每条消息都会经过，只在它回复了、做了平台动作或报错时才记。
- 命令策略（开关、维护、限流）在 Alconna 匹配阶段就拒绝，由 ``CommandPolicy`` 直接
  调用 :meth:`CallStats.denied` 记录。
- 回复条数和平台动作在 ``GuardedProtocol.call_api`` 里归到当前调用；渲染耗时由
  渲染服务累加。

计数先留在内存，每分钟批量写入 ``TenkoCommandStat``，按小时汇总。不记参数值、
用户 ID 和消息内容。沙盒里的调用不统计。
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterator
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from arclet.entari.command.provider import AlconnaSuppiler
from arclet.letoderea import Contexts, Propagator
from arclet.letoderea.context import EVENT
from arclet.letoderea.scope import global_propagators
from loguru import logger

from ..context import MessageContext
from .plugins.runtime import external_plugin_for_module
from .sandbox import SANDBOX_PLATFORM

RETENTION = timedelta(days=90)
_BUILTIN_PREFIX = "tenko.plugins."


@dataclass(slots=True)
class _Call:
    plugin: str
    handler: str
    command: bool
    subcommand: str
    platform: str
    group_id: str
    started: float = field(default_factory=time.perf_counter)
    outcome: str | None = None
    render_ms: int = 0
    replies: int = 0
    actions: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class _Bucket:
    count: int = 0
    total_ms: int = 0
    max_ms: int = 0
    render_ms: int = 0
    replies: int = 0
    actions: Counter[str] = field(default_factory=Counter)


_current: ContextVar[_Call | None] = ContextVar("tenko_call", default=None)


def plugin_for_module(module: str) -> str | None:
    """处理函数所在模块属于哪个插件：内置插件取目录名，扩展插件取插件 ID。"""

    if module.startswith(_BUILTIN_PREFIX):
        return module[len(_BUILTIN_PREFIX) :].split(".")[0]
    return external_plugin_for_module(module)


def _hour(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)


class CallStats:
    def __init__(self) -> None:
        self._buckets: dict[tuple[Any, ...], _Bucket] = {}

    def _add(
        self,
        key: tuple[str, str, str, str, str, str],
        elapsed_ms: int = 0,
        call: _Call | None = None,
    ) -> None:
        bucket = self._buckets.setdefault((_hour(), *key), _Bucket())
        bucket.count += 1
        bucket.total_ms += elapsed_ms
        bucket.max_ms = max(bucket.max_ms, elapsed_ms)
        if call is not None:
            bucket.render_ms += call.render_ms
            bucket.replies += call.replies
            bucket.actions.update(call.actions)

    def denied(
        self, plugin: str, handler: str, context: MessageContext, reason: str
    ) -> None:
        if context.platform == SANDBOX_PLATFORM:
            return
        group = context.channel_id if context.chat_type == "group" else ""
        self._add((plugin, handler, "", context.platform, group, f"denied:{reason}"))

    def finish(self, call: _Call, error: bool) -> None:
        outcome = "error" if error else call.outcome
        if outcome is None:
            return  # 被插件自己的过滤器停下，不算一次调用
        if not call.command and outcome != "error":
            # 被动监听每条消息都会经过，没做事的不算。
            if outcome != "ok" or not (call.replies or call.actions):
                return
        elapsed = int((time.perf_counter() - call.started) * 1000)
        key = (
            call.plugin,
            call.handler,
            call.subcommand,
            call.platform,
            call.group_id,
            outcome,
        )
        self._add(key, elapsed, call)

    def records(self, *, clear: bool = False) -> list[Any]:
        """内存里尚未落库的计数（repository 记录）；``clear`` 时取出并清空。"""

        from ..db.repositories import CommandStatRecord

        buckets = self._buckets
        if clear:
            self._buckets = {}
        return [
            CommandStatRecord(
                *key,
                bucket.count,
                bucket.total_ms,
                bucket.max_ms,
                bucket.render_ms,
                bucket.replies,
                dict(bucket.actions),
            )
            for key, bucket in buckets.items()
        ]

    async def flush(self, repository: Any) -> None:
        records = self.records(clear=True)
        if not records:
            return
        try:
            await repository.add(records)
        except Exception:
            # 写失败放回内存，下次一起写；不能因为统计影响插件。
            logger.exception("调用统计写入失败，稍后重试")
            for record in records:
                self._merge_back(record)

    def _merge_back(self, record: Any) -> None:
        bucket = self._buckets.setdefault(record.key, _Bucket())
        bucket.count += record.count
        bucket.total_ms += record.total_ms
        bucket.max_ms = max(bucket.max_ms, record.max_ms)
        bucket.render_ms += record.render_ms
        bucket.replies += record.replies
        bucket.actions.update(record.actions)


call_stats = CallStats()


REASON_LABELS = {
    "chat": "聊天类型不符",
    "permission": "权限不足",
    "controlled": "受控插件",
    "maintenance": "维护中",
    "feature_off": "功能关闭",
    "rate_limited": "限流",
    "unavailable": "状态不可用",
    "unmanaged": "未登记插件",
    "other": "其他",
}


@dataclass(slots=True)
class Summary:
    """一组调用记录的汇总（按插件、命令或群）。"""

    key: str
    calls: int = 0
    ok: int = 0
    errors: int = 0
    denied: int = 0
    # 处理函数实际执行（成功或报错）的耗时。
    run_ms: int = 0
    max_ms: int = 0
    render_ms: int = 0
    replies: int = 0
    reasons: Counter[str] = field(default_factory=Counter)
    actions: Counter[str] = field(default_factory=Counter)

    @property
    def ran(self) -> int:
        return self.ok + self.errors

    @property
    def success_rate(self) -> float | None:
        return self.ok / self.ran if self.ran else None

    @property
    def avg_ms(self) -> int | None:
        return self.run_ms // self.ran if self.ran else None

    @property
    def avg_render_ms(self) -> int | None:
        return self.render_ms // self.ran if self.ran and self.render_ms else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "calls": self.calls,
            "ok": self.ok,
            "errors": self.errors,
            "denied": self.denied,
            "success_rate": self.success_rate,
            "avg_ms": self.avg_ms,
            "max_ms": self.max_ms,
            "avg_render_ms": self.avg_render_ms,
            "replies": self.replies,
            "reasons": [
                {"reason": r, "label": REASON_LABELS.get(r, r), "count": n}
                for r, n in self.reasons.most_common()
            ],
            "actions": dict(self.actions.most_common()),
        }


def summarize(records: Any, key: Any) -> list[Summary]:
    """按 ``key(record)`` 分组汇总，调用多的在前。"""

    groups: dict[str, Summary] = {}
    for record in records:
        item = groups.setdefault(str(key(record)), Summary(str(key(record))))
        item.calls += record.count
        if record.outcome == "ok":
            item.ok += record.count
        elif record.outcome == "error":
            item.errors += record.count
        else:
            item.denied += record.count
            item.reasons[record.outcome.removeprefix("denied:")] += record.count
        if record.outcome in {"ok", "error"}:
            item.run_ms += record.total_ms
            item.max_ms = max(item.max_ms, record.max_ms)
            item.render_ms += record.render_ms
        item.replies += record.replies
        item.actions.update(record.actions)
    return sorted(groups.values(), key=lambda s: (-s.calls, s.key))


def hourly(records: Any, since: datetime) -> list[int]:
    """每小时的调用量，从 ``since`` 所在整点到当前整点。"""

    start = _hour(since)
    slots = int((_hour() - start) / timedelta(hours=1)) + 1
    counts = [0] * slots
    for record in records:
        index = int((record.hour - start) / timedelta(hours=1))
        if 0 <= index < slots:
            counts[index] += record.count
    return counts


async def load(repository: Any, days: int) -> tuple[datetime, list[Any]]:
    """最近 ``days`` 天的记录：数据库里的加上内存里尚未落库的。"""

    since = _hour() - timedelta(days=days) + timedelta(hours=1)
    stored = await repository.since(since)
    return since, [*stored, *call_stats.records()]


def current_call() -> _Call | None:
    return _current.get()


def mark_denied(reason: str) -> None:
    """守卫拒绝当前调用时调用。"""

    if (call := _current.get()) is not None:
        call.outcome = f"denied:{reason}"


@contextmanager
def render_timer() -> Iterator[None]:
    """渲染服务用：把渲染耗时计入当前调用。"""

    started = time.perf_counter()
    try:
        yield
    finally:
        if (call := _current.get()) is not None:
            call.render_ms += int((time.perf_counter() - started) * 1000)


def record_api(action: str) -> None:
    """真实账号的写操作：发消息算一次回复，其余算平台动作。"""

    if (call := _current.get()) is None:
        return
    if action == "message.create":
        call.replies += 1
    else:
        call.actions[action.removeprefix("internal/")] += 1


def _command_info(subscriber: Any, ctx: Contexts) -> tuple[str, str] | None:
    try:
        command = subscriber.get_propagator(AlconnaSuppiler).cmd
    except ValueError:
        return None
    arparma = getattr(ctx.get("alc_result"), "result", None)
    # 选项的默认值也会出现在解析结果里，只记输入里真的写了的；子命令只在用到时出现。
    tokens = set(str(getattr(arparma, "origin", "")).split())
    used = [*getattr(arparma, "subcommands", {})]
    for option in getattr(command, "options", ()):
        if getattr(option, "dest", None) in getattr(arparma, "options", {}) and (
            tokens & set(getattr(option, "aliases", ()))
        ):
            used.append(option.dest)
    return str(command.command), " ".join(sorted(map(str, used)))[:128]


class _Tracker(Propagator):
    """每个处理函数注册时都会经过这里（letoderea 先 ``validate`` 再 ``compose``）。

    命令处理函数由 Entari 的 ``execute`` 以 ``inner=True`` 调用，运行时上下文里的
    ``SUBSCRIBER`` 是 ``execute`` 而不是命令本身，所以在注册时记下处理函数，用闭包
    带进计时函数。不属于任何插件的处理函数（宿主自己的）不挂计时器。
    """

    def __init__(self) -> None:
        self._target: Any = None
        self._plugin: str | None = None

    def validate(self, subscriber: Any) -> bool:
        module = getattr(subscriber.callable_target, "__module__", "")
        self._target, self._plugin = subscriber, plugin_for_module(str(module))
        return self._plugin is not None

    def compose(self):
        subscriber, plugin = self._target, self._plugin

        # 参数只用普通类：ExceptionEvent 等的提供者会对注解调用 issubclass，
        # ``Session | None`` 这类联合类型会让处理函数注册失败。
        async def start(ctx: Contexts, stack: AsyncExitStack):
            _start(subscriber, plugin, ctx, stack)

        # Alconna 匹配是 70，权限等守卫从 80 开始：只统计真正命中命令的消息。
        yield start, True, 75
        yield _succeed, False, 0


def _start(subscriber: Any, plugin: str, ctx: Contexts, stack: Any) -> None:
    origin = getattr(ctx.get(EVENT), "_origin", None)
    if origin is None or stack is None:
        return
    try:
        context = MessageContext.from_event(origin)
    except (AttributeError, TypeError, ValueError):
        return
    if context.chat_type not in {"group", "private"}:
        return
    if context.platform == SANDBOX_PLATFORM:
        return
    command = _command_info(subscriber, ctx)
    call = _Call(
        plugin,
        command[0] if command else str(getattr(subscriber, "label", "?")),
        command is not None,
        command[1] if command else "",
        context.platform,
        context.channel_id if context.chat_type == "group" else "",
    )
    _current.set(call)

    async def end(exc_type, exc, tb):
        call_stats.finish(call, error=exc is not None)

    # 退出栈在最外层处理函数结束时关闭，成功、被拒、报错都会走到这里。
    stack.push_async_exit(end)


async def _succeed():
    if (call := _current.get()) is not None:
        call.outcome = "ok"


_tracker = _Tracker()


def install() -> None:
    """在插件加载前把计时器加入 letoderea 的全局传播器（重复调用无副作用）。"""

    if _tracker not in global_propagators:
        global_propagators.append(_tracker)


__all__ = [
    "REASON_LABELS",
    "RETENTION",
    "CallStats",
    "Summary",
    "hourly",
    "load",
    "summarize",
    "call_stats",
    "current_call",
    "install",
    "mark_denied",
    "plugin_for_module",
    "record_api",
    "render_timer",
]
