"""前端外壳（登录、总览、主题、登出、移动布局）的浏览器验证；后端为路由替身。"""

import copy

from webui_browser_support import POLL_MS, Panel, _feature, sse


def log_in(page, password="correct-horse"):
    page.locator("#login-password").fill(password)
    page.get_by_role("button", name="登录", exact=True).click()


def test_unauthenticated_shows_login_and_requests_only_auth_state(open_panel):
    panel = Panel()
    page = open_panel(panel, "overview")
    page.locator("#login-username").wait_for()
    assert page.locator(".sidebar").count() == 0
    assert page.locator("#login-username").input_value() == "master"
    assert page.locator("#login-password").get_attribute("type") == "password"
    page.wait_for_timeout(300)
    assert panel.paths() == ["/api/auth/state"]
    assert page.url.endswith("#/login?redirect=/overview")
    assert not page.errors


def test_password_not_set_explains_both_ways_and_rechecks(open_panel):
    panel = Panel(password_set=False)
    page = open_panel(panel, "overview")
    page.get_by_text("尚未设置面板密码").wait_for()
    assert page.locator("#login-username").count() == 0
    text = page.locator(".login-body").inner_text()
    assert "/面板重置密码" in text
    assert "python -m tenko webui-passwd" in text

    panel.password_set = True
    page.get_by_role("button", name="我已设置，重新检查").click()
    page.locator("#login-username").wait_for()
    assert panel.paths() == ["/api/auth/state"] * 2
    assert not page.errors


def test_wrong_password_shows_inline_error(open_panel):
    panel = Panel()
    page = open_panel(panel, "overview")
    page.locator("#login-username").wait_for()
    log_in(page, "wrong")
    error = page.locator(".form-error")
    error.wait_for()
    assert "用户名或密码不正确" in error.inner_text()
    assert page.locator(".sidebar").count() == 0
    assert panel.data_requests() == ["/api/auth/login"]
    assert not page.errors


def test_lockout_shows_countdown_and_disables_submit(open_panel):
    panel = Panel()
    panel.login_error = (429, "too_many_attempts", {"retry_after": 90})
    page = open_panel(panel, "overview", clock=True)
    page.locator("#login-username").wait_for()
    log_in(page, "wrong")
    error = page.locator(".form-error")
    assert error.inner_text().startswith("登录尝试次数过多")
    assert "01:30" in error.inner_text()
    submit = page.get_by_role("button", name="登录", exact=True)
    assert submit.is_disabled()
    page.clock.run_for(1000)
    assert "01:29" in error.inner_text()
    page.clock.run_for(90_000)
    assert error.count() == 0
    assert submit.is_enabled()
    assert not page.errors


def test_login_not_set_response_switches_to_instructions(open_panel):
    panel = Panel()
    panel.login_error = (409, "password_not_set", {})
    page = open_panel(panel, "overview")
    page.locator("#login-username").wait_for()
    log_in(page, "anything")
    page.get_by_text("尚未设置面板密码").wait_for()
    assert not page.errors


def test_login_renders_overview_and_polls_every_30_seconds(open_panel):
    panel = Panel()
    page = open_panel(panel, "overview", clock=True)
    page.locator("#login-username").wait_for()
    log_in(page)
    page.locator(".page-head h1", has_text="运行总览").wait_for()
    page.get_by_text("消息活动").wait_for()
    # 登录后才请求业务数据；认证只依赖 Cookie，不发送 Authorization。
    login = panel.calls("POST", "/api/auth/login")[0]
    assert login[3] == {"username": "master", "password": "correct-horse"}
    assert login[2]["content-type"] == "application/json"
    assert all("authorization" not in headers for _m, _p, headers, _b in panel.requests)
    assert page.url.endswith("#/overview")

    # 总览：版本/在线标签、KPI、账号与插件概况、待关注。
    main = page.locator("main")
    assert "v4.0.0-test" in main.inner_text()
    assert "1 天 2 小时" in main.inner_text()
    assert "12,873" in main.inner_text()
    assert "3 项待处理" in main.inner_text()
    assert page.locator(".chart rect").count() > 60
    assert page.locator(".account-list li").count() == 2
    assert "账号 10002 离线" in main.inner_text()
    assert "入群欢迎 处于维护状态" in main.inner_text()
    assert page.locator(".sidebar .el-menu-item.is-active").inner_text() == "总览"
    assert page.locator("header .user-button").inner_text().strip().endswith("master")

    count = len(panel.paths("/api/overview"))
    page.clock.fast_forward(POLL_MS + 500)
    page.wait_for_timeout(300)
    assert len(panel.paths("/api/overview")) == count + 1
    # 数据读取成功后刷新按钮可手动触发同样的一轮。
    page.get_by_role("button", name="立即刷新").click()
    page.wait_for_timeout(300)
    assert len(panel.paths("/api/overview")) == count + 2
    assert not page.errors


def test_overview_event_updates_numbers_without_a_rest_poll(open_panel):
    panel = Panel(authenticated=True)
    pushed = {**panel.data["/api/overview"], "active_groups": 99}
    panel.events = "retry: 60000\n\n" + sse("overview", pushed)
    page = open_panel(panel, "overview")
    # 推送比挂载时那次 REST 的结果新，页面以推送为准。
    page.locator(".hero-stats").get_by_text("99").wait_for()
    assert len(panel.paths("/api/overview")) == 1
    assert not page.errors


def test_overview_ignores_installed_but_disabled_external_plugins(open_panel):
    panel = Panel(authenticated=True)
    data = copy.deepcopy(panel.data["/api/features"])  # 默认数据集是模块级共享的
    total = len(data["features"])
    data["features"].append(
        _feature("ext_demo", "演示插件", source="external", loaded=False, enabled=False)
    )
    panel.data["/api/features"] = data
    page = open_panel(panel, "overview")
    page.locator(".hero-stats").wait_for()
    main = page.locator("main")
    # 未启用的第三方插件不算“未加载”，也不进入待关注列表和总数。
    assert "演示插件" not in main.inner_text()
    assert f"/ {total}" in page.locator(".hero-stats").inner_text()
    assert not page.errors


def test_unauthorized_polling_returns_to_login(open_panel):
    panel = Panel(authenticated=True)
    page = open_panel(panel, "overview", clock=True)
    page.locator(".page-head h1").wait_for()
    panel.authenticated = False  # 服务端会话失效
    page.clock.fast_forward(POLL_MS + 500)
    page.locator("#login-username").wait_for()
    page.get_by_text("登录已失效，请重新登录").wait_for()
    assert "redirect=/overview" in page.url
    assert page.locator(".sidebar").count() == 0
    # 重新登录后回到原页面。
    log_in(page)
    page.locator(".page-head h1", has_text="运行总览").wait_for()
    assert not page.errors


def test_logout_calls_endpoint_and_returns_to_login(open_panel):
    panel = Panel(authenticated=True)
    page = open_panel(panel, "overview")
    page.locator(".page-head h1").wait_for()
    page.locator("header .user-button").click()
    page.get_by_text("退出登录").click()
    page.locator("#login-username").wait_for()
    assert len(panel.calls("POST", "/api/auth/logout")) == 1
    assert panel.authenticated is False
    page.get_by_text("已退出登录").wait_for()
    # 登出后访问受保护路由会被带回登录页。
    page.evaluate("location.hash = '#/settings'")
    page.locator("#login-username").wait_for()
    assert not page.errors


def test_user_menu_links_to_account_security(open_panel):
    page = open_panel(Panel(authenticated=True), "overview")
    page.locator("header .user-button").click()
    page.get_by_text("账号与安全").click()
    page.locator(".page-head h1", has_text="设置").wait_for()
    assert "section=account-security" in page.url


def test_theme_switch_persists_and_follows_system(open_panel):
    page = open_panel(Panel(authenticated=True), "overview")
    page.locator(".page-head h1").wait_for()
    html = page.locator("html")
    sidebar = page.locator(".sidebar")
    sidebar.get_by_role("radio", name="深色主题").check(force=True)
    assert "dark" in html.get_attribute("class")
    assert page.evaluate("localStorage.getItem('tenko-webui-theme')") == "dark"
    page.reload()
    page.locator(".page-head h1").wait_for()
    assert "dark" in html.get_attribute("class")

    sidebar.get_by_role("radio", name="跟随系统").check(force=True)
    page.emulate_media(color_scheme="light")
    page.wait_for_function("!document.documentElement.classList.contains('dark')")
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("document.documentElement.classList.contains('dark')")
    link = sidebar.get_by_role("link", name="Tenko GitHub 仓库")
    assert link.get_attribute("href") == "https://github.com/g1331/tenko"
    assert not page.errors


def test_theme_applies_before_login_page(open_panel):
    page = open_panel(Panel(), "overview")
    page.evaluate("localStorage.setItem('tenko-webui-theme', 'dark')")
    page.reload()
    page.locator("#login-username").wait_for()
    assert "dark" in page.locator("html").get_attribute("class")


def test_mobile_layout_has_no_horizontal_overflow(open_panel):
    panel = Panel()
    page = open_panel(panel, "overview", viewport=(390, 844))
    page.locator("#login-username").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    log_in(page)
    page.locator(".page-head h1").wait_for()
    nav = page.locator(".mobile-nav")
    assert nav.is_visible()
    assert page.locator(".sidebar").is_hidden()
    for label in ("总览", "账户", "插件", "开关", "日志", "设置"):
        nav.get_by_text(label, exact=True).click()
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
            label
        )
    nav.get_by_text("总览", exact=True).click()
    page.get_by_text("消息活动").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not page.errors
