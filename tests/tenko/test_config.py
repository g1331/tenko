from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger
from arclet.entari.config import EntariConfig as NativeEntariConfig
from arclet.entari.config.model import BasicConfig, LogSaveInfo

from tenko.config import TenkoConfig, WebUIConfig


def test_default_config_is_local_and_does_not_send() -> None:
    config = TenkoConfig()

    assert isinstance(config.basic, BasicConfig)
    assert config.basic.prefix == ["/"]
    assert config.basic.log.save is None
    assert config.onebot.reverse_ws_url == "ws://127.0.0.1:8080/onebot/v11/ws"
    assert config.command_prefix == "/"
    assert config.debug.enabled is False
    assert config.debug.masters == ()
    assert config.exception.message_buffer_size == 10
    assert config.exception.evidence_dir == ".tenko/exceptions"
    assert config.database.url == "sqlite+aiosqlite:///./.tenko/tenko.db"
    assert config.database.create_table_at == "preparing"
    assert config.webui == WebUIConfig()
    assert config.webui.token is None
    assert config.webui.public_url is None
    assert "web-secret" not in repr(WebUIConfig(enabled=True, token="web-secret"))
    assert config.upgrade.restart_watch_timeout == 300
    assert config.notify_group is None


def test_config_loads_reverse_ws_and_runtime_options(tmp_path) -> None:
    path = tmp_path / "tenko.toml"
    path.write_text(
        """
[notify]
group = 40001

[onebot]
listen_host = "0.0.0.0"
listen_port = 9000
access_token = "secret"
# 旧版内部 Satori 配置：识别后忽略，不影响启动。
satori_token = "satori-secret"
satori_host = "127.0.0.1"

[runtime]
log_level = "DEBUG"
command_prefix = "!"
superusers = { onebot = [12345, "67890"] }

[debug]
enabled = true
masters = [12345, "67890"]

[database]
url = "sqlite+aiosqlite:///tmp/tenko.db"
echo = true
create_table_at = "prepared"
""",
        encoding="utf-8",
    )

    config = TenkoConfig.load(path)

    assert config.onebot.reverse_ws_url == "ws://0.0.0.0:9000/onebot/v11/ws"
    assert config.onebot.access_token == "secret"
    assert config.onebot.local_host == "127.0.0.1"
    assert config.basic.prefix == ["!"]
    assert config.basic.log.level == "DEBUG"
    assert "entari" not in config.entari_config.data
    assert "runtime" not in config.entari_config.data
    assert config.command_prefix == "!"
    assert config.basic.superusers == {"onebot": ["12345", "67890"]}
    assert config.debug.enabled is True
    assert config.debug.masters == ("12345", "67890")
    assert config.notify_group == "40001"
    assert config.database.url == "sqlite+aiosqlite:///tmp/tenko.db"
    assert config.database.echo is True
    assert config.database.create_table_at == "prepared"


def test_top_level_notify_group_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"\[notify\]"):
        TenkoConfig.from_mapping({"notify_group": "40001"})


def test_config_loads_official_basic_and_tenko_sections_together(tmp_path) -> None:
    path = tmp_path / "tenko.toml"
    path.write_text(
        """
[basic]
prefix = ["/", "!"]
ignore_self_message = false
skip_req_missing = true
superusers = { onebot = [12345, "67890"] }

[basic.log]
level = "WARNING"
ignores = ["tenko.noisy"]
save = { rotation = "10 MB", compression = "gz", colorize = false }

[[basic.network]]
type = "ws"
host = "127.0.0.1"
port = 5140
path = "satori"
token = "placeholder"

[onebot]
listen_port = 9000

[debug]
enabled = true
""",
        encoding="utf-8",
    )

    config = TenkoConfig.load(path)

    assert isinstance(config.basic, BasicConfig)
    assert config.basic.prefix == ["/", "!"]
    assert config.basic.ignore_self_message is False
    assert config.basic.skip_req_missing is True
    assert config.basic.superusers == {"onebot": ["12345", "67890"]}
    assert config.basic.log.level == "WARNING"
    assert config.basic.log.ignores == ["tenko.noisy"]
    assert config.basic.log.save == LogSaveInfo(
        rotation="10 MB", compression="gz", colorize=False
    )
    assert config.basic.network[0].type == "ws"
    assert config.basic.network[0].port == 5140
    assert config.onebot.listen_port == 9000
    assert config.basic.log.level == "WARNING"
    assert config.basic.superusers == {"onebot": ["12345", "67890"]}
    assert config.debug.masters == ("12345", "67890")
    assert config.entari_config is NativeEntariConfig.instance


def test_config_example_is_loadable_with_official_basic_section(tmp_path) -> None:
    example = Path(__file__).parents[2] / "config/tenko.toml.example"
    path = tmp_path / "tenko.toml"
    path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = TenkoConfig.load(path)

    assert config.basic.prefix == ["/"]
    assert config.basic.log.save is None
    assert config.basic.superusers == {"qq-client": ["YOUR_QQ_ID"]}
    assert config.notify_group is None
    # 模板默认的版本源不依赖 Git，压缩包部署也能检查和安装更新。
    from tenko.host.updater import UpgradeManager

    project = Path(__file__).parents[2]
    manager = UpgradeManager.from_config(config.upgrade, project_root=project)
    assert [source.name for source in manager.sources] == ["github_release"]
    assert manager.source_error is None


@pytest.mark.parametrize(
    "text", ["", '[basic]\nprefix = ["/"]\n', "[basic]\nstr_as_message = true\n"]
)
def test_plugin_str_replies_are_always_plain_text(tmp_path, text) -> None:
    """插件直接返回 str：Entari 的 str_as_message 会把 str 当 Satori XML 解析，强制关闭。"""

    path = tmp_path / "tenko.toml"
    path.write_text(text, encoding="utf-8")

    basic = TenkoConfig.load(path).basic
    assert basic.str_as_message is False
    assert basic.prefix == ["/"]


def test_entari_save_preserves_tenko_sections_and_writes_toml(tmp_path) -> None:
    path = tmp_path / "tenko.toml"
    path.write_text(
        """
[basic]
prefix = ["/"]

[onebot]
listen_port = 9001
""",
        encoding="utf-8",
    )

    config = TenkoConfig.load(path)
    config.entari_config.data["basic"]["log"] = {
        "level": "INFO",
        "save": {"rotation": "00:00", "compression": "gz", "colorize": False},
    }
    config.entari_config.save()

    saved = TenkoConfig.load(path)
    assert saved.onebot.listen_port == 9001
    assert saved.basic.log.save == LogSaveInfo(
        rotation="00:00", compression="gz", colorize=False
    )
    assert saved.entari_config.data["onebot"]["listen_port"] == 9001


def test_empty_command_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="command_prefix"):
        TenkoConfig.from_mapping({"runtime": {"command_prefix": ""}})


def test_official_basic_takes_precedence_over_legacy_sections() -> None:
    config = TenkoConfig.from_mapping(
        {
            "basic": {
                "prefix": ["!"],
                "log": {"level": "WARNING"},
                "superusers": {"onebot": ["official"]},
            },
            "runtime": {
                "command_prefix": "/",
                "log_level": "DEBUG",
                "superusers": {"onebot": ["legacy"]},
            },
            "entari": {"superusers": {"onebot": ["old-entari"]}},
        }
    )

    assert config.basic.prefix == ["!"]
    assert config.basic.log.level == "WARNING"
    assert config.basic.superusers == {"onebot": ["official"]}


def test_exception_evidence_configuration_is_loaded_and_validated() -> None:
    config = TenkoConfig.from_mapping(
        {
            "exception": {
                "message_buffer_size": 32,
                "evidence_dir": ".tenko/test-exceptions",
            }
        }
    )

    assert config.exception.message_buffer_size == 32
    assert config.exception.evidence_dir == ".tenko/test-exceptions"
    with pytest.raises(ValueError, match="message_buffer_size"):
        TenkoConfig.from_mapping({"exception": {"message_buffer_size": 0}})


def test_database_configuration_rejects_unknown_table_creation_stage() -> None:
    with pytest.raises(ValueError, match="create_table_at"):
        TenkoConfig.from_mapping({"database": {"create_table_at": "later"}})


def test_webui_configuration_needs_no_token_when_enabled() -> None:
    config = TenkoConfig.from_mapping(
        {
            "webui": {
                "enabled": True,
                "allowed_ips": ["127.0.0.1", "::1", "10.1.2.3/8"],
                "public_url": "http://203.0.113.10:8081/",
            },
        }
    )

    assert config.webui.enabled is True
    assert config.webui.allowed_ips == ("127.0.0.1", "::1", "10.0.0.0/8")
    assert config.webui.public_url == "http://203.0.113.10:8081"
    for bad in ("localhost", "10.0.0.0/33"):
        with pytest.raises(ValueError, match="非法 IP"):
            TenkoConfig.from_mapping({"webui": {"allowed_ips": [bad]}})
    for value in ("203.0.113.10:8081", "ftp://example.com", 8081):
        with pytest.raises(ValueError, match="public_url"):
            TenkoConfig.from_mapping({"webui": {"public_url": value}})
    assert (
        TenkoConfig.from_mapping({"webui": {"public_url": ""}}).webui.public_url is None
    )


def test_entari_superusers_are_inherited_by_debug_and_upgrade() -> None:
    config = TenkoConfig.from_mapping(
        {
            "entari": {"superusers": {"onebot": [12345], "satori": ["67890"]}},
        }
    )

    assert config.basic.superusers == {"onebot": ["12345"], "satori": ["67890"]}
    assert config.debug.masters == ("12345", "67890")
    assert config.upgrade.superuser_ids == ("12345", "67890")


def test_explicit_debug_and_upgrade_superusers_override_inheritance() -> None:
    config = TenkoConfig.from_mapping(
        {
            "entari": {"superusers": {"onebot": ["10001"]}},
            "runtime": {"superusers": {"onebot": ["legacy"]}},
            "debug": {"masters": ["20001"]},
            "upgrade": {"superuser_ids": ["30001"]},
        }
    )

    assert config.basic.superusers == {"onebot": ["10001"]}
    assert config.debug.masters == ("20001",)
    assert config.upgrade.superuser_ids == ("30001",)


def test_deprecated_webui_tokens_load_with_one_warning() -> None:
    messages: list[str] = []
    sink = logger.add(messages.append, level="WARNING", format="{message}")
    try:
        config = TenkoConfig.from_mapping(
            {"webui": {"enabled": True, "token": "reader", "admin_token": "reader"}}
        )
        TenkoConfig.from_mapping({"webui": {"enabled": True, "token": ""}})
    finally:
        logger.remove(sink)

    assert config.webui.enabled is True
    assert len([message for message in messages if "已弃用" in message]) == 1
    assert "reader" not in repr(config.webui)


def test_non_loopback_bind_requires_access_token() -> None:
    from tenko.config import OneBotConfig

    for host in ("0.0.0.0", "::"):
        with pytest.raises(ValueError, match="listen_host"):
            TenkoConfig(onebot=OneBotConfig(listen_host=host))

    TenkoConfig(onebot=OneBotConfig(listen_host="::1"))
    TenkoConfig(onebot=OneBotConfig(listen_host="localhost"))
    TenkoConfig(onebot=OneBotConfig(listen_host="0.0.0.0", access_token="a"))


def test_plugin_config_tables_load_and_survive_entari_save(tmp_path) -> None:
    path = tmp_path / "tenko.toml"
    path.write_text(
        """
[basic]
prefix = ["/"]

[plugin_config.example]
greeting = "你好"
limit = 3

[plugin_config.example.nested]
enabled = true

[plugin_config.other_plugin]
token = "${{ env.OTHER_TOKEN:-none }}"
""",
        encoding="utf-8",
    )

    config = TenkoConfig.load(path)
    assert config.plugin_config["example"] == {
        "greeting": "你好",
        "limit": 3,
        "nested": {"enabled": True},
    }
    assert set(config.plugin_config) == {"example", "other_plugin"}
    # 不是 Entari 的 [plugins]：不会触发它按配置自动加载插件。
    assert config.entari_config.plugin == {}
    assert config.entari_config.plugin_names == []

    # WebUI / Entari 写回配置文件后，插件配置节原样保留。
    config.entari_config.data["onebot"] = {"listen_port": 9002}
    config.entari_config.save()
    saved = TenkoConfig.load(path)
    assert saved.plugin_config == config.plugin_config
    assert saved.onebot.listen_port == 9002
    assert "${{ env.OTHER_TOKEN:-none }}" in path.read_text(encoding="utf-8")


def test_plugin_config_defaults_to_empty_and_rejects_bad_shapes() -> None:
    assert TenkoConfig().plugin_config == {}
    with pytest.raises(ValueError, match="必须是 TOML table"):
        TenkoConfig.from_mapping({"plugin_config": {"example": "字符串"}})
    with pytest.raises(ValueError, match="不是合法的插件 ID"):
        TenkoConfig.from_mapping({"plugin_config": {"Bad-Name": {}}})
    with pytest.raises(ValueError, match=r"\[plugin_config\]"):
        TenkoConfig.from_mapping({"plugin_config": ["x"]})
