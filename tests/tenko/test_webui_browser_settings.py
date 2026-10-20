"""设置页（运行设置、账号表单、登录会话）的浏览器验证；后端为路由替身。"""

import copy

from webui_browser_support import Panel

THIRD = {
    "id": "s-tablet",
    "created_at": "2026-10-17T08:00:00Z",
    "last_seen_at": "2026-10-18T08:00:00Z",
    "expires_at": "2026-10-24T08:00:00Z",
    "ip": "192.0.2.55",
    "user_agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Safari/537.36",
    "current": False,
}


class SettingsPanel(Panel):
    """会话列表为三个会话；可让 /auth/account 返回指定的失败。"""

    def __init__(self):
        super().__init__(authenticated=True)
        self.sessions.append(copy.deepcopy(THIRD))
        self.account_error = None  # (状态码, error.code, 额外字段, 响应头)

    def handle(self, route):
        path = route.request.url.split("/webui", 1)[-1].split("?")[0]
        if path == "/api/auth/account" and self.account_error:
            status, code, extra, headers = self.account_error
            self.requests.append(("POST", path, {}, route.request.post_data_json))
            error = {"code": code, "message": "后端消息", **extra}
            route.fulfill(
                status=status, json={"ok": False, "error": error}, headers=headers
            )
        else:
            super().handle(route)


def open_settings(open_panel, panel=None, route="settings", **kwargs):
    panel = panel or SettingsPanel()
    page = open_panel(panel, route, **kwargs)
    page.get_by_text("Chrome · Android").wait_for()
    return panel, page


def confirm(page, name="注销"):
    page.locator(".el-message-box").get_by_role("button", name=name, exact=True).click()


def test_default_policy_save_posts_body_and_shows_toast(open_panel):
    panel, page = open_settings(open_panel)
    save = page.get_by_role("button", name="保存运行设置")
    assert save.is_disabled()
    assert "已保存到数据库" in page.locator(".hint").first.inner_text()

    page.get_by_label("未设置的功能").click()
    page.get_by_role("option", name="默认关闭").click()
    save.click()
    page.get_by_text("运行设置已保存").wait_for()
    assert panel.calls("POST", "/api/manage/settings")[0][3] == {
        "default_enabled": False
    }
    assert not page.errors


def test_sessions_list_marks_current_and_disables_its_revoke(open_panel):
    _panel, page = open_settings(open_panel)
    items = page.locator(".item")
    assert items.count() == 3
    current = items.filter(has_text="Chrome · Linux")
    assert "当前" in current.inner_text()
    assert "203.0.113.7" in current.inner_text()
    assert current.get_by_role("button", name="注销").is_disabled()
    assert (
        items.filter(has_text="Safari · iOS")
        .get_by_role("button", name="注销")
        .is_enabled()
    )
    assert page.get_by_role("button", name="注销其他会话（2）").is_enabled()
    assert not page.errors


def test_revoke_session_needs_confirmation(open_panel):
    panel, page = open_settings(open_panel)
    phone = page.locator(".item").filter(has_text="Safari · iOS")

    phone.get_by_role("button", name="注销").click()
    confirm(page, "取消")
    page.wait_for_timeout(200)
    assert panel.calls("POST", "/api/auth/sessions/s-phone/revoke") == []

    phone.get_by_role("button", name="注销").click()
    confirm(page)
    page.get_by_text("会话已注销").wait_for()
    assert len(panel.calls("POST", "/api/auth/sessions/s-phone/revoke")) == 1
    assert len(panel.calls("GET", "/api/auth/sessions")) == 2  # 操作后重新读取
    assert not page.errors


def test_revoke_others_confirm_shows_count(open_panel):
    panel, page = open_settings(open_panel)
    page.get_by_role("button", name="注销其他会话（2）").click()
    assert "2 个会话" in page.locator(".el-message-box").inner_text()
    confirm(page)
    page.get_by_text("已注销 1 个其他会话").wait_for()
    assert len(panel.calls("POST", "/api/auth/sessions/revoke-others")) == 1
    assert not page.errors


def fill_account(page, current="", username=None, new="", confirm_new=""):
    page.get_by_label("当前密码").fill(current)
    if username is not None:
        page.get_by_label("用户名").fill(username)
    page.get_by_label("新密码", exact=True).fill(new)
    page.get_by_label("确认新密码").fill(confirm_new)
    page.get_by_role("button", name="保存账户变更").click()


def test_account_form_validation_blocks_request(open_panel):
    panel, page = open_settings(open_panel)
    assert page.get_by_label("用户名").input_value() == "master"

    fill_account(page)
    page.get_by_text("请输入当前密码").wait_for()

    fill_account(page, "correct-horse", username="bad name!")
    page.get_by_text("用户名需为 1-32 位").wait_for()

    fill_account(page, "correct-horse", username="master", new="short")
    page.get_by_text("新密码至少需要 8 个字符").wait_for()

    fill_account(page, "correct-horse", new="long-enough-1", confirm_new="different-1")
    page.get_by_text("两次输入的新密码不一致").wait_for()

    fill_account(page, "correct-horse")  # 用户名未变且无新密码
    page.get_by_text("没有需要保存的更改").wait_for()
    assert panel.calls("POST", "/api/auth/account") == []
    assert not page.errors


def test_account_update_posts_expected_body_and_updates_header(open_panel):
    panel, page = open_settings(open_panel)
    fill_account(
        page,
        "correct-horse",
        username="alice",
        new="long-enough-1",
        confirm_new="long-enough-1",
    )
    page.get_by_text("账户已更新").wait_for()
    assert panel.calls("POST", "/api/auth/account")[0][3] == {
        "current_password": "correct-horse",
        "username": "alice",
        "new_password": "long-enough-1",
    }
    assert page.locator(".user-name").inner_text() == "alice"
    assert page.get_by_label("当前密码").input_value() == ""
    assert len(panel.calls("GET", "/api/auth/sessions")) == 2  # 改密码会注销其他会话
    assert not page.errors


def test_username_only_update_omits_password_fields(open_panel):
    panel, page = open_settings(open_panel)
    fill_account(page, "correct-horse", username="alice")
    page.get_by_text("用户名已更新").wait_for()
    assert panel.calls("POST", "/api/auth/account")[0][3] == {
        "current_password": "correct-horse",
        "username": "alice",
    }


def test_wrong_current_password_is_field_error_and_keeps_session(open_panel):
    panel, page = open_settings(open_panel)
    fill_account(
        page, "wrong-password", new="long-enough-1", confirm_new="long-enough-1"
    )
    page.get_by_text("当前密码不正确").wait_for()
    assert panel.authenticated
    assert page.locator(".sidebar").is_visible()
    assert page.url.endswith("#/settings")
    assert page.get_by_label("当前密码").input_value() == "wrong-password"

    # 不修改输入直接再次提交，错误仍会再次显示。
    page.get_by_label("当前密码").fill("wrong-password-2")
    page.get_by_role("button", name="保存账户变更").click()
    page.get_by_text("当前密码不正确").wait_for()
    assert len(panel.calls("POST", "/api/auth/account")) == 2
    assert not page.errors


def test_rate_limit_and_bad_request_are_toasts(open_panel):
    panel, page = open_settings(open_panel)
    panel.account_error = (
        429,
        "too_many_attempts",
        {"retry_after": 90},
        {"Retry-After": "90"},
    )
    fill_account(page, "correct-horse", username="alice")
    page.get_by_text("尝试次数过多，请在 1 分 30 秒 后重试").wait_for()

    panel.account_error = (400, "invalid_request", {}, {})
    page.get_by_role("button", name="保存账户变更").click()
    page.get_by_text("后端消息").wait_for()
    assert panel.authenticated
    assert not page.errors


def test_section_query_scrolls_to_account_security(open_panel):
    _panel, page = open_settings(
        open_panel, route="settings?section=account-security", viewport=(1280, 400)
    )
    page.wait_for_function(
        "document.querySelector('#account-security').getBoundingClientRect().top < 120"
    )
    assert page.evaluate("window.scrollY") > 0

    # 没有 section 参数时停留在顶部。
    _panel, page = open_settings(open_panel, viewport=(1280, 400))
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") == 0
