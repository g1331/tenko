"""稳定插件 API：插件独立存储。

存储都以调用方插件的 ID 为键，且只能在插件自己的模块里使用（包必须在
``pyproject.toml`` 的 ``tenko.plugins`` 入口中声明，子模块同样适用）：

- :func:`data_dir`：数据目录 ``.tenko/plugin-data/<id>/``；
- :class:`Store`：带版本号的 JSON 文件，旧版本迁移前自动备份；
- :class:`Model`：SQLAlchemy 声明式基类，表建在插件自己的 SQLite 库
  ``.tenko/plugin-data/<id>/data.db``；:func:`get_session` 获取会话。

``Model`` 和 ``get_session`` 在首次访问时才转发到宿主的数据库服务，所以不使用
数据库的插件不受影响。
"""

from typing import TYPE_CHECKING, Any

from tenko.host.plugins.storage import (
    PluginStorageError,
    Store,
    StoreVersionError,
    data_dir,
)

if TYPE_CHECKING:
    from tenko.host.plugins.database import Model, get_session

__all__ = [
    "Model",
    "PluginStorageError",
    "Store",
    "StoreVersionError",
    "data_dir",
    "get_session",
]


def __getattr__(name: str) -> Any:
    if name in ("Model", "get_session"):
        from tenko.host.plugins import database as plugin_models

        return getattr(plugin_models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
