"""插件页（卡片状态、搜索、插件级操作、插件市场占位）的浏览器验证；后端为路由替身。"""

import copy

from webui_browser_support import (
    POLL_MS,
    Panel,
    _feature,
    confirm_box as confirm,
    toast,
)


class PluginsPanel(Panel):
    """通用替身 + 一份更完整的插件数据集。"""

    def __init__(self):
        super().__init__(authenticated=True)
        self.failures = {}
        self.data["/api/features"] = {
            "features": [
                _feature("group_manager", "群管理"),
                _feature("perm_manager", "权限管理", protected=True),
                _feature("welcome", "入群欢迎", maintenance=True),
                _feature("old_tool", "旧版工具", enabled=False),
                _feature("rss", "RSS 订阅", loaded=False, enabled=False),
                _feature("status", "状态查询", scope="global", global_enabled=True),
                _feature("antispam", "刷屏检测", global_enabled=False),
                _feature("dice", "骰子", global_enabled=True),
                _feature(
                    "ext_demo",
                    "演示插件",
                    source="external",
                    loaded=False,
                    enabled=False,
                ),
            ],
            "group_names": {},
        }


class HeldFeaturesPanel(PluginsPanel):
    """可扣住 GET /api/features 的响应（内容取扣住那一刻的快照），并让插件开关真实生效。"""

    def __init__(self):
        super().__init__()
        self.hold = False
        self.held = []  # (route, 快照)

    def handle(self, route):
        request = route.request
        path = request.url.split("/webui", 1)[-1].split("?")[0]
        if request.method == "POST" and path.startswith("/api/manage/plugins/"):
            plugin = path.rsplit("/", 1)[-1]
            for item in self.data["/api/features"]["features"]:
                if item["plugin"] == plugin:
                    item["enabled"] = request.post_data_json["enabled"]
        if self.hold and request.method == "GET" and path == "/api/features":
            self.requests.append((request.method, path, request.headers, None))
            self.held.append((route, copy.deepcopy(self.data[path])))
            return
        super().handle(route)


def card(page, plugin):
    return page.locator(f".plugin-card[data-plugin='{plugin}']")


def open_plugins(open_panel, panel, **kwargs):
    page = open_panel(panel, "plugins", **kwargs)
    page.locator(".plugin-card").first.wait_for()
    return page


def test_plugins_render_status_scope_and_locked_actions(open_panel):
    panel = PluginsPanel()
    page = open_plugins(open_panel, panel)
    assert page.locator(".plugin-card").count() == 9
    assert "共 9 个插件 · 5 个运行中" in page.locator("main").inner_text()
    assert "运行中" in card(page, "group_manager").inner_text()
    assert "群级功能 · 1/2 个群启用" in card(page, "group_manager").inner_text()
    protected = card(page, "perm_manager")
    assert "受保护" in protected.inner_text()
    assert protected.get_by_role("button", name="停用插件").is_disabled()
    assert protected.get_by_role("button", name="进入维护").is_disabled()
    assert "维护中" in card(page, "welcome").inner_text()
    assert card(page, "welcome").get_by_role("button", name="解除维护").is_enabled()
    assert "已停用" in card(page, "old_tool").inner_text()
    assert card(page, "old_tool").get_by_role("button", name="启用插件").is_enabled()
    unloaded = card(page, "rss")
    assert "未加载" in unloaded.inner_text()
    assert unloaded.get_by_role("button", name="启用插件").is_disabled()
    assert "第三方" not in card(page, "rss").inner_text()
    glob = card(page, "status")
    assert "全局开启" in glob.inner_text() and "范围 全局" in glob.inner_text()
    assert glob.get_by_role("button", name="关闭全局功能").is_enabled()
    assert (
        card(page, "antispam").get_by_role("button", name="恢复群级开关").count() == 1
    )
    assert not page.errors


def test_plugins_search_filters_by_name_and_key(open_panel):
    panel = PluginsPanel()
    page = open_plugins(open_panel, panel)
    search = page.get_by_placeholder("搜索插件名称或标识")
    search.fill("入群")
    assert page.locator(".plugin-card").count() == 1
    search.fill("RSS")
    assert page.locator(".plugin-card").count() == 1
    search.fill("antispam")
    assert card(page, "antispam").count() == 1
    search.fill("不存在")
    page.get_by_text("没有匹配的插件").wait_for()
    assert page.locator(".plugin-card").count() == 0


def test_plugin_actions_post_expected_bodies_after_confirm(open_panel):
    panel = PluginsPanel()
    page = open_plugins(open_panel, panel)

    card(page, "group_manager").get_by_role("button", name="停用插件").click()
    confirm(page, "停用插件")
    toast(page, "插件已停用")
    call = panel.calls("POST", "/api/manage/plugins/group_manager")[0]
    assert call[3] == {"enabled": False, "confirm": True}

    card(page, "old_tool").get_by_role("button", name="启用插件").click()
    confirm(page, "启用插件")
    toast(page, "插件已启用")
    assert panel.calls("POST", "/api/manage/plugins/old_tool")[0][3] == {
        "enabled": True,
        "confirm": True,
    }

    card(page, "group_manager").get_by_role("button", name="进入维护").click()
    confirm(page, "进入维护")
    toast(page, "已进入维护")
    assert panel.calls("POST", "/api/manage/features/group_manager")[0][3] == {
        "maintenance": True
    }

    card(page, "welcome").get_by_role("button", name="解除维护").click()
    confirm(page, "解除维护")
    toast(page, "已解除维护")
    assert panel.calls("POST", "/api/manage/features/welcome")[0][3] == {
        "maintenance": False
    }

    card(page, "status").get_by_role("button", name="关闭全局功能").click()
    confirm(page, "关闭全局功能")
    toast(page, "全局功能已关闭")
    assert panel.calls("POST", "/api/manage/features/status")[0][3] == {
        "group_id": None,
        "enabled": False,
    }

    card(page, "antispam").get_by_role("button", name="恢复群级开关").click()
    confirm(page, "恢复群级开关")
    toast(page, "已恢复群级开关")
    assert panel.calls("POST", "/api/manage/features/antispam")[0][3] == {
        "group_id": None,
        "enabled": None,
    }
    # 每次写入后重新读取共享的功能数据。
    assert len(panel.paths("/api/features")) >= 7
    assert not page.errors


def test_plugin_global_toggle_turns_on_when_globally_off(open_panel):
    panel = PluginsPanel()
    panel.data["/api/features"]["features"][5]["global_enabled"] = False
    page = open_plugins(open_panel, panel)
    card(page, "status").get_by_role("button", name="开启全局功能").click()
    confirm(page, "开启全局功能")
    toast(page, "全局功能已开启")
    assert panel.calls("POST", "/api/manage/features/status")[0][3] == {
        "group_id": None,
        "enabled": True,
    }


def test_plugin_cancel_does_not_post_and_failure_toasts(open_panel):
    panel = PluginsPanel()
    panel.failures["/api/manage/plugins/group_manager"] = "插件当前未加载"
    page = open_plugins(open_panel, panel)
    button = card(page, "group_manager").get_by_role("button", name="停用插件")
    button.click()
    page.locator(".el-message-box").get_by_role("button", name="取消").click()
    page.wait_for_timeout(300)
    assert not panel.paths("/api/manage/plugins")

    button.click()
    confirm(page, "停用插件")
    toast(page, "停用插件失败：插件当前未加载")
    assert len(panel.paths("/api/manage/plugins")) == 1
    assert button.is_enabled()
    assert not page.errors


def test_plugin_market_placeholder_dialog(open_panel):
    page = open_plugins(open_panel, PluginsPanel())
    page.get_by_role("button", name="插件市场").click()
    dialog = page.locator(".el-dialog")
    dialog.get_by_text("插件市场筹备中").wait_for()
    assert "仍在设计" in dialog.inner_text()
    dialog.get_by_role("button", name="Close").click()
    dialog.wait_for(state="hidden")


def test_plugins_empty_state(open_panel):
    panel = PluginsPanel()
    panel.data["/api/features"] = {"features": [], "group_names": {}}
    page = open_panel(panel, "plugins")
    page.get_by_text("暂无插件", exact=True).wait_for()
    assert page.locator(".plugin-card").count() == 0


def test_plugins_mobile_no_horizontal_overflow(open_panel):
    panel = PluginsPanel()
    panel.data["/api/features"]["features"].append(
        _feature(
            "a_very_long_plugin_name_for_overflow_check_in_narrow_viewports",
            "一个名字特别特别长的插件用于检查窄屏换行",
        )
    )
    page = open_plugins(open_panel, panel, viewport=(390, 844))
    assert page.locator(".plugin-card").count() == 10
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
    card(page, "group_manager").get_by_role("button", name="进入维护").click()
    confirm(page, "进入维护")
    toast(page, "已进入维护")
    assert not page.errors


def test_refresh_after_write_is_not_satisfied_by_older_inflight_request(open_panel):
    panel = HeldFeaturesPanel()
    page = open_plugins(open_panel, panel, clock=True)
    page.set_default_timeout(5000)

    # 定时刷新的 features 请求先发出并被扣住，响应内容是写入之前的状态。
    panel.hold = True
    page.clock.fast_forward(POLL_MS + 500)
    while not panel.held:
        page.wait_for_timeout(20)

    card(page, "group_manager").get_by_role("button", name="停用插件").click()
    confirm(page, "停用插件")
    toast(page, "插件已停用")
    panel.hold = False
    route, stale = panel.held.pop()
    assert stale["features"][0]["enabled"] is True
    route.fulfill(json={"ok": True, "data": stale})

    # 写入后的刷新必须在旧请求之后再取一次，页面最终显示写入后的状态。
    card(page, "group_manager").get_by_role("button", name="启用插件").wait_for()
    assert len(panel.calls("GET", "/api/features")) == 3
    assert not page.errors


def test_external_plugin_is_tagged_and_enable_reports_restart(open_panel):
    panel = PluginsPanel()
    panel.write_results["/api/manage/plugins/ext_demo"] = {
        "plugin": "ext_demo",
        "enabled": True,
        "restart_required": True,
    }
    page = open_plugins(open_panel, panel)
    external = card(page, "ext_demo")
    assert "第三方" in external.inner_text()
    assert "未启用" in external.inner_text()
    # 第三方插件未加载时也能启用；维护开关仍需先加载。
    assert external.get_by_role("button", name="进入维护").is_disabled()
    external.get_by_role("button", name="启用插件").click()
    confirm(page, "启用插件")
    toast(page, "已启用，重启后生效")
    assert panel.calls("POST", "/api/manage/plugins/ext_demo")[0][3] == {
        "enabled": True,
        "confirm": True,
    }
    assert not page.errors
