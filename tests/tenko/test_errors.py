from tenko.host.errors import SUMMARY_INTERVAL, ErrorBook, summary_text


def _raise(message: str) -> BaseException:
    try:
        raise RuntimeError(message)
    except RuntimeError as error:
        return error


def test_same_error_with_changing_message_reports_once_then_summarises() -> None:
    book = ErrorBook()
    assert book.record(_raise("群 40001 失败"), (__name__,), "40001")
    assert not book.record(_raise("群 40002 失败"), (__name__,), "40002")
    assert not book.record(_raise("群 40003 失败"), (__name__,), "40003")

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
    assert book.record(_raise("a"), (__name__,))
    try:
        raise RuntimeError("a")
    except RuntimeError as error:
        assert book.record(error, (__name__,))
