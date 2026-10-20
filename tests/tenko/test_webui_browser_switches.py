"""功能开关页（#/switches）的浏览器验证：搜索、逐群 / 批量 / 全局 / 维护写入与路由 query。"""

import re

from webui_browser_support import FEATURES, POLL_MS, Panel

GROUPS = {
    "40001": "测试群一",
    "40002": "测试群二",
    "40003": "技术闲聊",
    "40004": "",
    "40005": "测试群三",
    "40006": "读书会",
}


def feature(plugin, name, groups_on, **overrides):
    return {
        "plugin": plugin,
        "name": name,
        "scope": "group",
        "protected": False,
        "loaded": True,
        "enabled": True,
        "maintenance": False,
        "global_enabled": None,
        "groups": {gid: gid in groups_on for gid in GROUPS},
        **overrides,
    }


def features_payload():
    everyone = set(GROUPS)
    return {
        "features": [
            feature("group_manager", "群管理", everyone - {"40002"}),
            feature("perm_manager", "权限管理", everyone, protected=True),
            feature("welcome", "入群欢迎", everyone, maintenance=True),
            feature("antispam", "刷屏检测", everyone, global_enabled=False),
            feature("fun_dice", "骰子与抽签", {"40001", "40002", "40003", "40004"}),
            feature("dead_end", "全关功能", set()),
            feature(
                "status", "状态查询", everyone, scope="global", global_enabled=True
            ),
        ],
        "group_names": {gid: name for gid, name in GROUPS.items() if name},
    }


class SwitchPanel(Panel):
    """写入会真实改动内存数据，使 refresh() 后的页面状态可验证。"""

    def __init__(self, fail_groups=()):
        super().__init__(authenticated=True)
        self.data["/api/features"] = features_payload()
        self.fail_groups = set(fail_groups)

    def handle(self, route):
        request = route.request
        path = request.url.split("/webui", 1)[-1].split("?")[0]
        if request.method == "POST" and path.startswith("/api/manage/features/"):
            body = request.post_data_json
            if body.get("group_id") in self.fail_groups:
                self.requests.append((request.method, path, request.headers, body))
                route.fulfill(
                    status=503,
                    json={
                        "ok": False,
                        "error": {"code": "db", "message": "数据库暂不可用"},
                    },
                )
                return
            item = next(
                f
                for f in self.data["/api/features"]["features"]
                if f["plugin"] == path.rsplit("/", 1)[-1]
            )
            if "maintenance" in body:
                item["maintenance"] = body["maintenance"]
            elif body["group_id"] is None:
                item["global_enabled"] = body["enabled"]
            else:
                item["groups"][body["group_id"]] = body["enabled"]
        super().handle(route)

    def feature_posts(self, plugin):
        return [r[3] for r in self.calls("POST", f"/api/manage/features/{plugin}")]


def open_switches(open_panel, panel, query="", **options):
    page = open_panel(panel, f"switches{query}", **options)
    page.set_default_timeout(5000)  # 失败时尽快暴露，而不是等满 30 秒
    page.locator(".sw-layout").wait_for()
    return page


def rows(page):
    return page.locator(".sw-row")


def radio(page, text):
    """Element Plus 的单选按钮 / 分段控件的 input 是隐藏的，点击可见的外壳。"""
    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$")
    return page.locator(".el-radio-button, .el-segmented__item").filter(
        has_text=pattern
    )


def switch(page, label):
    """el-switch 的 input 隐藏在外壳里：返回可点击的外壳，状态读 input 的 aria-checked。"""
    return page.locator(".el-switch").filter(has=page.get_by_role("switch", name=label))


def checked(page, label):
    return page.get_by_role("switch", name=label).get_attribute("aria-checked")


def confirm(page):
    page.locator(".el-message-box__btns .el-button--primary").click()


def test_shell_default_dataset_still_loads_without_switch_data(open_panel):
    # 沿用公共替身的默认数据：群只来自 features.groups，页面不应报错。
    panel = Panel(authenticated=True)
    page = open_switches(open_panel, panel)
    assert rows(page).count() == 2
    assert FEATURES["features"][0]["name"] in page.locator("#sw-detail").inner_text()
    assert not page.errors


def test_group_search_filters_by_name_and_id(open_panel):
    page = open_switches(open_panel, SwitchPanel())
    search = page.locator(".sw-search input")
    assert rows(page).count() == 6
    search.fill("测试")
    assert rows(page).count() == 3
    assert "匹配 3 个" in page.locator(".sw-meta").inner_text()
    search.fill("40006")
    assert rows(page).count() == 1
    assert "读书会" in rows(page).first.inner_text()
    # 连续快速输入不会被路由异步回写吃字
    search.fill("")
    search.press_sequentially("4000", delay=0)
    assert search.input_value() == "4000"
    assert rows(page).count() == 6
    page.wait_for_function("location.hash.includes('q=4000')")
    assert search.input_value() == "4000"
    search.fill("没有这个群")
    assert rows(page).count() == 0
    assert "没有匹配的群" in page.locator(".sw-empty").inner_text()
    assert not page.errors


def test_group_list_marks_only_all_off_groups(open_panel):
    # 可调功能：group_manager / fun_dice / dead_end；40002 有 fun_dice 开，没有全关的群。
    panel = SwitchPanel()
    for item in panel.data["/api/features"]["features"]:
        if item["plugin"] in {"group_manager", "fun_dice"}:
            item["groups"]["40006"] = False
    page = open_switches(open_panel, panel)
    assert rows(page).filter(has_text="全部关闭").count() == 1
    assert "40006" in rows(page).filter(has_text="全部关闭").inner_text()
    assert "已开 2 / 3" in rows(page).filter(has_text="40001").inner_text()
    assert page.locator(".sw-dot").count() == 0


def test_group_detail_explains_each_state(open_panel):
    page = open_switches(open_panel, SwitchPanel())
    detail = page.locator("#sw-detail")
    text = detail.inner_text()
    assert "受保护" in text
    assert "插件维护中，群开关暂不可改" in text
    assert "已被全局策略覆盖" in text
    assert "全局功能（对所有群生效）" in text
    # 维护中的开关是禁用的，且没有可写请求
    assert switch(page, "入群欢迎 / 测试群一").locator("input").is_disabled()
    link = detail.get_by_role("link", name="在按插件查看中修改")
    link.click()
    assert "view=plugin" in page.url and "id=status" in page.url
    assert page.locator(".sw-head h2").inner_text() == "状态查询"


def test_selecting_group_and_toggling_posts_body_and_refreshes(open_panel):
    panel = SwitchPanel()
    page = open_switches(open_panel, panel)
    rows(page).filter(has_text="读书会").click()
    assert "id=40006" in page.url
    assert checked(page, "群管理 / 读书会") == "true"
    switch(page, "群管理 / 读书会").click()
    page.get_by_role("switch", name="群管理 / 读书会", checked=False).wait_for(
        state="attached"
    )
    assert panel.feature_posts("group_manager") == [
        {"enabled": False, "group_id": "40006"}
    ]
    assert panel.paths("/api/features")[1:]  # 写入后重新读取了真实状态
    assert "已关闭 群管理 / 读书会" in page.locator(".el-message").inner_text()
    assert not page.errors


def test_failed_write_shows_error_and_keeps_state(open_panel):
    panel = SwitchPanel(fail_groups={"40001"})
    page = open_switches(open_panel, panel)
    switch(page, "群管理 / 测试群一").click()
    page.locator(".el-message--error").wait_for()
    assert "数据库暂不可用" in page.locator(".el-message--error").inner_text()
    assert checked(page, "群管理 / 测试群一") == "true"


def test_plugin_view_bulk_close_posts_once_per_filtered_group(open_panel):
    panel = SwitchPanel()
    page = open_switches(open_panel, panel, "?view=plugin&id=fun_dice&f=on&gq=测试")
    # fun_dice 开启的群：40001/40002/40003/40004；名称含“测试”的：40001、40002
    bulk = page.get_by_role("button", name="全部关闭")
    assert "（2）" in bulk.inner_text()
    assert page.get_by_role("button", name="全部开启").is_disabled()
    bulk.click()
    dialog = page.locator(".el-message-box")
    assert "将在 2 个群中关闭 骰子与抽签" in dialog.inner_text()
    assert "当前筛选共 2 个群" in dialog.inner_text()
    # 取消不发请求
    page.locator(".el-message-box__btns .el-button:not(.el-button--primary)").click()
    page.wait_for_timeout(200)
    assert panel.feature_posts("fun_dice") == []
    bulk.click()
    confirm(page)
    page.locator(".el-message--success").wait_for()
    assert panel.feature_posts("fun_dice") == [
        {"group_id": "40001", "enabled": False},
        {"group_id": "40002", "enabled": False},
    ]
    assert (
        "已关闭 2 个群的 骰子与抽签"
        in page.locator(".el-message--success").inner_text()
    )
    assert panel.data["/api/features"]["features"][4]["groups"]["40003"] is True
    radio(page, "已开启 2").wait_for()
    assert not page.errors


def test_bulk_failure_summary_lists_failed_groups(open_panel):
    panel = SwitchPanel(fail_groups={"40002"})
    page = open_switches(open_panel, panel, "?view=plugin&id=fun_dice&f=on")
    page.get_by_role("button", name="全部关闭").click()
    confirm(page)
    warning = page.locator(".el-message--warning")
    warning.wait_for()
    text = warning.inner_text()
    assert "已完成 3/4，1 个失败" in text and "测试群二：数据库暂不可用" in text
    # 失败不中断：其余群仍被逐个请求
    assert [b["group_id"] for b in panel.feature_posts("fun_dice")] == [
        "40001",
        "40002",
        "40003",
        "40004",
    ]


def test_global_tristate_and_maintenance_post_correct_bodies(open_panel):
    panel = SwitchPanel()
    page = open_switches(open_panel, panel, "?view=plugin&id=status")
    radio(page, "关闭").click()
    page.locator(".el-message-box").wait_for()
    assert "状态查询：全局关闭？" in page.locator(".el-message-box").inner_text()
    confirm(page)
    page.locator(".el-radio-button.is-active").filter(has_text="关闭").wait_for()
    radio(page, "跟随默认").click()
    confirm(page)
    page.locator(".el-message-box").wait_for(state="detached")
    switch(page, "状态查询 维护模式").click()
    confirm(page)
    page.get_by_role("switch", name="状态查询 维护模式", checked=True).wait_for(
        state="attached"
    )
    assert panel.feature_posts("status") == [
        {"group_id": None, "enabled": False},
        {"group_id": None, "enabled": None},
        {"maintenance": True},
    ]
    assert not page.errors


def test_group_scope_global_override_can_be_cleared(open_panel):
    panel = SwitchPanel()
    page = open_switches(open_panel, panel, "?view=plugin&id=antispam")
    assert "逐群开关已被全局策略覆盖" in page.locator("#sw-detail").inner_text()
    page.get_by_role("button", name="恢复群级开关").click()
    confirm(page)
    page.get_by_text("各群开关").wait_for()
    assert panel.feature_posts("antispam") == [{"group_id": None, "enabled": None}]


def test_route_query_survives_poll_and_reload(open_panel):
    panel = SwitchPanel()
    page = open_switches(open_panel, panel, clock=True)
    radio(page, "按插件查看").click()
    rows(page).filter(has_text="fun_dice").click()
    page.get_by_placeholder("搜索群名称或群号").fill("测试")
    radio(page, "已开启 4").click()
    expected = "view=plugin&id=fun_dice&gq=%E6%B5%8B%E8%AF%95&f=on"
    page.wait_for_function(f"location.hash.includes('{expected}')")
    before = len(panel.paths("/api/features"))
    page.clock.fast_forward(POLL_MS + 500)
    page.wait_for_timeout(300)
    assert len(panel.paths("/api/features")) > before  # 轮询确实重新读取并重绘了
    assert expected in page.url
    assert page.locator(".sw-head h2").inner_text() == "骰子与抽签"
    assert page.get_by_placeholder("搜索群名称或群号").input_value() == "测试"
    assert "显示 2 / 6 个群" in page.locator(".sw-bulk").inner_text()
    page.reload()
    page.locator(".sw-layout").wait_for()
    assert page.locator(".sw-head h2").inner_text() == "骰子与抽签"
    assert "显示 2 / 6 个群" in page.locator(".sw-bulk").inner_text()
    assert not page.errors


def test_keyboard_navigation_and_mobile_layout(open_panel):
    page = open_switches(open_panel, SwitchPanel(), viewport=(390, 844))
    search = page.locator(".sw-search input")
    search.focus()
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.classList.contains('sw-row')")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert "id=40002" in page.url
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")
    assert page.evaluate("document.activeElement.closest('.sw-search') !== null")
    # 窄屏：列表在详情上方，页面无横向溢出
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    list_box = page.locator(".sw-side").bounding_box()
    detail_box = page.locator("#sw-detail").bounding_box()
    assert list_box["y"] < detail_box["y"]
    radio(page, "按插件查看").click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_deep_link_unknown_id_falls_back_to_first_item(open_panel):
    page = open_switches(open_panel, SwitchPanel(), "?view=group&id=99999")
    assert page.locator(".sw-head h2").inner_text() == "测试群一"
    assert not page.errors
