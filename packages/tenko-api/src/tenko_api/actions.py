"""稳定插件 API：宿主平台动作。

动作经过宿主的权限检查、账号可用性判断和能力探测；失败时抛出
:class:`ActionServiceError` 的子类。首个参数 ``account_or_id`` 接受 Satori
账号对象或账号 ID，``ctx`` 为触发动作的消息上下文。
"""

from tenko.host.actions import (
    ActionAccountUnavailable,
    ActionCapabilityUnavailable,
    ActionExecutionError,
    ActionFailure,
    ActionPermissionDenied,
    ActionReceipt,
    ActionServiceError,
    ActionTargetUnavailable,
    action_service,
)

send_group_message = action_service.send_group_message
send_private_message = action_service.send_private_message
mute_member = action_service.mute_member
unmute_member = action_service.unmute_member
kick_member = action_service.kick_member
delete_message = action_service.delete_message
get_group_member = action_service.get_group_member
list_group_members = action_service.list_group_members
list_groups = action_service.list_groups

__all__ = [
    "ActionAccountUnavailable",
    "ActionCapabilityUnavailable",
    "ActionExecutionError",
    "ActionFailure",
    "ActionPermissionDenied",
    "ActionReceipt",
    "ActionServiceError",
    "ActionTargetUnavailable",
    "delete_message",
    "get_group_member",
    "kick_member",
    "list_group_members",
    "list_groups",
    "mute_member",
    "send_group_message",
    "send_private_message",
    "unmute_member",
]
