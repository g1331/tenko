"""第三方插件测试的公共辅助：伪造 tenko.plugins 入口，并在临时目录写插件模块。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from tenko.context import MessageContext
from tenko.host import plugins as plugin_host


def entry(name: str, value: str, version: str | None = "1.0.0"):
    """模拟 ``tenko.plugins`` 入口；``version`` 是所属分发包的版本。"""

    return SimpleNamespace(
        name=name,
        value=value,
        dist=SimpleNamespace(name=f"tenko-plugin-{name}", version=version),
    )


def patch_entry_points(monkeypatch, *entries) -> None:
    groups = {"tenko.plugins": list(entries), "entari.plugin": []}
    monkeypatch.setattr(
        plugin_host, "entry_points", lambda *, group: groups.get(group, [])
    )


def write_plugin(root: Path, monkeypatch, module: str, source: str) -> None:
    """在 ``root`` 写一个插件模块并加入 sys.path。"""

    (root / f"{module}.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(root)


def drop_module(*modules: str) -> None:
    """卸载 Entari 插件并清理 sys.modules，避免插件在测试间互相污染。"""

    from arclet.entari.plugin import unload_plugin

    for module in modules:
        unload_plugin(module)
        for name in [
            m for m in sys.modules if m == module or m.startswith(f"{module}.")
        ]:
            sys.modules.pop(name, None)


def make_group_context(group_id: str, text: str = "hello") -> MessageContext:
    return MessageContext(
        account_id="10001",
        event_type="message-created",
        protocol_event_type="message.group.normal",
        chat_type="group",
        channel_id=group_id,
        user_id="20001",
        message_id="30001",
        text=text,
        image_urls=(),
    )
