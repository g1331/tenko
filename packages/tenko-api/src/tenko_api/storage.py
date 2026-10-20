"""稳定插件 API：插件独立存储。

两种存储都以调用方插件的 ID 为键，且只能在插件自己的模块里使用（包必须在
``pyproject.toml`` 的 ``tenko.plugins`` 入口中声明，子模块同样适用）：

- :func:`data_dir`：数据目录 ``.tenko/plugin-data/<id>/``；
- :class:`Store`：带版本号的 JSON 文件，旧版本迁移前自动备份。
"""

from tenko.host.plugin_storage import (
    PluginStorageError,
    Store,
    StoreVersionError,
    data_dir,
)

__all__ = ["PluginStorageError", "Store", "StoreVersionError", "data_dir"]
