"""调用统计：每分钟把宿主采集的计数写入数据库，并提供 /统计。"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from arclet.alconna import Alconna, Args, CommandMeta
from arclet.entari import Cleanup, command, plugin, scheduler
from arclet.entari.command import Match
from arclet.entari.plugin import PluginRole

from tenko.db.repositories import CommandStatRepository
from tenko.host.guards import require
from tenko.host.perm import Permission
from tenko.host.plugins.runtime import PluginRuntime
from tenko.host.render import RenderService
from tenko.host.stats import RETENTION, call_stats, hourly, load, summarize
from tenko.plugins._common import render_or_text

plugin.metadata(
    "调用统计",
    PluginRole.NORMAL,
    author=["13"],
    description="统计各插件的调用量、耗时、失败和被拒原因，/统计 查看。",
    classifier=["required"],
)

repository = CommandStatRepository()
_DAYS = re.compile(r"(\d{1,2})\s*[dD天]?")
_pruned_at: datetime | None = None


@scheduler.schedule(lambda: timedelta(minutes=1), label="tenko-call-stats")
async def flush_stats():
    global _pruned_at
    await call_stats.flush(repository)
    now = datetime.now(UTC)
    if _pruned_at is None or now - _pruned_at >= timedelta(days=1):
        _pruned_at = now
        await repository.prune(now - RETENTION)


@plugin.listen(Cleanup)
async def flush_on_exit():
    await call_stats.flush(repository)


def plugin_labels() -> dict[str, str]:
    return {info.name: info.display_name for info in PluginRuntime().discover()}


def resolve(value: str) -> str | None:
    for info in PluginRuntime().discover():
        if value in info.lookup_names or value == info.display_name:
            return info.name
    return None


def _ms(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value} ms" if value < 1000 else f"{value / 1000:.1f} s"


def _rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def rows(summaries, labels: dict[str, str]) -> list[dict[str, object]]:
    total = max((s.calls for s in summaries), default=0) or 1
    result = []
    for s in summaries:
        reason = s.to_dict()["reasons"][:1]
        actions = " / ".join(f"{k} {v}" for k, v in s.actions.most_common(3))
        result.append(
            {
                "name": labels.get(s.key, s.key) or "私聊",
                "calls": s.calls,
                "share": s.calls / total,
                "rate": _rate(s.success_rate),
                "bad": s.success_rate is not None and s.success_rate < 0.95,
                "avg": _ms(s.avg_ms),
                "render": _ms(s.avg_render_ms),
                "denied": s.denied,
                "reason": reason[0]["label"] if reason else "",
                "errors": s.errors,
                "actions": actions,
            }
        )
    return result


def text_report(title: str, items: list[dict[str, object]]) -> str:
    lines = [title]
    for item in items:
        line = (
            f"{item['name']}：{item['calls']} 次，成功率 {item['rate']}，"
            f"平均 {item['avg']}"
        )
        if item["denied"]:
            line += f"，被拒 {item['denied']}（{item['reason']}）"
        if item["actions"]:
            line += f"，动作 {item['actions']}"
        lines.append(line)
    return "\n".join(lines) if items else f"{title}\n还没有调用记录"


stats_command = Alconna(
    "统计",
    Args["first?", str]["second?", str],
    meta=CommandMeta(
        "查看各插件的调用量、耗时、失败和被拒原因",
        usage="统计 [插件] [天数，默认 1，最多 90]",
        example="/统计\n/统计 7d\n/统计 状态查询 30d",
        compact=True,
    ),
)


@command.on(stats_command)
@require(Permission.BotAdmin)
async def stats(
    first: Match[str],
    second: Match[str],
    *,
    render_service: RenderService,
):
    days, target = 1, None
    for value in (first, second):
        if not value.available:
            continue
        if match := _DAYS.fullmatch(value.result.strip()):
            days = int(match.group(1))
        elif (target := resolve(value.result)) is None:
            return f"没有找到插件 {value.result}"
    if not 1 <= days <= RETENTION.days:
        return f"天数需要在 1~{RETENTION.days} 之间"
    since, records = await load(repository, days)
    labels = plugin_labels()
    period = "24 小时" if days == 1 else f"{days} 天"
    if target is None:
        title = f"最近 {period}的插件调用"
        items = rows(summarize(records, lambda r: r.plugin), labels)
        groups: list[dict[str, object]] = []
    else:
        records = [r for r in records if r.plugin == target]
        title = f"{labels.get(target, target)} · 最近 {period}"
        items = rows(summarize(records, lambda r: r.handler), {})
        groups = rows(summarize(records, lambda r: r.group_id), {})[:8]
    (total,) = summarize(records, lambda r: "") or [None]
    counts = hourly(records, since)
    # 一天内按小时，更长按天。
    trend = (
        counts
        if days == 1
        else [sum(counts[i : i + 24]) for i in range(0, len(counts), 24)]
    )
    data = {
        "title": "调用统计",
        "subtitle": title,
        "total": total.calls if total else 0,
        "rate": _rate(total.success_rate) if total else "—",
        "denied": total.denied if total else 0,
        "errors": total.errors if total else 0,
        "items": items,
        "groups": groups,
        "trend": trend,
        "trend_unit": "小时" if days == 1 else "天",
        "by_command": target is not None,
    }
    return await render_or_text(
        render_service, "stats.html", data, text_report(title, items)
    )
