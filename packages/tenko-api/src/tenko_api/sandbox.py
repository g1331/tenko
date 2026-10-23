"""稳定插件 API：命令沙盒。

面板的命令沙盒会真实执行插件逻辑，宿主在出口处隔离副作用：真实账号的发送、撤回、
禁言等写操作不会发出，宿主全局设置不会被改，``tenko_api.storage`` 读写的是数据
副本。宿主管不到的副作用（调用外部服务、扣费、写 ``data_dir()`` 以外的文件）由
插件自己用 :func:`in_sandbox` 判断。
"""

from tenko.host.sandbox import origin

__all__ = ["in_sandbox"]


def in_sandbox() -> bool:
    """当前处理链是否来自命令沙盒。"""

    return origin() is not None
