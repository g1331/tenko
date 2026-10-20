"""好友申请策略：保存在数据库设置表中的全局动态设置，修改后立即生效。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.repositories import ManagementRepository

SETTING_NAME = "friend-request-mode"
DEFAULT_MODE = "admins"
# 键是存库和 WebUI 使用的标识，值是 QQ 命令中的写法。
MODE_LABELS = {"off": "关闭", "admins": "仅管理员", "all": "所有人"}


def default_repository() -> ManagementRepository:
    # repository 依赖官方 database 插件已加载，只能在运行期延迟导入。
    from ..db.repositories import ManagementRepository

    return ManagementRepository()


async def get_mode(repository: ManagementRepository) -> str:
    """读取当前策略；数据库不可用或存储值非法时抛错，由调用方按“不放行”处理。"""

    mode = await repository.setting(SETTING_NAME)
    if mode is None:
        return DEFAULT_MODE
    if mode not in MODE_LABELS:
        raise ValueError(f"持久化的好友申请策略非法: {mode!r}")
    return mode


async def set_mode(repository: ManagementRepository, mode: str) -> None:
    if mode not in MODE_LABELS:
        raise ValueError("好友申请策略必须是 off、admins 或 all")
    await repository.save_setting(SETTING_NAME, mode)


def should_approve(mode: str | None, is_admin: bool) -> bool:
    """``mode`` 为 None 表示策略读取失败，此时不自动同意。"""

    return mode == "all" or (mode == "admins" and is_admin)
