"""稳定插件 API：错误上报。

命令处理函数直接返回 ``str`` 即可，Entari 会原样发送；混合内容（图片等）返回
``MessageChain``。``text_message`` 自 0.2.0 起弃用，将在 1.0 前移除。
"""

import warnings

from tenko.host.action_errors import action_error_message, report_action_error

# 把异常连同消息来源报告给 Master；投递失败只记日志，不会再抛出。
report_error = report_action_error


def text_message(content: str):
    """返回只含一段文本的 ``MessageChain``。

    .. deprecated:: 0.2.0
        直接返回 ``str``；将在 1.0 前移除。
    """

    from arclet.entari import MessageChain
    from satori import Text

    warnings.warn(
        "tenko_api.messages.text_message 自 0.2.0 起弃用，将在 1.0 前移除；"
        "命令处理函数请直接返回 str",
        DeprecationWarning,
        stacklevel=2,
    )
    return MessageChain(Text(content))


__all__ = ["action_error_message", "report_error", "text_message"]
