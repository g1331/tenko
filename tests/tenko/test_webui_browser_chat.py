"""聊天页：打开会话、回复一条消息并用 Enter 发送；后端为路由替身。"""

from webui_browser_support import ACCOUNTS, Panel

BASE = "/api/manage/chat"
CONVERSATIONS = {
    "enabled": True,
    "retention_days": 7,
    "online": True,
    "conversations": [
        {
            "channel_id": "40001",
            "chat_type": "group",
            "peer_id": "40001",
            "name": "测试群一",
            "last": {
                "preview": "早",
                "user_name": "群友甲",
                "outgoing": False,
                "created_at": "2026-10-20T04:00:00Z",
            },
            "muted": False,
        }
    ],
}
MESSAGE = {
    "id": 1,
    "message_id": "501",
    "outgoing": False,
    "user_id": "20001",
    "user_name": "群友甲",
    "content": [{"type": "text", "text": "早"}],
    "created_at": "2026-10-20T04:00:00Z",
}


def test_replying_to_a_message_sends_quote_and_text(open_panel):
    panel = Panel(authenticated=True)
    panel.data["/api/manage/accounts"] = ACCOUNTS
    panel.data[f"{BASE}/conversations"] = CONVERSATIONS
    panel.data[f"{BASE}/messages"] = {"messages": [MESSAGE], "has_more": False}
    page = open_panel(panel, "chat")

    page.locator(".conversation", has_text="测试群一").click()
    page.locator(".message", has_text="早").hover()
    page.get_by_role("button", name="回复").click()
    box = page.get_by_label("消息内容")
    box.fill("早上好")
    box.press("Enter")

    page.wait_for_function(
        "() => !document.querySelector('[aria-label=消息内容]').value"
    )
    [(_m, _p, _h, body)] = panel.calls("POST", f"{BASE}/send")
    assert body == {
        "platform": "onebot",
        "account": "10001",
        "channel": "40001",
        "content": [
            {"type": "quote", "id": "501"},
            {"type": "text", "text": "早上好"},
        ],
    }
    assert not page.errors
