"""升级页：不兼容插件的处理、重启期间断线、恢复后报告新版本；后端为路由替身。"""

from webui_browser_support import Panel, confirm_box

URL = "/api/manage/upgrade"


def status(version="1.0.0", job=None):
    return {
        "enabled": True,
        "current_version": version,
        "channel": "stable",
        "policy": "check",
        "check_interval_hours": 6,
        "previous_version": None,
        "last_check": {
            "checked_at": "2026-10-20T01:00:00Z",
            "status": "ok",
            "current_version": "1.0.0",
            "channel": "stable",
            "candidate": {
                "tag": "v1.1.0",
                "version": "1.1.0",
                "prerelease": False,
                "source": "github",
                "notes": "修复若干问题",
            },
        },
        "job": job,
        "history": [],
    }


def job(state, **overrides):
    return {
        "action": "upgrade",
        "state": state,
        "stage": "",
        "message": "",
        "target": "v1.1.0",
        "incompatible": {},
        **overrides,
    }


class UpgradePanel(Panel):
    """GET 依次返回 replies 里的状态；None 表示宿主正在重启、连不上。"""

    def __init__(self, first):
        super().__init__(authenticated=True)
        self.replies = [first]

    def handle(self, route):
        if not route.request.url.endswith(URL) or route.request.method != "GET":
            super().handle(route)
            return
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if reply is None:
            route.abort()
        else:
            route.fulfill(json={"ok": True, "data": reply})


def test_upgrade_suspends_incompatible_plugins_and_follows_restart(open_panel):
    failed = job(
        "failed",
        incompatible={"old": "需要 tenko<1.1", "rep": "需要 tenko-api<0.4"},
        options={"old": [], "rep": ["0.4.0", "0.3.0"]},
    )
    panel = UpgradePanel(status(job=failed))
    panel.write_results[URL] = job("running", stage="下载并校验 v1.1.0")
    page = open_panel(panel, "upgrade", clock=True)

    page.get_by_text("需要 tenko<1.1").wait_for()
    # 默认停用；rep 改成目录里能用的最旧版本（改动最小）。
    page.locator(".incompatible li", has_text="rep").locator(".el-select").click()
    page.get_by_role("option", name="升级到 0.3.0（改动最小）").click()
    page.get_by_role("button", name="按所选方式处理后升级").click()
    confirm_box(page, "升级宿主", "升级")
    page.locator(".job-title", has_text="下载并校验 v1.1.0").wait_for()
    assert panel.calls("POST", URL)[0][3] == {
        "action": "upgrade",
        "confirm": True,
        "plugins": {"old": "suspend", "rep": "0.3.0"},
    }

    panel.replies = [status(job=job("restarting")), None, status("1.1.0")]
    for _ in range(3):
        page.clock.run_for(2100)
    page.get_by_text("已重启，当前版本 1.1.0").wait_for()
    assert page.locator(".current .version").inner_text() == "1.1.0"
    assert not page.errors
