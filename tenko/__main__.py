from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from arclet.entari.config import EntariConfig
from loguru import logger

from .config import TenkoConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tenko Entari/OneBot 11 runtime")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/tenko.toml"),
        help="TOML 配置文件路径（默认：config/tenko.toml）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只加载配置并打印连接信息，不启动网络服务",
    )
    subcommands = parser.add_subparsers(dest="command")
    passwd = subcommands.add_parser(
        "webui-passwd",
        help="重置 WebUI 面板密码并让所有会话下线，打印新的用户名和密码",
    )
    # SUPPRESS 避免子解析器的默认值覆盖主解析器已解析的 --config。
    passwd.add_argument(
        "--config", type=Path, default=argparse.SUPPRESS, help="TOML 配置文件路径"
    )
    passwd.add_argument("--username", help="同时修改面板用户名（默认保持不变）")
    plugins = subcommands.add_parser("plugins", help="第三方插件环境管理")
    plugins.add_argument(
        "--config", type=Path, default=argparse.SUPPRESS, help="TOML 配置文件路径"
    )
    plugins.add_subparsers(dest="plugins_command", required=True).add_parser(
        "sync", help="按 .tenko/plugins/plugins.lock 重装缺失的插件包（uv sync 之后用）"
    )
    return parser


def _current_code_root() -> Path:
    """返回当前已加载 Tenko 包的代码根目录。"""

    return Path(__file__).resolve().parent.parent


def _exec_release_root(
    release_root: Path, stable_root: Path, arguments: Sequence[str]
) -> None:
    """在 fresh Python 进程中以候选目录优先重新执行 Tenko。"""

    bootstrap = (
        "import runpy,sys;"
        "release_root=sys.argv.pop(1);"
        "sys.path.insert(0,release_root);"
        "sys.argv[0]='tenko';"
        "runpy.run_module('tenko',run_name='__main__')"
    )
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(release_root), existing_pythonpath) if value
    )
    os.chdir(stable_root)
    os.execve(
        sys.executable,
        [sys.executable, "-c", bootstrap, str(release_root), *arguments],
        environment,
    )


def _run_startup_bootstrap(config: TenkoConfig, arguments: Sequence[str]) -> bool:
    """消费 handoff，并在 active 代码不是当前代码时重新执行。"""

    from .host.updater import UpgradeConfigError, UpgradeManager, read_project_version

    stable_root = Path.cwd().resolve()
    manager = UpgradeManager.from_config(config.upgrade, project_root=stable_root)
    if manager.layout.handoff_file.is_file():
        handoff_action = None
        try:
            handoff = manager.layout.read_handoff()
        except UpgradeConfigError:
            handoff = None
        if handoff is not None:
            handoff_action = handoff.get("action")
        result = asyncio.run(manager.apply_handoff())
        if not result.success:
            if handoff_action == "rollback":
                logger.error(
                    "Tenko automatic rollback handoff failed; stopping to avoid "
                    "a restart loop: {}",
                    result.reason,
                )
                # 进程就此停止，此刻发不出消息；留给人工重启后的启动通知。
                active = manager.layout.read_active()
                if active is not None:
                    manager.layout.write_recovery_notice(
                        active.version,
                        reason="automatic rollback failure",
                        message=(
                            "升级后启动失败，自动回滚也失败，Tenko 曾停止运行；"
                            f"现以 {active.version} 版本重新启动，"
                            "详情见 .tenko/upgrades/audit.jsonl"
                        ),
                    )
                raise SystemExit(1)
            if getattr(result, "rolled_back", False):
                logger.warning(
                    "Tenko upgrade handoff failed and was rolled back; "
                    "continuing with the recovered active version: {}",
                    result.reason,
                )
            else:
                logger.warning(
                    "Tenko upgrade handoff was not applied; "
                    "continuing with the active version: {}",
                    result.reason,
                )

    active = manager.layout.read_active()
    if active is None:
        return False

    try:
        stable_version = read_project_version(stable_root)
    except UpgradeConfigError:
        stable_version = None
    if stable_version is not None and active.version < stable_version:
        logger.warning(
            "稳定根版本 {} 新于 active 指针 {}，使用稳定根代码；"
            "可删除 active.json 或执行 /升级 重建指针",
            stable_version,
            active.version,
        )
        if _current_code_root() != stable_root:
            _exec_release_root(stable_root, stable_root, arguments)
            return True
        return False

    if active.path == _current_code_root():
        from .host.updater import DependencySyncError, UvDependencySync

        try:
            synced = UvDependencySync().ensure(active.path)
        except DependencySyncError as error:
            logger.error(
                "Tenko 补同步 {} 的依赖失败，按现有环境启动: {}", active.version, error
            )
            return False
        if synced:
            # 已导入的旧依赖还留在本进程里，换新进程才能用上同步后的环境。
            logger.info("Tenko 已按 {} 的 uv.lock 补同步依赖，重新启动", active.version)
            _exec_release_root(active.path, stable_root, arguments)
            return True
        return False

    logger.info(
        "Tenko selecting active release {} from {}",
        active.version,
        active.path,
    )
    _exec_release_root(active.path, stable_root, arguments)
    return True


async def _reset_webui_password(config: TenkoConfig, username: str | None) -> None:
    from .db.bootstrap import load_database_plugin

    service = load_database_plugin(config.database)
    # 必须在官方 database 插件按配置加载之后再导入模型和 repository。
    from .webui.auth import reset_password

    try:
        await service.initialize()
        # 与宿主启动时的 create_table_at 等价：只补建缺失的表，不动已有结构。
        async with service.engines[""].begin() as connection:
            await connection.run_sync(
                service.base_class.metadata.create_all, checkfirst=True
            )
        name, password, revoked = await reset_password(username)
    finally:
        for engine in service.engines.values():
            await engine.dispose()
    print(f"username: {name}")
    print(f"password: {password}")
    print(f"已让 {revoked} 个会话下线", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    config = TenkoConfig.load(args.config)

    if args.command == "webui-passwd":
        # Entari 默认把日志写到 stdout；stdout 只留给账号和密码，便于脚本读取。
        logger.remove()
        logger.add(sys.stderr, level="WARNING")
        # load_plugin 要求 EntariConfig 已初始化；这里不需要读取其文件配置。
        if not EntariConfig._inited:
            EntariConfig(Path(".tenko/entari-boot.yaml"))
        try:
            asyncio.run(_reset_webui_password(config, args.username))
        except ValueError as error:
            print(f"错误：{error}", file=sys.stderr)
            return 2
        return 0

    if args.command == "plugins":
        from .host.plugin_manager import PluginManagerError, get_plugin_manager

        try:
            repaired = asyncio.run(get_plugin_manager().sync_env())
        except PluginManagerError as error:
            print(f"错误：{error}", file=sys.stderr)
            return 1
        print("已重装：" + "、".join(repaired) if repaired else "插件环境与插件锁一致")
        return 0

    if args.dry_run:
        logger.info("Tenko dry-run: configuration loaded; no network connection opened")
        logger.info(
            "OneBot 11 reverse WebSocket endpoint: {}", config.onebot.reverse_ws_url
        )
        return 0

    if _run_startup_bootstrap(config, arguments):
        return 0

    from .runtime import run

    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
