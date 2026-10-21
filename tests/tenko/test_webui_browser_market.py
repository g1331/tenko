"""插件市场与第三方插件页的浏览器验证；后端为路由替身，任务由替身按需完成。"""

import copy

from webui_browser_support import Panel, confirm_box as confirm

MARKET = {
    "available": True,
    "url": "https://registry.test/index.json",
    "host_api": "0.2.0",
    "plugins": [
        {
            "id": "weather",
            "name": "天气查询",
            "description": "查询城市天气并定时播报。",
            "authors": ["alice"],
            "homepage": "https://example.test/weather",
            "license": "MIT",
            "tags": ["工具"],
            "tier": "official",
            "icon_url": None,
            "source": "tenko-plugin-weather",
            "source_type": "pypi",
            "latest": "1.2.0",
            "problem": None,
            "versions": [
                {"version": "1.2.0", "requires_api": ">=0.2", "problem": None},
                {
                    "version": "1.1.0",
                    "requires_api": ">=0.2",
                    "problem": "已撤回：数据损坏",
                },
            ],
            "installed_version": None,
            "blocked": None,
        },
        {
            "id": "dice_pro",
            "name": "骰子增强",
            "description": "支持表达式的骰子。",
            "authors": [],
            "homepage": "",
            "license": "",
            "tags": ["娱乐"],
            "tier": "listed",
            "icon_url": None,
            "source": "tenko-plugin-dice-pro",
            "source_type": "pypi",
            "latest": None,
            "problem": "需要 Tenko API >=9",
            "versions": [],
            "installed_version": None,
            "blocked": None,
        },
    ],
}
INSTALLED = {
    "plugins": [
        {
            "plugin_id": "weather",
            "dist": "tenko-plugin-weather",
            "version": "1.0.0",
            "source": "tenko-plugin-weather==1.0.0",
            "trust": "official",
            "requirement": "tenko-plugin-weather==1.0.0",
            "enabled": True,
            "loaded": True,
            "restart_required": False,
            "installed_at": "2026-10-20T01:00:00Z",
            "updated_at": "2026-10-20T01:00:00Z",
            "name": "天气查询",
            "icon_url": None,
            "update": "1.2.0",
            "yanked": None,
            "blocked": None,
        }
    ],
    "drift": [],
    "rollback_available": False,
    "restart_required": False,
    "can_restart": True,
    "task": None,
}


class MarketPanel(Panel):
    """市场与任务接口的替身：创建任务后第一次查询就返回 `outcome`。"""

    def __init__(self):
        super().__init__(authenticated=True)
        self.data["/api/market"] = copy.deepcopy(MARKET)
        self.data["/api/plugins/installed"] = copy.deepcopy(INSTALLED)
        self.outcome = {
            "state": "succeeded",
            "message": "完成，重启后生效",
            "changes": [],
        }

    def task(self, **fields):
        return {
            "id": "t1",
            "action": "install",
            "plugin_id": "weather",
            "state": "running",
            "message": "",
            "changes": [],
            "restored": None,
            "started_at": 0,
            "finished_at": None,
            **fields,
        }

    def handle(self, route):
        request = route.request
        path = request.url.split("/webui", 1)[-1].split("?")[0]
        if path.startswith("/api/manage/plugin-tasks"):
            body = request.post_data_json if request.post_data else None
            self.requests.append((request.method, path, request.headers, body))
            fields = body and {"action": body["action"], "plugin_id": body["plugin_id"]}
            data = self.task(**(fields or self.outcome | {"action": self.action}))
            self.action = data["action"]
            route.fulfill(json={"ok": True, "data": data})
            return
        super().handle(route)


def open_market(open_panel, panel, tab="插件市场", **kwargs):
    page = open_panel(panel, "plugins", **kwargs)
    page.get_by_role("tab", name=tab).click()
    return page


def test_market_search_and_detail_versions(open_panel):
    panel = MarketPanel()
    page = open_market(open_panel, panel)
    cards = page.locator(".market-card")
    cards.first.wait_for()
    assert cards.count() == 2
    assert (
        "需要 Tenko API >=9"
        in page.locator(".market-card[data-plugin='dice_pro']").inner_text()
    )

    search = page.get_by_placeholder("搜索名称、标识、简介或标签")
    search.fill("娱乐")  # 按标签匹配
    assert cards.count() == 1
    search.fill("天气")
    assert cards.count() == 1

    cards.first.click()
    drawer = page.locator(".el-drawer")
    drawer.get_by_text("已撤回：数据损坏").wait_for()
    assert "可安装" in drawer.inner_text()
    assert drawer.get_by_role("radio", name="1.1.0").is_disabled()
    assert not page.errors


def test_install_success_shows_changes_without_restart_banner(open_panel):
    panel = MarketPanel()
    panel.outcome = {
        "state": "succeeded",
        "message": "已安装，启用后生效",
        "changes": [{"plugin_id": "weather", "dist": "d", "from": None, "to": "1.2.0"}],
    }
    page = open_market(open_panel, panel)
    page.locator(".market-card[data-plugin='weather']").click()
    page.get_by_role("button", name="安装 1.2.0").click()
    confirm(page, "安装插件", "安装")
    page.get_by_text("已完成：安装 weather").wait_for()
    assert "weather：未安装 → 1.2.0" in page.locator(".banners").inner_text()
    # 新装的插件默认不启用，启用时多数能直接加载：安装本身不提示重启。
    assert page.get_by_text("有插件变更需要重启后生效").count() == 0
    assert panel.calls("POST", "/api/manage/plugin-tasks")[0][3] == {
        "action": "install",
        "plugin_id": "weather",
        "version": "1.2.0",
        "confirm": True,
    }
    assert not page.errors


def test_install_failure_reports_restored_environment(open_panel):
    panel = MarketPanel()
    panel.outcome = {
        "state": "failed",
        "message": "校验失败：入口不符",
        "restored": True,
    }
    page = open_market(open_panel, panel)
    page.locator(".market-card[data-plugin='weather']").click()
    page.get_by_role("button", name="安装 1.2.0").click()
    confirm(page, "安装插件", "安装")
    banner = page.locator(".banners")
    banner.get_by_text("安装 weather失败").wait_for()
    assert "入口不符" in banner.inner_text()
    assert "环境已恢复到操作之前的状态" in banner.inner_text()
    assert not page.errors


def test_uninstall_loaded_plugin_cannot_clear_data(open_panel):
    panel = MarketPanel()
    panel.outcome = {"state": "succeeded", "message": "完成，重启后生效"}
    page = open_market(open_panel, panel, "第三方插件")
    page.get_by_text("可更新到 1.2.0").wait_for()
    page.get_by_role("button", name="卸载").click()
    dialog = page.get_by_role("dialog", name="卸载插件")
    assert dialog.get_by_label("同时清除数据").is_disabled()
    dialog.get_by_role("button", name="卸载").click()
    page.get_by_text("已完成：卸载 weather").wait_for()
    assert panel.calls("POST", "/api/manage/plugin-tasks")[0][3] == {
        "action": "uninstall",
        "plugin_id": "weather",
        "clear_data": False,
        "confirm": True,
    }
    assert not page.errors
