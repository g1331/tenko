"""稳定插件 API：错误上报。

命令处理函数直接返回 ``str`` 即可，Entari 会原样发送；混合内容（图片等）返回
``MessageChain``。
"""

from tenko.host.action_errors import action_error_message, report_action_error

# 把异常连同消息来源报告给 Master；投递失败只记日志，不会再抛出。
report_error = report_action_error


__all__ = ["action_error_message", "report_error"]
