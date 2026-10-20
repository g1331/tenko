"""稳定插件 API：离线渲染。

``RenderService`` 由 Entari 按类型注入到命令处理函数；调用
:func:`render_or_none` 渲染，浏览器不可用时得到 ``None``，请回退到文本。
"""

from tenko.render import RenderService, render_or_none

__all__ = ["RenderService", "render_or_none"]
