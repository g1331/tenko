"""WebUI 可编辑的宿主配置项。

连接监听、数据库、WebUI 安全和 superusers 只能在配置文件里改，不在这里暴露。
单项先在这里校验，整份配置再由 ``WebUIService.update_config`` 用 ``TenkoConfig``
校验后写回；所有改动重启后生效。
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from ..config import RegistryConfig

LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR")


@dataclass(frozen=True, slots=True)
class ConfigField:
    key: str
    group: str
    label: str
    kind: str  # boolean / integer / number / enum / identifier / urls
    default: Any
    help: str = ""
    options: tuple[tuple[Any, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "group": self.group,
            "label": self.label,
            "kind": self.kind,
            "default": self.default,
            "help": self.help,
            "options": [{"value": v, "label": label} for v, label in self.options],
            "min": self.minimum,
            "max": self.maximum,
        }


FIELDS: tuple[ConfigField, ...] = (
    ConfigField(
        "notify.group",
        "basic",
        "默认通知群",
        "identifier",
        "",
        "「通知设置」从未保存过时，通知发到这个群；留空则私聊 Master。",
    ),
    ConfigField(
        "log_level",
        "basic",
        "日志级别",
        "enum",
        "INFO",
        "DEBUG 会记录大量细节，排查问题时再开。",
        tuple((level, level) for level in LOG_LEVELS),
    ),
    ConfigField(
        "debug.enabled",
        "basic",
        "调试模式",
        "boolean",
        False,
        "开启后只处理主人（superusers）发出的消息，其他人一律忽略。",
    ),
    ConfigField(
        "upgrade.enabled",
        "upgrade",
        "启用升级系统",
        "boolean",
        True,
        "关闭后 /升级、自动检查和面板重启都不可用。",
    ),
    ConfigField(
        "upgrade.channel",
        "upgrade",
        "更新通道",
        "enum",
        "stable",
        "预发布通道会收到 rc 等测试版本。",
        (("stable", "正式版"), ("prerelease", "预发布")),
    ),
    ConfigField(
        "upgrade.policy",
        "upgrade",
        "发现新版本时",
        "enum",
        "check",
        "",
        (("check", "只通知"), ("download", "下载待装"), ("install", "自动安装")),
    ),
    ConfigField(
        "upgrade.check_interval_hours",
        "upgrade",
        "检查间隔（小时）",
        "integer",
        24,
        "",
        minimum=1,
        maximum=720,
    ),
    ConfigField(
        "render.timeout",
        "render",
        "渲染超时（秒）",
        "number",
        10.0,
        "超时后回退为文本回复。",
        minimum=1,
        maximum=120,
    ),
    ConfigField(
        "render.quality",
        "render",
        "JPEG 质量",
        "integer",
        85,
        "",
        minimum=1,
        maximum=100,
    ),
    ConfigField(
        "render.device_scale_factor",
        "render",
        "清晰度倍率",
        "integer",
        2,
        "倍率越高图片越清晰，体积也越大。",
        minimum=1,
        maximum=4,
    ),
    ConfigField("ratelimit.enabled", "ratelimit", "启用限流", "boolean", True),
    ConfigField(
        "ratelimit.window_seconds",
        "ratelimit",
        "统计窗口（秒）",
        "number",
        15.0,
        "",
        minimum=1,
        maximum=3600,
    ),
    ConfigField(
        "ratelimit.max_weight",
        "ratelimit",
        "窗口内命令上限",
        "integer",
        24,
        "同一用户在统计窗口内累计的命令权重上限。",
        minimum=1,
        maximum=1000,
    ),
    ConfigField(
        "ratelimit.cooldown_seconds",
        "ratelimit",
        "超限冷却（秒）",
        "number",
        5.0,
        "",
        minimum=0,
        maximum=3600,
    ),
    ConfigField(
        "ratelimit.blacklist_seconds",
        "ratelimit",
        "反复超限拉黑（秒）",
        "number",
        300.0,
        "",
        minimum=0,
        maximum=86400,
    ),
    ConfigField(
        "ratelimit.override_permission",
        "ratelimit",
        "不受限流的权限",
        "enum",
        1,
        "达到该权限的成员不受限流。",
        (
            (1, "群管理员及以上"),
            (2, "群主及以上"),
            (3, "Bot 管理员及以上"),
            (4, "仅主人"),
        ),
    ),
    ConfigField(
        "chat_log.enabled",
        "chat_log",
        "记录聊天消息",
        "boolean",
        True,
        "面板聊天页的历史来自这里；关闭后仍可发送，但不显示历史。",
    ),
    ConfigField(
        "chat_log.retention_days",
        "chat_log",
        "保留天数",
        "integer",
        7,
        "",
        minimum=1,
        maximum=365,
    ),
    ConfigField(
        "chat_log.max_messages",
        "chat_log",
        "最多保留条数",
        "integer",
        50000,
        "",
        minimum=1000,
        maximum=5_000_000,
    ),
    ConfigField(
        "exception.message_buffer_size",
        "exception",
        "异常报告附带消息数",
        "integer",
        10,
        "异常报告里附上最近几条消息。",
        minimum=1,
        maximum=100,
    ),
    ConfigField(
        "registry.urls",
        "registry",
        "插件目录地址",
        "urls",
        list(RegistryConfig().urls),
        "按顺序尝试，前一个不可用时回退到下一个。",
    ),
)
FIELD_MAP = {field.key: field for field in FIELDS}


def _path(field: ConfigField, raw: Mapping[str, Any]) -> tuple[str, ...]:
    if field.key != "log_level":
        return tuple(field.key.split("."))
    # 没有官方 [basic] 节时沿用旧 [runtime] log_level：新建 [basic] 会让旧
    # [entari] 里的 superusers 失效。
    return ("basic", "log", "level") if "basic" in raw else ("runtime", "log_level")


def _lookup(raw: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = raw
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _has_placeholder(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_placeholder(item) for item in value)
    return isinstance(value, str) and "${" in value


def read(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for field in FIELDS:
        value = _lookup(raw, _path(field, raw))
        rows.append(
            {
                **field.describe(),
                "value": field.default if value is None else value,
                "read_only": _has_placeholder(value),
            }
        )
    return rows


def validate(field: ConfigField, value: Any) -> Any:
    """返回要写入的值；``None`` 表示删除该键。"""

    if field.kind == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{field.label}必须是开或关")
        return value
    if field.kind == "enum":
        if value not in [option for option, _ in field.options] or type(value) is bool:
            raise ValueError(f"{field.label}的取值无效")
        return value
    if field.kind == "identifier":
        if not isinstance(value, str) or not (value == "" or value.isdigit()):
            raise ValueError(f"{field.label}必须是群号或留空")
        return value or None
    if field.kind == "urls":
        if (
            not isinstance(value, list)
            or not value
            or not all(
                isinstance(url, str) and url.startswith("https://") for url in value
            )
        ):
            raise ValueError(f"{field.label}必须是至少一个 https 地址")
        return [url.strip() for url in value]
    numeric = int if field.kind == "integer" else (int, float)
    if not isinstance(value, numeric) or isinstance(value, bool):
        raise ValueError(
            f"{field.label}必须是{'整数' if field.kind == 'integer' else '数字'}"
        )
    if (field.minimum is not None and value < field.minimum) or (
        field.maximum is not None and value > field.maximum
    ):
        raise ValueError(f"{field.label}必须在 {field.minimum} 到 {field.maximum} 之间")
    return value


def plan(raw: Mapping[str, Any], values: Any) -> list[tuple[tuple[str, ...], Any]]:
    """校验请求，返回 (路径, 新值) 列表。"""

    if not isinstance(values, Mapping) or not values:
        raise ValueError("没有要保存的配置")
    changes = []
    for key, value in values.items():
        field = FIELD_MAP.get(key)
        if field is None:
            raise ValueError(f"不支持在面板修改 {key}")
        path = _path(field, raw)
        if _has_placeholder(_lookup(raw, path)):
            raise ValueError(f"{field.label}引用了环境变量，请在配置文件里修改")
        changes.append((path, validate(field, value)))
    return changes


def apply(document: MutableMapping[str, Any], changes: list) -> None:
    """同时用于 tomlkit 文档和内存里的配置字典。"""

    for path, value in changes:
        node = document
        for part in path[:-1]:
            node = node.setdefault(part, {})
        if value is None:
            node.pop(path[-1], None)
        else:
            node[path[-1]] = value
