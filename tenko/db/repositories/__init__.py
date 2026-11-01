"""Tenko 数据库 repository。

repository 只持有官方 database service 提供的异步 session 工厂，不把
SQLAlchemy 查询对象暴露给宿主或插件调用方。各领域的 repository 按文件拆分，
这里统一 re-export；全局 session 工厂只存在于 ``_base`` 模块。
"""

from __future__ import annotations

from ._base import configure_database_service, configure_session_factory
from .accounts import (
    AccountResponseRecord,
    AccountRouteRecord,
    AccountStateRepository,
    AccountStateSnapshot,
    account_state_repository,
)
from .chat import ChatMessageRepository, ChatRecord, chat_message_repository
from .features import (
    FeatureStateRecord,
    FeatureStateRepository,
    feature_state_repository,
)
from .group_settings import GroupSettingRepository, group_setting_repository
from .management import ManagementRepository
from .permissions import (
    BlacklistRecord,
    PermissionRepository,
    PermissionSnapshot,
    permission_repository,
)
from .ratelimit import (
    RateLimitEventRecord,
    RateLimitRepository,
    RateLimitStateSnapshot,
    RateLimitSubjectRecord,
    rate_limit_repository,
)
from .startup import StartupTimeRepository, startup_time_repository
from .stats import CommandStatRecord, CommandStatRepository
from .webui_auth import (
    WebUIAccountRecord,
    WebUIAuthRepository,
    WebUISessionRecord,
    webui_auth_repository,
)

__all__ = [
    "AccountResponseRecord",
    "AccountRouteRecord",
    "AccountStateRepository",
    "AccountStateSnapshot",
    "BlacklistRecord",
    "ChatMessageRepository",
    "ChatRecord",
    "CommandStatRecord",
    "CommandStatRepository",
    "FeatureStateRecord",
    "FeatureStateRepository",
    "GroupSettingRepository",
    "ManagementRepository",
    "PermissionRepository",
    "PermissionSnapshot",
    "RateLimitEventRecord",
    "RateLimitRepository",
    "RateLimitStateSnapshot",
    "RateLimitSubjectRecord",
    "StartupTimeRepository",
    "WebUIAccountRecord",
    "WebUIAuthRepository",
    "WebUISessionRecord",
    "account_state_repository",
    "chat_message_repository",
    "configure_database_service",
    "configure_session_factory",
    "feature_state_repository",
    "group_setting_repository",
    "permission_repository",
    "rate_limit_repository",
    "startup_time_repository",
    "webui_auth_repository",
]
