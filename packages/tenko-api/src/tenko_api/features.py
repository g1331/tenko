"""稳定插件 API：功能开关查询与 Tenko 专有元数据声明。"""

from typing import Literal

from tenko.host import features as _features
from tenko.host.guards import feature_required

__all__ = ["declare", "feature_enabled", "feature_required"]


def feature_enabled(
    plugin_id: str, group_id: str | int | None = None, *, platform: str = "qq-client"
) -> bool:
    """只读查询插件在某群是否开启（全局开关、维护状态和默认值已合并）。

    ``platform`` 是群所在平台，与 ``session.event.login.platform`` 一致。
    """

    group = None if group_id is None else (platform, str(group_id))
    return _features.feature_service.is_enabled(plugin_id, group)


def declare(
    *,
    scope: Literal["group", "global"] = "group",
    default_enabled: bool = True,
    rate_weight: int = 1,
    controlled: bool = False,
) -> None:
    """在插件模块导入期间声明 Tenko 元数据，须与 ``plugin.metadata(...)`` 一起调用。

    - ``scope``：``"group"`` 按群开关；``"global"`` 只有一个全局开关。
    - ``default_enabled``：为 ``False`` 时，没有显式设置的群默认关闭该插件。
    - ``rate_weight``：每次调用该插件命令计入限流窗口的权重（正整数）。
    - ``controlled``：受控插件（例如消耗 token 的 AI 插件）。所有群默认关闭，只有
      BOT 管理员及以上能在群里打开；私聊里只有 BOT 管理员及以上能用。

    插件不能借此声明自己是受保护的宿主插件。
    """

    _features.declare_feature(
        scope=scope,
        default_enabled=default_enabled,
        rate_weight=rate_weight,
        controlled=controlled,
    )
