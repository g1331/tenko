"""WebUI 浏览器测试的公共装置：内存后端替身 + Playwright 页面工厂。

`browser`、`open_panel` 两个 fixture 在 conftest.py 中注册，测试模块只需要导入 `Panel`：

    from webui_browser_support import Panel

页面加载 `tenko/webui/static` 下构建好的前端（先在 webui/ 运行 `npm run build`），
所有 `/webui/api/*` 请求由 `Panel` 按 Cookie 会话语义应答，不连接真实后端。
"""

from __future__ import annotations

import copy
import json
import mimetypes
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

# 并行开发页面时可用 TENKO_WEBUI_STATIC 指向私有构建目录，默认读取已提交的构建。
STATIC = Path(
    os.environ.get("TENKO_WEBUI_STATIC")
    or Path(__file__).resolve().parents[2] / "tenko" / "webui" / "static"
)
POLL_MS = 30000
EXPIRES = "2026-10-27T01:00:00Z"
SESSIONS = [
    {
        "id": "s-current",
        "created_at": "2026-10-20T01:00:00Z",
        "last_seen_at": "2026-10-20T04:30:00Z",
        "expires_at": EXPIRES,
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


def _feature(plugin, name, **overrides):
    return {
        "plugin": plugin,
        "name": name,
        "scope": "group",
        "source": "builtin",
        "protected": False,
        "loaded": True,
        "enabled": True,
        "maintenance": False,
        "global_enabled": None,
        "groups": {"40001": True, "40002": False},
        **overrides,
    }


FEATURES = {
    "features": [
        _feature("group_manager", "群管理"),
        _feature("perm_manager", "权限管理", protected=True),
        _feature("welcome", "入群欢迎", maintenance=True),
        _feature("old_tool", "旧版工具", enabled=False),
        _feature("rss", "RSS 订阅", loaded=False, enabled=False),
        _feature("status", "状态查询", scope="global", global_enabled=True),
    ],
    "group_names": {"40001": "测试群一"},
}
ACCOUNTS = {
    "accounts": [
        {
            "id": "10001",
            "ref": "10001",
            "platform": "onebot",
            "online": True,
            "group_count": 9,
            "response_strategies": [
                {
                    "group_id": "40001",
                    "strategy": "random",
                    "deterministic_account": "10001",
                }
            ],
        },
        {
            "id": "10002",
            "ref": "10002",
            "platform": "onebot",
            "online": False,
            "group_count": 5,
            "response_strategies": [],
        },
    ]
}
DATA = {
    "/api/overview": {
        "version": "4.0.0-test",
        "uptime_seconds": 93784,
        "rss": 327155712,
        "resources": {
            "cpu_percent": 23.4,
            "memory_used": 5261334937,
            "memory_total": 8589934592,
            "memory_percent": 61.2,
            "disk_used": 36507222016,
            "disk_total": 85899345920,
            "disk_percent": 42.5,
            "net_sent": 1048576,
            "net_received": 2097152,
        },
        "activity": [
            [(i * 7) % 23 + (30 if i > 48 else 0), (i * 3) % 5] for i in range(60)
        ],
        "online_accounts": 1,
        "active_groups": 14,
        "messages": {
            "received_count": 12873,
            "sent_count": 951,
            "received_rate": 31,
            "sent_rate": 7,
        },
    },
    "/api/accounts": ACCOUNTS,
    "/api/features": FEATURES,
    "/api/manage/accounts": {"accounts": [], "capabilities": []},
    "/api/manage/settings": {
        "default_enabled": True,
        "stored": True,
        "friend_request_mode": "admins",
    },
}


class Panel:
    """内存中的 WebUI 后端替身：记录请求，并按 Cookie 会话语义放行或返回 401。"""

    def __init__(self, *, authenticated=False, password_set=True):
        self.authenticated = authenticated
        self.password_set = password_set
        self.requests = []  # (method, 路径, 请求头, 请求体)
        self.data = dict(DATA)
        self.login_error = None  # (状态码, error.code, 额外字段)
        self.sessions = copy.deepcopy(SESSIONS)
        self.failures = {}  # 写接口路径 -> 409 conflict 的错误消息
        self.write_results = {}  # 写接口路径 -> 成功响应的 data（缺省为 {}）

    def paths(self, prefix=""):
        return [path for _m, path, _h, _b in self.requests if path.startswith(prefix)]

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def data_requests(self):
        return [p for p in self.paths() if p != "/api/auth/state"]

    def handle(self, route):
        request = route.request
        path = request.url.split("/webui", 1)[-1].split("?")[0]
        if path in {"", "/"}:
            route.fulfill(path=STATIC / "index.html", content_type="text/html")
            return
        if path.startswith("/assets/"):
            asset = STATIC / path.lstrip("/")
            kind = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            route.fulfill(path=asset, content_type=kind)
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
                    "expires_at": EXPIRES if self.authenticated else None,
                }
            )
        elif path == "/api/auth/login":
            if self.login_error:
                fail(*self.login_error[:2], **self.login_error[2])
            elif body["password"] == "correct-horse":
                self.authenticated = True
                ok({"username": body["username"], "expires_at": EXPIRES})
            else:
                fail(401, "invalid_credentials", "bad credentials")
        elif path == "/api/auth/logout":
            self.authenticated = False
            ok({})
        elif not self.authenticated:
            fail(401, "unauthorized", "login required")
        elif path == "/api/auth/sessions":
            ok({"sessions": self.sessions})
        elif path == "/api/auth/account":
            if body["current_password"] != "correct-horse":
                fail(401, "invalid_credentials", "bad password")
            else:
                ok({"username": body.get("username", "master")})
        elif path.startswith("/api/auth/sessions/"):
            ok({"revoked": 1})
        elif request.method == "POST" and path in self.failures:
            fail(409, "conflict", self.failures[path])
        elif path.startswith("/api/manage/") and request.method == "POST":
            ok(self.write_results.get(path, {}))
        elif path == "/api/manage/logs":
            ok({"files": [], "lines": ["12:00 INFO hello"], "history_enabled": True})
        else:
            ok(self.data.get(path, {}))


def confirm_box(page, title, button="确认"):
    """等待确认框出现（标题精确匹配）并点击按钮。"""
    box = page.locator(".el-message-box")
    box.get_by_text(title, exact=True).wait_for()
    box.get_by_role("button", name=button, exact=True).click()


def toast(page, text):
    page.locator(".el-message", has_text=text).wait_for()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("浏览器验证需要 playwright install chromium")
        if not (STATIC / "index.html").exists():
            pytest.skip("缺少前端构建产物，请先在 webui/ 运行 npm run build")
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def open_panel(browser):
    pages = []

    def open_page(panel, route="overview", *, clock=False, viewport=(1280, 720)):
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
