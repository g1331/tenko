"""面板统计页（#235）：插件调用量、耗时、被拒原因和最近的错误。

数据与 ``/统计`` 相同：数据库里按小时汇总的记录加上内存里尚未落库的计数。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.requests import Request

from ..host import sandbox as sandbox_module
from ..host.errors import error_book
from ..host.plugins.runtime import PluginRuntime
from ..host.stats import REASON_LABELS, Summary, hourly, load, summarize
from .auth import ApiResponse, error_response, read_json, success_response
from .contracts import (
    StatsData,
    StatsDays,
    StatsError,
    StatsFailure,
    StatsGroup,
    StatsPlugin,
    StatsReplayResult,
    StatsSummary,
)

if TYPE_CHECKING:
    from .service import WebUIService

PERIODS: dict[int, StatsDays] = {1: 1, 7: 7, 30: 30}
TOP_GROUPS = 10


def _summary(item: Summary) -> StatsSummary:
    return {
        "key": item.key,
        "calls": item.calls,
        "ok": item.ok,
        "errors": item.errors,
        "denied": item.denied,
        "success_rate": item.success_rate,
        "avg_ms": item.avg_ms,
        "max_ms": item.max_ms,
        "avg_render_ms": item.avg_render_ms,
        "replies": item.replies,
        "reasons": [
            {"reason": r, "label": REASON_LABELS.get(r, r), "count": n}
            for r, n in item.reasons.most_common()
        ],
        "actions": dict(item.actions.most_common()),
    }


class StatsPanel:
    def __init__(self, service: WebUIService) -> None:
        self.service = service

    def routes(self) -> tuple[tuple[str, list[str], Any], ...]:
        return (
            ("/api/manage/stats", ["GET"], self.stats),
            ("/api/manage/stats/replay", ["POST"], self.replay),
        )

    async def replay(self, request: Request) -> ApiResponse[StatsReplayResult]:
        """把一次失败调用的原始输入以 Master 身份在沙盒里重放。"""

        async def action() -> StatsReplayResult:
            body = await read_json(request)
            failure_id = body.get("id")
            failure = (
                error_book.failure(failure_id) if type(failure_id) is int else None
            )
            if failure is None:
                raise ValueError(
                    "这条失败记录已经不在了（只保留最近 50 条，重启后清空）"
                )
            if sandbox_module.sandbox is None:
                raise RuntimeError("沙盒还没有就绪，请稍后再试")
            request.state.webui_action = f"stats-replay {failure.id}"
            await sandbox_module.sandbox.replay(
                failure.chat, failure.content, failure.account_id
            )
            return {"chat": "group" if failure.chat == "group" else "private"}

        return await self.service.manage(request, action, lock=False)

    async def stats(self, request: Request) -> ApiResponse[StatsData]:
        try:
            requested = int(request.query_params.get("days", "1"))
        except ValueError:
            requested = 0
        days = PERIODS.get(requested)
        if days is None:
            return error_response("invalid_days", "时间范围只能是 1、7 或 30 天", 400)
        # repository 依赖官方 database 插件已加载：模块顶层导入会让它以默认配置初始化。
        from ..db.repositories import CommandStatRepository

        since, records = await load(CommandStatRepository(), days)
        names = await self.service.group_names()
        infos = PluginRuntime().discover()
        labels = {info.name: info.display_name for info in infos}

        def group_rows(rows: list[Any]) -> list[StatsGroup]:
            return [
                {**_summary(s), "name": names.get(s.key, "") if s.key else "私聊"}
                for s in summarize(rows, lambda r: r.group_id)[:TOP_GROUPS]
            ]

        plugins: list[StatsPlugin] = []
        for item in summarize(records, lambda r: r.plugin):
            rows = [r for r in records if r.plugin == item.key]
            plugins.append(
                {
                    **_summary(item),
                    "label": labels.get(item.key, item.key),
                    "commands": [
                        _summary(s) for s in summarize(rows, lambda r: r.handler)
                    ],
                    "groups": group_rows(rows),
                }
            )

        def failures(fingerprint: str) -> list[StatsFailure]:
            rows: list[StatsFailure] = [
                {
                    "id": f.id,
                    "at": f.at.isoformat(),
                    "chat": f.chat,
                    "group_id": f.group_id,
                    "group_name": names.get(f.group_id, ""),
                    "user_id": f.user_id,
                    "preview": f.preview,
                }
                for f in reversed(error_book.failures)
                if f.fingerprint == fingerprint
            ]
            return rows[:5]

        errors: list[StatsError] = [
            {
                "fingerprint": r.fingerprint,
                "kind": r.kind,
                "location": r.location,
                "message": r.message,
                "traceback": r.traceback,
                "first_seen": r.first_seen.isoformat(),
                "last_seen": r.last_seen.isoformat(),
                "total": r.total,
                "groups": sorted(names.get(g) or g for g in r.seen_groups),
                "failures": failures(r.fingerprint),
            }
            for r in error_book.records()
        ]
        used = {item["key"] for item in plugins}
        counts = hourly(records, since)
        total = next(iter(summarize(records, lambda r: "")), None)
        return success_response(
            {
                "days": days,
                "since": since.isoformat(),
                "total": _summary(total) if total else None,
                # 一天内按小时，更长按天。
                "trend": counts
                if days == 1
                else [sum(counts[i : i + 24]) for i in range(0, len(counts), 24)],
                "trend_unit": "hour" if days == 1 else "day",
                "plugins": plugins,
                "idle": sorted(
                    labels[info.name]
                    for info in infos
                    if info.native_plugin is not None and info.name not in used
                ),
                "errors": errors,
            }
        )
