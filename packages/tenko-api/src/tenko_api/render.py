"""稳定插件 API：离线渲染。

``RenderService`` 由 Entari 按类型注入到命令处理函数；用 :func:`try_render`
包住它的渲染方法（如 ``render_service.render_template``），浏览器不可用时得到
``None``，请回退到文本。:func:`render_or_none` 是按方法名分发的旧写法，保留兼容。
"""

from tenko.host.render import RenderService, render_or_none, try_render

__all__ = ["RenderService", "render_or_none", "try_render"]
