"""稳定插件 API：通知 Master（0.4.3 起）。

发到 Master 私聊还是通知群、由哪个账号发送，沿用宿主的通知设置（面板“设置 > 通知”、
``/通知设置``），插件不需要知道 Master 是谁。宿主在内容前加一行“来自插件「名称」（ID）”。

- :func:`notify_master` 发送文字或文字加图片，返回 :class:`MasterNotice`，据
  ``delivered`` 决定是否退回其他处理。没有用户消息的场景（定时任务、后台检查）也能调用。
- 发送时传 ``key``（例如 ``"submission:42"``），之后在消息监听里用
  :func:`master_reply` 识别 Master 引用这条通知的回复，得到同一个 ``key``。记录保存
  30 天，重启后仍然有效。
- 宿主按插件限速：最多连发 5 条，之后每 3 分钟恢复一条；超出时得到
  ``reason="rate_limited"``，Master 收到一次说明。
- 命令沙盒里不会真的发出：通知原文记进当前沙盒会话，切到 Master 身份引用它回复，
  ``master_reply`` 同样能识别。

两个函数都必须在插件自己的模块里调用（规则与 ``tenko_api.storage.data_dir`` 相同），
参数在调用时就校验，返回值需要 ``await``。
"""

from tenko.host.plugins.notices import (
    MasterNotice,
    MasterReply,
    NoticeError,
    master_reply,
    notify_master,
)

__all__ = [
    "MasterNotice",
    "MasterReply",
    "NoticeError",
    "master_reply",
    "notify_master",
]
