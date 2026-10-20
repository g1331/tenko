import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

HTML = Path("tenko/webui/static/index.html").read_text()
POLL_MS = 30000
SESSIONS = [
    {
        "id": "s-current",
        "created_at": "2026-10-20T01:00:00Z",
        "last_seen_at": "2026-10-20T04:30:00Z",
        "expires_at": "2026-10-27T01:00:00Z",
        "ip": "203.0.113.7",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0 Safari/537.36",
        "current": True,
    },
    {
        "id": "s-phone",
        "created_at": "2026-10-18T10:00:00Z",
        "last_seen_at": "2026-10-19T22:10:00Z",
        "expires_at": "2026-10-25T10:00:00Z",
        "ip": "198.51.100.23",
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Version/18.0 Safari/604.1",
        "current": False,
    },
]
FEATURES = {
    "features": [
        {
            "plugin": "group_manager",
            "name": "群管理",
            "scope": "group",
            "protected": False,
            "loaded": True,
            "enabled": True,
            "maintenance": False,
            "global_enabled": None,
            "groups": {"40001": True, "40002": False},
        },
        {
            "plugin": "perm_manager",
            "name": "权限管理",
            "scope": "group",
            "protected": True,
            "loaded": True,
            "enabled": True,
            "maintenance": False,
            "global_enabled": None,
            "groups": {"40001": True, "40002": True},
        },
    ]
}
DATA = {
    "/api/overview": {
        "version": "4.0.0-test",
        "uptime_seconds": 93784,
        "online_accounts": 1,
        "active_groups": 2,
        "messages": {"received_count": 10, "sent_count": 2},
    },
    "/api/accounts": {"accounts": []},
    "/api/features": FEATURES,
    "/api/manage/accounts": {"accounts": [], "capabilities": []},
    "/api/manage/pairing": {"url": "ws://x/ws", "path": "/ws", "requires_host": False},
    "/api/manage/settings": {"default_enabled": True, "stored": True},
}


class Panel:
    """内存中的 WebUI 后端替身：记录请求，并按 Cookie 会话语义放行或返回 401。"""

    def __init__(self, *, authenticated=False, password_set=True):
        self.authenticated = authenticated
        self.password_set = password_set
        self.requests = []  # (method, 路径, 请求头, 请求体)
        self.data = dict(DATA)
        self.login_error = None

    def paths(self, prefix=""):
        return [path for _m, path, _h, _b in self.requests if path.startswith(prefix)]

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def data_requests(self):
        return [p for p in self.paths() if p != "/api/auth/state"]

    def handle(self, route):
        request = route.request
        path = request.url.split("/webui")[-1].split("?")[0]
        if not path:
            route.fulfill(content_type="text/html", body=HTML)
            return
        body = json.loads(request.post_data) if request.post_data else None
        self.requests.append((request.method, path, request.headers, body))

        def ok(data):
            route.fulfill(json={"ok": True, "data": data})

        def fail(status, code, message="x", **extra):
            error = {"code": code, "message": message, **extra}
            route.fulfill(status=status, json={"ok": False, "error": error})

        if path == "/api/auth/state":
            ok(
                {
                    "authenticated": self.authenticated,
                    "username": "master" if self.authenticated else None,
                    "password_set": self.password_set,
                    "expires_at": "2026-10-27T01:00:00Z"
                    if self.authenticated
                    else None,
                }
            )
        elif path == "/api/auth/login":
            if self.login_error:
                fail(*self.login_error[:2], **self.login_error[2])
            elif body["password"] == "correct-horse":
                self.authenticated = True
                ok({"username": body["username"], "expires_at": "2026-10-27T01:00:00Z"})
            else:
                fail(401, "invalid_credentials", "bad credentials")
        elif path == "/api/auth/logout":
            self.authenticated = False
            ok({})
        elif not self.authenticated:
            fail(401, "unauthorized", "login required")
        elif path == "/api/auth/sessions":
            ok({"sessions": SESSIONS})
        elif path == "/api/auth/account":
            if body["current_password"] != "correct-horse":
                fail(401, "invalid_credentials", "bad password")
            else:
                ok({"username": body.get("username", "master")})
        elif path.startswith("/api/auth/sessions/"):
            ok({"revoked": 1})
        elif path.startswith("/api/manage/") and request.method == "POST":
            ok({})
        elif path == "/api/manage/logs":
            ok({"files": [], "lines": ["12:00 INFO hello"], "history_enabled": True})
        else:
            ok(self.data.get(path, {}))


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("浏览器验证需要 playwright install chromium")
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def open_panel(browser):
    pages = []

    def open_page(panel, route="settings", *, clock=False, viewport=(1280, 720)):
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.errors = []
        page.on("pageerror", lambda error: page.errors.append(str(error)))
        if clock:
            page.clock.install()
        page.route("https://tenko.test/webui**", panel.handle)
        page.goto(f"https://tenko.test/webui#/{route}")
        pages.append(page)
        return page

    yield open_page
    for page in pages:
        page.close()


def log_in(page, password="correct-horse"):
    page.locator("#login-password").fill(password)
    page.locator("#login-submit").click()


def test_unauthenticated_shows_login_and_requests_no_data(open_panel):
    panel = Panel()
    page = open_panel(panel, "overview")
    page.locator("#login-form").wait_for()
    assert page.locator("#app").is_hidden()
    assert page.locator("#login-username").input_value() == "master"
    assert page.locator("#login-password").get_attribute("type") == "password"
    page.wait_for_timeout(300)
    assert panel.paths() == ["/api/auth/state"]
    assert not page.errors


def test_password_not_set_explains_both_ways(open_panel):
    page = open_panel(Panel(password_set=False), "overview")
    unset = page.locator("#login-unset")
    unset.wait_for()
    assert page.locator("#login-form").is_hidden()
    assert "/面板重置密码" in unset.inner_text()
    assert "python -m tenko webui-passwd" in unset.inner_text()


def test_login_errors_and_lockout(open_panel):
    panel = Panel()
    page = open_panel(panel, "overview")
    page.locator("#login-form").wait_for()
    log_in(page, "wrong")
    error = page.locator("#login-error")
    error.wait_for()
    assert "不正确" in error.inner_text()
    assert page.locator("#app").is_hidden()
    assert panel.data_requests() == ["/api/auth/login"]

    panel.login_error = (429, "too_many_attempts", {"retry_after": 90})
    log_in(page, "wrong")
    page.wait_for_function(
        "['01:30', '01:29'].includes("
        "document.querySelector('#login-countdown').textContent)"
    )
    assert page.locator("#login-submit").is_disabled()

    # 密码尚未设置时，服务端返回 409，页面切换为设置说明。
    page.reload()
    panel.login_error = (409, "password_not_set", {})
    page.locator("#login-form").wait_for()
    log_in(page, "anything")
    page.locator("#login-unset").wait_for()
    assert not page.errors


def test_login_flow_navigation_and_form_stability(open_panel, tmp_path):
    """登录后检查正文滚动、导航工具、主题，以及后台轮询不打断表单。"""
    panel = Panel()
    page = open_panel(panel, "settings", clock=True)
    page.locator("#login-form").wait_for()
    log_in(page)
    select = page.locator('[name="default_enabled"]')
    select.wait_for()
    page.locator("#connection-dot.ok").wait_for()
    assert page.locator("#login-view").is_hidden()
    assert page.locator("#user-name").inner_text() == "master"
    # 认证只依赖 Cookie：不再发送 Authorization，写请求使用 JSON。
    assert all("authorization" not in headers for _m, _p, headers, _b in panel.requests)
    login = panel.calls("POST", "/api/auth/login")[0]
    assert login[2]["content-type"] == "application/json"
    assert login[3] == {"username": "master", "password": "correct-horse"}

    select.select_option("false")
    page.locator("h3").first.click()
    page.evaluate("window.originalForm = document.querySelector('#runtime-settings')")
    count = len(panel.requests)
    page.clock.fast_forward(11000)
    assert select.input_value() == "false"
    assert len(panel.requests) == count
    page.clock.fast_forward(POLL_MS)
    page.wait_for_timeout(300)
    assert len(panel.requests) > count  # 后台轮询确实发生
    assert select.input_value() == "false"
    assert page.evaluate(
        "window.originalForm === document.querySelector('#runtime-settings')"
    )
    # 手动刷新会以服务端数据重新渲染。
    page.locator("#refresh-button").click()
    page.wait_for_function(
        "document.querySelector('[name=default_enabled]')?.value === 'true'"
    )

    for item in page.locator(".sidebar-nav .nav-item").all():
        item.hover()
        page.wait_for_timeout(180)
        assert page.locator(".sidebar-nav").evaluate(
            "nav => nav.scrollWidth === nav.clientWidth"
        )

    page.set_viewport_size({"width": 1280, "height": 1200})
    page.evaluate("window.scrollTo(0, 0)")
    footer = page.locator("#page-footer")
    box = footer.bounding_box()
    assert abs(box["y"] + box["height"] - 1200) <= 1
    page.set_viewport_size({"width": 1280, "height": 720})
    page.evaluate("document.querySelector('#page-root').style.minHeight = '2400px'")
    assert footer.bounding_box()["y"] >= page.locator("main").evaluate(
        "main => main.getBoundingClientRect().bottom"
    )
    page.evaluate("window.scrollTo(0, 1200)")
    page.wait_for_timeout(350)
    sidebar = page.locator(".sidebar").bounding_box()
    assert sidebar["y"] == 0
    assert sidebar["height"] == 720
    theme = page.locator('.sidebar [data-theme-choice="dark"]')
    theme.click()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.evaluate("localStorage.getItem('tenko-webui-theme')") == "dark"
    link = page.locator(".sidebar .github-link")
    assert link.get_attribute("href") == "https://github.com/g1331/tenko"
    assert link.locator("svg").count() == 1
    assert link.bounding_box()["y"] + link.bounding_box()["height"] <= 720
    page.evaluate("document.querySelector('#page-root').style.minHeight = ''")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(tmp_path / "desktop.png"))
    page.locator('.sidebar [data-theme-choice="system"]').click()
    page.emulate_media(color_scheme="light")
    page.wait_for_function("document.documentElement.dataset.theme === 'light'")
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")

    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("window.scrollTo(0, 0)")
    mobile_theme = page.locator('.mobile-topbar [data-theme-choice="light"]')
    assert mobile_theme.is_visible()
    mobile_theme.click()
    assert theme.get_attribute("aria-pressed") == "false"
    assert page.locator("html").get_attribute("data-theme") == "light"
    for route in ("overview", "accounts", "plugins", "switches", "logs", "settings"):
        page.evaluate(f"location.hash = '#/{route}'")
        page.wait_for_timeout(200)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), route
    page.screenshot(path=str(tmp_path / "mobile.png"))
    assert not page.errors


def test_unauthorized_response_returns_to_login(open_panel):
    panel = Panel(authenticated=True)
    page = open_panel(panel, "overview", clock=True)
    page.locator("#connection-dot.ok").wait_for()
    panel.authenticated = False  # 服务端会话失效
    page.clock.fast_forward(POLL_MS + 1000)
    page.locator("#login-form").wait_for()
    assert page.locator("#app").is_hidden()
    assert "失效" in page.locator("#login-banner").inner_text()
    # 回到登录页后不再有后台数据请求。
    count = len(panel.requests)
    page.clock.fast_forward(POLL_MS * 2)
    page.wait_for_timeout(300)
    assert len(panel.requests) == count
    assert not page.errors


def test_logout_calls_endpoint(open_panel):
    panel = Panel(authenticated=True)
    page = open_panel(panel, "overview")
    page.locator("#connection-dot.ok").wait_for()
    page.locator("#user-button").click()
    assert page.locator("#user-button").get_attribute("aria-expanded") == "true"
    page.locator("#logout-button").click()
    page.locator("#login-form").wait_for()
    logout = panel.calls("POST", "/api/auth/logout")
    assert len(logout) == 1 and logout[0][3] == {}
    assert logout[0][2]["content-type"] == "application/json"
    assert page.locator("#app").is_hidden()
    assert not page.errors


def test_account_and_session_management(open_panel):
    panel = Panel(authenticated=True)
    page = open_panel(panel, "settings")
    sessions = page.locator("#sessions-root .session-item")
    sessions.nth(1).wait_for()
    assert sessions.count() == 2
    assert sessions.nth(0).locator(".pill").inner_text() == "当前"
    assert sessions.nth(0).locator("[data-action=revoke-session]").is_disabled()

    page.once("dialog", lambda dialog: dialog.accept())
    sessions.nth(1).locator("[data-action=revoke-session]").click()
    page.wait_for_timeout(300)
    assert panel.calls("POST", "/api/auth/sessions/s-phone/revoke")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("[data-action=revoke-others]").click()
    page.wait_for_timeout(300)
    assert panel.calls("POST", "/api/auth/sessions/revoke-others")

    form = page.locator("#account-form")
    form.locator("[name=current_password]").fill("wrong")
    form.locator("[name=new_password]").fill("a-new-password")
    form.locator("[name=confirm_password]").fill("a-new-password")
    form.locator("button[type=submit]").click()
    page.locator("#account-error").wait_for()
    assert "不正确" in page.locator("#account-error").inner_text()
    assert page.locator("#app").is_visible()  # 密码错误不等于会话失效

    form.locator("[name=current_password]").fill("correct-horse")
    form.locator("[name=username]").fill("admin")
    form.locator("button[type=submit]").click()
    page.locator("#account-ok").wait_for()
    body = panel.calls("POST", "/api/auth/account")[-1][3]
    assert body == {
        "current_password": "correct-horse",
        "username": "admin",
        "new_password": "a-new-password",
    }
    assert page.locator("#user-name").inner_text() == "admin"
    assert not page.errors


def _feature(plugin, name, groups, **overrides):
    return {
        "plugin": plugin,
        "name": name,
        "scope": "group",
        "protected": False,
        "loaded": True,
        "enabled": True,
        "maintenance": False,
        "global_enabled": None,
        "groups": groups,
        **overrides,
    }


def _switches_panel(group_count=4):
    groups = {str(40001 + i): i % 4 != 1 for i in range(group_count)}
    names = {
        "40001": "Alpha",
        "40002": "Alpha Two",
        "40003": "Beta",
        "40004": "Alpha Three",
    }
    panel = Panel(authenticated=True)
    panel.data["/api/features"] = {
        "features": [
            _feature("group_manager", "群管理", groups),
            _feature(
                "perm_manager", "权限管理", dict.fromkeys(groups, True), protected=True
            ),
            _feature(
                "notify",
                "启动通知",
                dict.fromkeys(groups, True),
                scope="global",
                global_enabled=True,
            ),
        ],
        "group_names": names,
    }
    return panel


def test_switches_group_view_search_and_toggle(open_panel):
    panel = _switches_panel()
    page = open_panel(panel, "switches")
    rows = page.locator(".sw-row")
    rows.first.wait_for()
    assert rows.count() == 4
    assert page.locator("#sw-count").inner_text() == "共 4 个群，匹配 4 个"

    # 群名称（不区分大小写）和群号都能搜索。
    page.locator("#sw-search").fill("ALPHA")
    assert rows.count() == 3
    assert page.locator("#sw-count").inner_text() == "共 4 个群，匹配 3 个"
    page.locator("#sw-search").fill("40003")
    assert rows.count() == 1
    page.locator("#sw-search").fill("没有这个群")
    assert rows.count() == 0
    assert page.locator(".sw-empty").is_visible()
    page.locator("#sw-search").fill("alpha")
    assert "q=alpha" in page.evaluate("location.hash")

    # 键盘：搜索框回车选中第一个结果，上下键在列表中移动。
    page.locator("#sw-search").press("ArrowDown")
    assert page.evaluate("document.activeElement.classList.contains('sw-row')")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert "id=40002" in page.evaluate("location.hash")

    row = page.locator('.sw-row[data-id="40002"]')
    assert row.get_attribute("aria-current") == "true"
    assert "Alpha Two" in page.locator("#sw-title").inner_text()
    switch = page.locator('[data-plugin="group_manager"][data-group="40002"]')
    assert switch.get_attribute("role") == "switch"
    assert switch.get_attribute("aria-checked") == "false"
    # 受保护插件只读，不渲染为可点击开关；全局功能只列在“全局功能”区。
    assert page.locator('[data-plugin="perm_manager"]').count() == 0
    assert page.locator(".sw-item .cell-chip.protected").count() == 1
    assert page.locator(".sw-global .sw-item").count() == 1
    page.once("dialog", lambda dialog: dialog.accept())
    switch.click()
    page.wait_for_timeout(300)
    request = panel.calls("POST", "/api/manage/features/group_manager")[0]
    assert request[3] == {"enabled": True, "group_id": "40002"}

    page.locator(".sw-global .sw-link").click()
    page.wait_for_selector(".sw-opts")
    assert page.evaluate("location.hash") == "#/switches?view=plugin&id=notify"
    assert page.locator('[data-action="global-feature"]').count() == 2
    assert not page.errors


def test_switches_plugin_view_bulk_and_global_state(open_panel):
    panel = _switches_panel()
    page = open_panel(panel, "switches?view=plugin&id=group_manager&gq=alpha")
    page.locator("#sw-gbody").wait_for()
    # 筛选后 3 个群：40001 开、40002 关、40004 开；只有状态不同的群会被写入。
    assert "显示 3 / 4 个群" in page.locator(".sw-bulk").inner_text()
    messages = []

    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept)
    page.get_by_role("button", name="全部关闭").click()
    page.wait_for_function(
        "document.querySelector('#operation-notice')?.textContent.includes('已关闭 2 个群')"
    )
    assert "2 个群" in messages[0] and "3 个群" in messages[0]
    posts = panel.calls("POST", "/api/manage/features/group_manager")
    assert [post[3] for post in posts] == [
        {"enabled": False, "group_id": "40001"},
        {"enabled": False, "group_id": "40004"},
    ]
    # 筛选和搜索保存在 hash 中。
    assert "gq=alpha" in page.evaluate("location.hash")

    # 全局功能：三态选项和维护开关复用既有写入接口。
    page.evaluate("location.hash = '#/switches?view=plugin&id=notify'")
    page.locator(".sw-opts").wait_for()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="关闭", exact=True).click()
    page.wait_for_timeout(300)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="跟随默认", exact=True).click()
    page.wait_for_timeout(300)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("switch", name="维护模式", exact=False).click()
    page.wait_for_timeout(300)
    bodies = [post[3] for post in panel.calls("POST", "/api/manage/features/notify")]
    assert bodies == [
        {"group_id": None, "enabled": False},
        {"group_id": None, "enabled": None},
        {"maintenance": True},
    ]
    assert not page.errors


def test_switches_hash_state_survives_poll_rerender(open_panel):
    panel = _switches_panel(group_count=60)
    panel.data["/api/features"]["group_names"] = {
        str(40001 + i): f"Room {i}" for i in range(60)
    }
    page = open_panel(panel, "switches?view=group&id=40030&q=room", clock=True)
    page.locator(".sw-row").first.wait_for()
    page.locator("#sw-list").evaluate("list => { list.scrollTop = 400; }")
    scroll = page.locator("#sw-list").evaluate("list => list.scrollTop")
    assert scroll > 0
    hash_before = page.evaluate("location.hash")

    # 服务端数据变化后，下一次轮询重绘；视图、选中项、搜索词和滚动位置保持。
    panel.data["/api/features"]["group_names"]["40030"] = "Room Renamed"
    page.locator("#sw-search").blur()
    page.locator("#page-title").click()
    page.clock.fast_forward(POLL_MS + 1000)
    page.wait_for_function(
        "document.querySelector('#sw-title')?.textContent === 'Room Renamed'"
    )
    assert page.evaluate("location.hash") == hash_before
    assert page.locator("#sw-search").input_value() == "room"
    assert (
        page.locator('.sw-row[aria-current="true"]').get_attribute("data-id") == "40030"
    )
    assert page.locator("#sw-list").evaluate("list => list.scrollTop") == scroll

    # 输入框聚焦时轮询不重建页面：焦点和已输入内容原样保留。
    page.locator("#sw-search").fill("room 3")
    before = len(panel.requests)
    page.clock.fast_forward(POLL_MS + 1000)
    page.wait_for_timeout(300)
    assert page.evaluate("document.activeElement.id") == "sw-search"
    assert page.locator("#sw-search").input_value() == "room 3"
    assert len(panel.requests) >= before
    assert not page.errors
