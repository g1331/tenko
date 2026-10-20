"""稳定插件 API：消息构造与错误上报。"""

from tenko.plugins._common import (
    action_error_message,
    report_action_error,
    text_message,
)

# 把异常连同消息来源报告给 Master；投递失败只记日志，不会再抛出。
report_error = report_action_error

__all__ = ["action_error_message", "report_error", "text_message"]
