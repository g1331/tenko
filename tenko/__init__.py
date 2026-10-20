"""Tenko 的 Entari + Satori/OneBot 11 最小运行时。"""

import sys
import time
from pathlib import Path

from .commands import configure_command_prefix

# 升级器按目录切换代码但共享同一个 venv，site-packages 里的 tenko-api 可能是
# 旧版本；让随当前宿主代码发布的门面排在最前，保证两者版本一致。作为 wheel
# 安装时没有这个目录，自然跳过。
_API_SOURCE = Path(__file__).resolve().parent.parent / "packages/tenko-api/src"
if _API_SOURCE.is_dir() and str(_API_SOURCE) not in sys.path[:1]:
    sys.path.insert(0, str(_API_SOURCE))

# ``tenko`` 包在 ``python -m tenko`` 进入 ``__main__`` 之前初始化；在这里
# 记录单调时钟，才能把配置读取和插件装配时间纳入启动耗时。
_process_start_monotonic = time.perf_counter()

# 插件模块会在导入期间构造 Alconna 对象；在任何
# ``tenko.plugins`` 包被导入之前先设置默认值。
configure_command_prefix()

__all__ = ["configure_command_prefix"]
