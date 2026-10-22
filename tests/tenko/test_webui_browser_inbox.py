"""待处理页：导航角标、拒绝群邀请时带理由；后端为路由替身。"""

from webui_browser_support import Panel, toast

URL = "/api/manage/inbox"
INVITE = {
    "id": "a1",
    "kind": "group",
    "platform": "onebot",
    "account_id": "10001",
    "user_id": "20002",
    "user_name": "群友乙",
    "comment": "",
    "group_id": "40001",
    "group_name": "新的演示群",
    "created_at": "2026-10-20T01:00:00Z",
    "expires_at": "2026-10-20T02:00:00Z",
}


def test_badge_and_rejecting_a_group_invite_with_a_reason(open_panel):
    panel = Panel(authenticated=True)
    panel.data["/api/overview"] = {**panel.data["/api/overview"], "inbox": 1}
    panel.data[URL] = {"items": [INVITE]}
    panel.write_results[f"{URL}/a1"] = {"items": []}
    page = open_panel(panel, "inbox")

    assert page.locator(".sidebar .nav-badge").inner_text() == "1"
    page.get_by_role("button", name="拒绝").click()
    box = page.locator(".el-message-box")
    box.locator("input").fill("不是工作群")
    box.get_by_role("button", name="拒绝").click()
    toast(page, "已拒绝")

    assert panel.calls("POST", f"{URL}/a1")[0][3] == {
        "action": "reject",
        "confirm": True,
        "comment": "不是工作群",
    }
    page.get_by_text("没有待处理的好友申请或群邀请").wait_for()
    assert not page.errors
