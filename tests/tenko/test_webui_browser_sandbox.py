"""命令沙盒页：切换身份后发命令，请求带上会话和身份；后端为路由替身。"""

from webui_browser_support import Panel

URL = "/api/manage/sandbox"
STATE = {
    "ready": True,
    "bot": {"id": "1000", "name": "Tenko"},
    "group": {"id": "1100", "name": "沙盒群"},
    "identities": [],
    "messages": [],
}


def test_sending_a_command_as_master(open_panel):
    panel = Panel(authenticated=True)
    panel.data[URL] = STATE
    page = open_panel(panel, "sandbox")

    page.get_by_text("发一条命令试试").wait_for()
    page.locator(".identity-select").click()
    page.locator(".el-select-dropdown__item:visible", has_text="Master").click()
    box = page.get_by_label("消息内容")
    box.fill("/状态")
    box.press("Enter")

    page.wait_for_function(
        "() => !document.querySelector('[aria-label=消息内容]').value"
    )
    [(_m, _p, _h, body)] = panel.calls("POST", f"{URL}/send")
    assert body == {
        "chat": "group",
        "identity": "master",
        "content": [{"type": "text", "text": "/状态"}],
    }
    assert not page.errors
