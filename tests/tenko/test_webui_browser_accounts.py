"""账户页（统计、配对、托管账户、偏好对话框、群响应策略）的浏览器验证；后端为路由替身。"""

import json

from webui_browser_support import Panel, confirm_box as confirm, toast

CAPS = ["send_group_message", "member_mute", "group_leave"]


def managed(account_id, **overrides):
    return {
        "platform": "onebot",
        "id": account_id,
        "alias": "",
        "enabled": True,
        "capabilities": {},
        "online": True,
        "available": True,
        "group_count": 2,
        "groups": ["40001", "40002"],
        "connected": True,
        "name": f"bot-{account_id}",
        "manageable": True,
        **overrides,
    }


def summary(account_id, *, online=True, groups=0):
    return {
        "id": account_id,
        "ref": account_id,
        "platform": "onebot",
        "online": online,
        "group_count": groups,
    }


def member(account_id, state="available"):
    return {
        "ref": account_id,
        "id": account_id,
        "platform": "onebot",
        "name": f"bot-{account_id}",
        "state": state,
        "role": None,
        "muted_until": None,
    }


def group(group_id, kind, target, members, silent=None):
    return {
        "group_id": group_id,
        "strategy": kind,
        "deterministic_account": target,
        "silent_reason": silent,
        "accounts": members,
    }


class AccountsPanel(Panel):
    """在通用替身上补充账户数据集与配对接口。"""

    def __init__(self):
        super().__init__(authenticated=True)
        both = [member("10001"), member("10003", "disabled")]
        self.data["/api/accounts"] = {
            "accounts": [
                summary("10001", groups=3),
                summary("10002", online=False),
                summary("10003", online=False, groups=2),
            ],
            "groups": [
                group("40001", "random", "10001", [member("10001")]),
                group("40002", "deterministic", "10001", both),
                # 定向给了已停用的账号：这个群不会响应。
                group("40003", "deterministic", "10003", both, "target_unavailable"),
            ],
        }
        self.data["/api/features"] = {
            "features": [],
            "group_names": {"40001": "测试群一"},
        }
        self.data["/api/manage/accounts"] = {
            "accounts": [
                managed("10001", alias="天子", capabilities={"member_mute": True}),
                managed(
                    "10002", online=False, connected=False, group_count=0, groups=[]
                ),
                managed("10003", enabled=False, online=False, connected=False),
                managed("10004", platform="satori", manageable=False, online=False),
            ],
            "capabilities": CAPS,
        }
        self.data["/api/manage/pairing"] = {
            "url": "ws://0.0.0.0:8081/onebot/v11/ws",
            "path": "/onebot/v11/ws",
            "requires_host": True,
            "token_configured": True,
            "can_rotate": True,
        }

    def handle(self, route):
        request = route.request
        path = request.url.split("/webui", 1)[-1].split("?")[0]
        if request.method == "POST" and path == "/api/manage/pairing":
            body = json.loads(request.post_data)
            self.requests.append((request.method, path, request.headers, body))
            data = {**self.data[path], "access_token": "tok-secret"}
            route.fulfill(json={"ok": True, "data": data})
        elif (
            request.method == "POST"
            and path.endswith("/response")
            and path not in self.failures
        ):
            body = json.loads(request.post_data)
            group_id = path.split("/")[-2]
            for item in self.data["/api/accounts"]["groups"]:
                if item["group_id"] == group_id:
                    item["strategy"] = body["strategy"]
                    item["deterministic_account"] = (
                        body["account"] or item["deterministic_account"]
                    )
            self.requests.append((request.method, path, request.headers, body))
            route.fulfill(json={"ok": True, "data": {}})
        else:
            super().handle(route)


def open_accounts(open_panel, panel, **kwargs):
    page = open_panel(panel, "accounts", **kwargs)
    page.locator(".account-table .el-table__row").first.wait_for()
    return page


def test_accounts_render_stats_table_and_strategies(open_panel):
    panel = AccountsPanel()
    page = open_accounts(open_panel, panel)
    main = page.locator("main").inner_text()
    assert page.locator(".kpi .num").all_inner_texts() == ["4", "1 / 4", "1", "2"]
    rows = page.locator(".account-table .el-table__row")
    assert rows.count() == 4
    first = rows.nth(0).inner_text()
    assert "天子" in first and "10001" in first and "在线" in first
    # 定向只算该账号被指定负责的群：40002 归 10001，40003 归已停用的 10003。
    assert "随机 1 群" in first and "定向负责 1 群" in first
    assert "未绑定群" in rows.nth(1).inner_text() and "离线" in rows.nth(1).inner_text()
    assert "定向负责 1 群" in rows.nth(2).inner_text()
    assert "已停用" in rows.nth(2).inner_text()
    assert "适配器暂不支持管理" in rows.nth(3).inner_text()
    # 离线账号不能踢下线；在线账号不能移除记录。
    assert rows.nth(0).get_by_role("button", name="移除记录").is_disabled()
    assert rows.nth(1).get_by_role("button", name="踢下线").is_disabled()
    assert rows.nth(2).get_by_role("button", name="启用").is_enabled()
    # 群响应策略：不会响应的群排在最前并写明原因；群名优先，缺名时显示群号。
    strategy = page.locator(".strategy-table .el-table__row")
    assert strategy.count() == 3
    silent = strategy.nth(0).inner_text()
    assert "40003" in silent and "定向账号 bot-10003 已停用" in silent
    assert "测试群一" in strategy.nth(1).inner_text()
    assert "共 3 个群" in main and "1 个群不会响应" in main
    page.get_by_role("button", name="1 个群不会响应").click()
    assert strategy.count() == 1
    page.get_by_role("button", name="1 个群不会响应").click()
    page.get_by_placeholder("搜索群名 / 群号 / 账号").fill("测试群一")
    assert strategy.count() == 1
    assert not page.errors


def test_group_strategy_edit_posts_body_and_failure_keeps_value(open_panel):
    panel = AccountsPanel()
    page = open_accounts(open_panel, panel)
    row = page.locator(".strategy-table .el-table__row", has_text="40002")
    # 40002 绑定了两个账号：定向下出现账号选择；单账号的 40001 没有。
    assert row.get_by_label("40002 定向账号").count() == 1
    assert (
        page.locator(".strategy-table .el-table__row", has_text="测试群一")
        .get_by_label("定向账号")
        .count()
        == 0
    )
    row.locator(".el-select").click()
    page.locator(".el-select-dropdown__item:visible", has_text="10003").click()
    toast(page, "已设为定向响应")
    assert panel.calls("POST", "/api/manage/groups/40002/response")[0][3] == {
        "strategy": "deterministic",
        "account": "10003",
    }

    # 失败时提示服务端消息，并保持原策略。
    panel.failures["/api/manage/groups/40002/response"] = "账号未绑定"
    row.get_by_text("随机", exact=True).click()
    toast(page, "修改失败：账号未绑定")
    assert row.locator(".el-segmented__item.is-selected").inner_text() == "定向"
    assert not page.errors


def test_accounts_confirmed_actions_post_confirm_body(open_panel):
    panel = AccountsPanel()
    page = open_accounts(open_panel, panel)
    rows = page.locator(".account-table .el-table__row")

    rows.nth(0).get_by_role("button", name="停用").click()
    confirm(page, "停用账户")
    toast(page, "账户已停用")
    call = panel.calls("POST", "/api/manage/accounts/onebot/10001/disable")[0]
    assert call[3] == {"confirm": True}

    rows.nth(0).get_by_role("button", name="踢下线").click()
    confirm(page, "踢下线")
    toast(page, "已断开账户连接")
    assert panel.calls("POST", "/api/manage/accounts/onebot/10001/kick")[0][3] == {
        "confirm": True
    }

    rows.nth(2).get_by_role("button", name="启用").click()
    confirm(page, "启用账户")
    toast(page, "账户已启用")
    assert panel.calls("POST", "/api/manage/accounts/onebot/10003/enable")

    rows.nth(1).get_by_role("button", name="移除记录").click()
    confirm(page, "移除记录")
    toast(page, "账户记录已移除")
    assert panel.calls("POST", "/api/manage/accounts/onebot/10002/forget")
    # 写入成功后重新读取共享数据。
    assert len(panel.paths("/api/manage/accounts")) >= 5
    assert not page.errors


def test_accounts_cancel_does_not_post_and_failure_toasts(open_panel):
    panel = AccountsPanel()
    panel.failures["/api/manage/accounts/onebot/10002/forget"] = "账户仍连接中"
    page = open_accounts(open_panel, panel)
    row = page.locator(".account-table .el-table__row").nth(1)
    row.get_by_role("button", name="移除记录").click()
    page.locator(".el-message-box").get_by_role("button", name="取消").click()
    page.wait_for_timeout(300)
    assert not panel.paths("/api/manage/accounts/onebot")

    row.get_by_role("button", name="移除记录").click()
    confirm(page, "移除记录")
    toast(page, "移除记录失败：账户仍连接中")
    assert len(panel.paths("/api/manage/accounts/onebot")) == 1
    assert not page.errors


def test_account_preferences_dialog_saves_alias_and_capabilities(open_panel):
    panel = AccountsPanel()
    page = open_accounts(open_panel, panel)
    page.locator(".account-table .el-table__row").first.get_by_role(
        "button", name="偏好"
    ).click()
    dialog = page.locator(".el-dialog")
    dialog.get_by_text("账户偏好").first.wait_for()
    alias = dialog.get_by_label("别名")
    assert alias.input_value() == "天子"
    alias.fill("新别名")
    # member_mute 初始为“允许”，其余为自动探测。
    assert "允许" in dialog.locator(".el-form-item", has_text="成员禁言").inner_text()
    dialog.locator(".el-form-item", has_text="群消息").locator(".el-select").click()
    page.locator(".el-select-dropdown__item:visible", has_text="禁用").click()
    dialog.get_by_role("button", name="保存偏好").click()
    toast(page, "账户偏好已保存")
    call = panel.calls("POST", "/api/manage/accounts/onebot/10001/preferences")[0]
    assert call[3] == {
        "alias": "新别名",
        "capabilities": {"member_mute": True, "send_group_message": False},
    }
    dialog.wait_for(state="hidden")
    assert not page.errors


def test_account_preferences_failure_keeps_dialog_open(open_panel):
    panel = AccountsPanel()
    panel.failures["/api/manage/accounts/onebot/10001/preferences"] = "别名过长"
    page = open_accounts(open_panel, panel)
    page.locator(".account-table .el-table__row").first.get_by_role(
        "button", name="偏好"
    ).click()
    page.locator(".el-dialog").get_by_role("button", name="保存偏好").click()
    toast(page, "保存偏好失败：别名过长")
    assert page.locator(".el-dialog").is_visible()
    assert not page.errors


def test_pairing_reveal_copy_and_rotate(open_panel):
    panel = AccountsPanel()
    page = open_accounts(open_panel, panel)
    page.get_by_text("添加账户 · 配对接入").click()
    url = page.get_by_label("连接地址")
    url.wait_for()
    assert url.input_value() == ""  # 监听所有地址时必须由用户填写可达地址
    assert "不要填写 0.0.0.0" in page.locator(".pairing").inner_text()
    assert "当前没有配置连接令牌" not in page.locator(".pairing").inner_text()
    assert panel.calls("POST", "/api/manage/pairing") == []

    page.get_by_role("button", name="显示连接凭据").click()
    token = page.get_by_label("OneBot Token")
    token.wait_for()
    assert token.input_value() == "tok-secret"
    assert token.get_attribute("type") == "password"
    assert panel.calls("POST", "/api/manage/pairing")[0][3] == {"action": "reveal"}

    page.evaluate(
        "window.copied = null; Object.defineProperty(navigator, 'clipboard',"
        " { value: { writeText: async (text) => { window.copied = text } } })"
    )
    url.fill("ws://0.0.0.0:8081/x")
    page.get_by_role("button", name="复制连接字段").click()
    toast(page, "请填写有效且可达的")
    assert page.evaluate("window.copied") is None
    url.fill("ws://bot.example:8081/onebot/v11/ws")
    page.get_by_role("button", name="复制连接字段").click()
    toast(page, "连接字段已复制")
    copied = page.evaluate("JSON.parse(window.copied)")
    assert copied == {
        "url": "ws://bot.example:8081/onebot/v11/ws",
        "token": "tok-secret",
    }

    page.get_by_role("button", name="隐藏凭据").click()
    token.wait_for(state="detached")

    page.get_by_role("button", name="轮换连接令牌").click()
    page.locator(".el-message-box").get_by_role("button", name="取消").click()
    assert len(panel.calls("POST", "/api/manage/pairing")) == 1
    page.get_by_role("button", name="轮换连接令牌").click()
    confirm(page, "轮换连接令牌")
    toast(page, "新令牌已保存")
    assert panel.calls("POST", "/api/manage/pairing")[1][3] == {
        "action": "rotate",
        "confirm": True,
    }
    assert page.get_by_label("OneBot Token").input_value() == "tok-secret"
    assert not page.errors


def test_pairing_without_token_shows_inline_hint_not_toast(open_panel):
    panel = AccountsPanel()
    panel.data["/api/manage/pairing"]["token_configured"] = False
    page = open_accounts(open_panel, panel)
    page.get_by_text("添加账户 · 配对接入").click()
    page.locator(".token-hint").wait_for()
    assert "当前没有配置连接令牌" in page.locator(".token-hint").inner_text()
    assert page.locator(".el-message").count() == 0  # 加载时不弹 toast


def test_pairing_rotate_disabled_when_config_not_writable(open_panel):
    panel = AccountsPanel()
    panel.data["/api/manage/pairing"]["can_rotate"] = False
    page = open_accounts(open_panel, panel)
    page.get_by_text("添加账户 · 配对接入").click()
    assert page.get_by_role("button", name="轮换连接令牌").is_disabled()


def test_accounts_mobile_uses_cards_without_overflow(open_panel):
    panel = AccountsPanel()
    page = open_panel(panel, "accounts", viewport=(390, 844))
    cards = page.locator(".cards .card")
    cards.first.wait_for()
    assert cards.count() == 4
    assert page.locator(".account-table").count() == 0
    cards.nth(0).get_by_role("button", name="停用").click()
    confirm(page, "停用账户")
    toast(page, "账户已停用")
    assert panel.calls("POST", "/api/manage/accounts/onebot/10001/disable")
    page.get_by_text("添加账户 · 配对接入").click()
    page.get_by_label("连接地址").wait_for()
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
    assert not page.errors
