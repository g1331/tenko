from tenko.host.errors import MAX_FAILURES, SUMMARY_INTERVAL, ErrorBook, summary_text


def _raise(message: str) -> BaseException:
    try:
        raise RuntimeError(message)
    except RuntimeError as error:
        return error


def test_same_error_with_changing_message_reports_once_then_summarises() -> None:
    book = ErrorBook()
    assert book.record(_raise("群 40001 失败"), {__name__: ""}, "40001")
    assert not book.record(_raise("群 40002 失败"), {__name__: ""}, "40002")
    assert not book.record(_raise("群 40003 失败"), {__name__: ""}, "40003")

    (record,) = book.records()
    assert record.location == f"{__file__}:6"
    assert book.due(record.reported_at + 60) == []

    (due,) = book.due(record.reported_at + SUMMARY_INTERVAL)
    assert "又发生 2 次，涉及 2 个群" in summary_text(due)
    assert "群 40003 失败" in summary_text(due)
    book.settle(due)
    assert book.due(due.reported_at + SUMMARY_INTERVAL) == []


def test_errors_from_different_lines_are_reported_separately() -> None:
    book = ErrorBook()
    assert book.record(_raise("a"), {__name__: ""})
    try:
        raise RuntimeError("a")
    except RuntimeError as error:
        assert book.record(error, {__name__: ""})


def test_failures_keep_the_input_for_replay_up_to_the_limit() -> None:
    book = ErrorBook()
    failure = {
        "platform": "qq-client",
        "account_id": "10001",
        "chat": "group",
        "group_id": "40001",
        "user_id": "20001",
        "content": "/签到",
        "preview": "/签到",
    }
    for _ in range(MAX_FAILURES + 5):
        book.record(_raise("x"), {__name__: ""}, "40001", failure)

    assert len(book.failures) == MAX_FAILURES
    latest = book.failures[-1]
    assert book.failure(latest.id) == latest
    assert latest.fingerprint == book.records()[0].fingerprint
    assert book.failure(-1) is None


def test_a_new_plugin_version_reports_the_same_error_again() -> None:
    book = ErrorBook()
    assert book.record(_raise("x"), {__name__: "1.0"}, "40001")
    assert not book.record(_raise("x"), {__name__: "1.0"}, "40002")
    assert book.record(_raise("x"), {__name__: "1.1"})
    assert book.records()[-1].seen_groups == {"40001", "40002"}
