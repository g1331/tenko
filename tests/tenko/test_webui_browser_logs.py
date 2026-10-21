"""日志页（实时轮询、历史检索、滚动保持、复制）的浏览器验证；后端为路由替身。"""

from urllib.parse import parse_qs, urlparse

from webui_browser_support import Panel, sse

LOG_PATH = "/api/manage/logs"


def record(index, level="INFO"):
    return {
        "id": index,
        "time": f"2026-10-20T12:00:{index % 60:02d}+00:00",
        "level": level,
        "message": f"message {index}",
    }


class LogsPanel(Panel):
    """在通用替身之上提供可控的日志接口，并记录每次日志查询参数。"""

    def __init__(self):
        super().__init__(authenticated=True)
        self.records = [record(1), record(2, "ERROR")]
        self.queries = []
        self.history = {
            "lines": ["2026-10-19 ERROR boom one", "2026-10-19 ERROR boom two"],
            "truncated": True,
            "matched": 731,
            "scan_limit_bytes": 8 * 1024 * 1024,
        }
        self.fail = False

    def handle(self, route):
        url = urlparse(route.request.url)
        if not url.path.endswith(LOG_PATH):
            super().handle(route)
            return
        query = {
            key: values[0]
            for key, values in parse_qs(url.query, keep_blank_values=True).items()
        }
        self.queries.append(query)
        self.requests.append((route.request.method, LOG_PATH, {}, None))
        if self.fail:
            route.fulfill(
                status=500,
                json={"ok": False, "error": {"code": "x", "message": "磁盘故障"}},
            )
            return
        common = {"files": ["tenko.log", "tenko.1.log"], "history_enabled": True}
        if "file" in query:
            data = {**common, **self.history}
        else:
            after = int(query.get("after", "0"))
            data = {
                **common,
                "records": [r for r in self.records if r["id"] > after],
                "cursor": self.records[-1]["id"],
                "reset": False,
                "truncated": False,
            }
        route.fulfill(json={"ok": True, "data": data})


def output_text(page):
    return page.locator(".log-output").inner_text()


def go_to(page, name):
    page.get_by_role("menuitem", name=name).first.click()


def test_live_polls_every_3s_and_stops_after_leaving_page(open_panel):
    panel = LogsPanel()
    page = open_panel(panel, "logs", clock=True)
    page.get_by_text("message 2").wait_for()
    assert panel.queries == [{"q": "", "after": "0", "level": ""}]

    panel.records.append(record(3))
    page.clock.run_for(3100)
    page.get_by_text("message 3").wait_for()
    assert panel.queries[-1]["after"] == "2"
    assert output_text(page).count("message 2") == 1  # 增量追加，不重复

    go_to(page, "总览")
    page.get_by_text("消息活动").wait_for()
    seen = len(panel.queries)
    page.clock.run_for(12_000)
    page.wait_for_timeout(200)
    assert len(panel.queries) == seen
    assert not page.errors


def test_pushed_log_records_append_without_duplicates(open_panel):
    panel = LogsPanel()
    # 推送里含已显示过的 id 2：按游标去重，只追加 3、4。
    records = [record(2, "ERROR"), record(3), record(4)]
    push = {"records": records, "cursor": 4, "reset": False}
    panel.events = "retry: 60000\n\n" + sse("logs", push, 4)
    page = open_panel(panel, "logs")
    page.get_by_text("message 4").wait_for()
    assert output_text(page).count("message 2") == 1
    assert "message 3" in output_text(page)
    assert not page.errors


def test_live_polling_pauses_while_tab_hidden(open_panel):
    panel = LogsPanel()
    page = open_panel(panel, "logs", clock=True)
    page.get_by_text("message 2").wait_for()
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {get: () => true, configurable: true})"
    )
    seen = len(panel.queries)
    page.clock.run_for(9000)
    page.wait_for_timeout(200)
    assert len(panel.queries) == seen
    assert not page.errors


def test_history_search_sends_file_and_query_and_marks_truncation(open_panel):
    panel = LogsPanel()
    page = open_panel(panel, "logs", clock=True)
    page.get_by_text("message 2").wait_for()

    page.locator(".source").click()
    page.get_by_role("option", name="tenko.1.log").click()
    page.get_by_text("boom one").wait_for()
    assert panel.queries[-1] == {"q": "", "file": "tenko.1.log"}
    assert page.locator(".level").count() == 0  # 级别筛选只对实时日志有效

    page.get_by_placeholder("搜索关键字").fill("boom")
    page.get_by_placeholder("搜索关键字").press("Enter")
    page.get_by_text("结果已截断").wait_for()
    assert panel.queries[-1] == {"q": "boom", "file": "tenko.1.log"}
    assert "8 MiB" in page.locator(".hint").inner_text()

    # 历史模式不轮询。
    seen = len(panel.queries)
    page.clock.run_for(9000)
    page.wait_for_timeout(200)
    assert len(panel.queries) == seen

    # 回到实时日志：从头读取并带上搜索词。
    page.locator(".source").click()
    page.get_by_role("option", name="实时日志").click()
    page.get_by_text("message 1").wait_for()
    assert panel.queries[-1] == {"q": "boom", "after": "0", "level": ""}
    assert not page.errors


def test_live_scroll_follows_only_when_at_bottom(open_panel):
    panel = LogsPanel()
    panel.records = [record(i) for i in range(1, 121)]
    page = open_panel(panel, "logs", clock=True, viewport=(1280, 600))
    page.get_by_text("message 120").wait_for()
    box = page.locator(".log-output")
    at_bottom = "e => e.scrollTop + e.clientHeight >= e.scrollHeight - 2"
    assert box.evaluate(at_bottom)  # 首次加载定位到最新

    panel.records.append(record(121))
    page.clock.run_for(3100)
    page.get_by_text("message 121").wait_for()
    assert box.evaluate(at_bottom)  # 停在底部时继续跟随

    box.evaluate("e => (e.scrollTop = 40)")
    panel.records.append(record(122))
    page.clock.run_for(3100)
    page.get_by_text("message 122").wait_for()
    assert box.evaluate("e => e.scrollTop") == 40  # 向上翻阅时不抢滚动位置
    assert not page.errors


def test_copy_puts_visible_lines_on_clipboard(open_panel):
    panel = LogsPanel()
    page = open_panel(panel, "logs", clock=True)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.get_by_text("message 2").wait_for()
    page.get_by_role("button", name="复制").click()
    page.get_by_text("已复制 2 行日志").wait_for()
    copied = page.evaluate("navigator.clipboard.readText()")
    assert "message 1" in copied and "ERROR    message 2" in copied
    assert not page.errors


def tick(page, panel):
    """推进一次轮询周期，并等到这次轮询的请求真正发出且处理完。

    请求在真实时间里完成；上一次请求未结束时下一次轮询会被跳过，
    所以不能连续推进假时钟而不等待。
    """
    seen = len(panel.queries)
    page.clock.run_for(3100)
    while len(panel.queries) == seen:
        page.wait_for_timeout(20)
    page.wait_for_timeout(100)


TOAST_COUNTER = """
window.toastsShown = 0
new MutationObserver((records) => {
  for (const record of records)
    for (const node of record.addedNodes)
      if (node.classList?.contains('el-message')) window.toastsShown += 1
}).observe(document.body, { childList: true, subtree: true })
"""


def test_error_state_shows_toast_once_and_retry(open_panel):
    panel = LogsPanel()
    panel.fail = True
    page = open_panel(panel, "logs", clock=True)
    # 严格模式下同时出现多个同名提示会直接报错，所以首次出现时恰为一条。
    page.get_by_text("读取日志失败：磁盘故障").wait_for()
    assert "日志读取失败：磁盘故障" in output_text(page)

    # 提示 3 秒后会被（假）时钟自动关闭，DOM 中的当前数量取决于淡出动画的真实耗时，
    # 不能用来判断是否重复弹出，所以记录之后新建的提示条数。
    page.evaluate(TOAST_COUNTER)
    for _ in range(2):
        tick(page, panel)
    assert page.evaluate("window.toastsShown") == 0  # 持续失败不重复弹窗

    page.get_by_role("button", name="重试").click()  # 再次失败：新一轮失败应重新提示
    page.wait_for_function("window.toastsShown === 1")
    page.get_by_role("button", name="重试").wait_for()  # 失败结果渲染完毕

    panel.fail = False
    page.get_by_role("button", name="重试").click()
    page.get_by_text("message 2").wait_for()
    assert not page.errors
