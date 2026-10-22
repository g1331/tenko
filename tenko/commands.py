from __future__ import annotations

from collections.abc import Iterable

from arclet.alconna import config as alconna_config


def configure_command_prefix(prefixes: Iterable[str] = ("/",)) -> None:
    """设置 Alconna 默认命名空间的命令前缀。

    Alconna 在构建每条命令时会复制 ``default_namespace.prefixes``，因此必须在导入
    插件之前调用。运行时传入空列表，前缀交给 Entari 官方 ``basic.prefix`` 统一剥离；
    包导入时的默认 ``/`` 只服务于直接用 Alconna 解析 ``/命令`` 的插件测试。
    """

    alconna_config.default_namespace.prefixes = list(prefixes)
