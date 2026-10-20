"""稳定插件 API：消息上下文。"""

from tenko.context import MessageContext
from tenko.plugins._common import context_from_session

__all__ = ["MessageContext", "context_from_session"]
