"""稳定插件 API：功能开关查询与 Tenko 专有元数据声明。"""

from typing import Literal

from tenko.host import features as _features
from tenko.host.guards import feature_required
from tenko.host.perm import Permission

__all__ = ["declare", "feature_enabled", "feature_required"]


def feature_enabled(plugin_id: str, group_id: str | int | None = None) -> bool:
    """只读查询插件在某群是否开启（全局开关、维护状态和默认值已合并）。"""

    return _features.feature_service.is_enabled(plugin_id, group_id)


def declare(
    *,
    scope: Literal["group", "global"] = "group",
    default_enabled: bool = True,
    rate_weight: int = 1,
    level: Permission = Permission.ActiveGroup,
) -> None:
    """在插件模块导入期间声明 Tenko 元数据，须与 ``plugin.metadata(...)`` 一起调用。

    - ``scope``：``"group"`` 按群开关；``"global"`` 只有一个全局开关。
    - ``default_enabled``：为 ``False`` 时，没有显式设置的群默认关闭该插件。
    - ``rate_weight``：每次调用该插件命令计入限流窗口的权重（正整数）。
    - ``level``：插件要求的群等级（``Permission.InactiveGroup`` 到 ``TestGroup``），
      默认 ``ActiveGroup``。群等级低于它时，该插件的命令在群里被静默忽略。

    插件不能借此声明自己是受保护的宿主插件。
    """

    _features.declare_feature(
        scope=scope,
        default_enabled=default_enabled,
        rate_weight=rate_weight,
        level=level,
    )
