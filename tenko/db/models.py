"""Tenko SQLAlchemy 模型。"""

from __future__ import annotations

try:
    from entari_plugin_database import BaseOrm, Mapped, mapped_column
except (AttributeError, ImportError, LookupError) as error:  # pragma: no cover
    from .errors import DatabaseUnavailableError

    raise DatabaseUnavailableError(
        "官方 entari-plugin-database 尚未加载，无法注册 Tenko 数据模型"
    ) from error

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from .migration import LEGACY_SCHEMA_REVISION


class MemberPerm(BaseOrm):
    """成员权限；``group_id=0`` 表示全局权限记录。"""

    __tablename__ = "MemberPerm"
    __revision__ = LEGACY_SCHEMA_REVISION

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    qq: Mapped[int] = mapped_column(Integer, primary_key=True)
    perm: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        info={"check": [-1, 0, 16, 32, 64, 128, 256]},
        default=16,
    )


class GroupPerm(BaseOrm):
    """群等级和启用状态。"""

    __tablename__ = "GroupPerm"
    __revision__ = LEGACY_SCHEMA_REVISION

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[str] = mapped_column(String(length=60), nullable=False)
    perm: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        info={"check": [0, 1, 2, 3]},
        default=1,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=True, default=True)


class GroupSetting(BaseOrm):
    """群频控、响应账号和成员权限同步策略。"""

    __tablename__ = "GroupSetting"
    __revision__ = LEGACY_SCHEMA_REVISION

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    frequency_limitation: Mapped[bool] = mapped_column(
        Boolean, nullable=True, default=True
    )
    response_type: Mapped[str] = mapped_column(
        String,
        nullable=True,
        info={"check": ["random", "deterministic"]},
        default="random",
    )
    permission_type: Mapped[str] = mapped_column(
        String,
        nullable=True,
        info={"check": ["default", "admin"]},
        default="default",
    )


class FeatureState(BaseOrm):
    """Tenko 功能开关的全局或群级显式状态。"""

    __tablename__ = "TenkoFeatureState"

    plugin_name: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        String(length=128), primary_key=True, default=""
    )
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AccountRoute(BaseOrm):
    """账号与群的有序绑定关系。"""

    __tablename__ = "TenkoAccountRoute"
    __table_args__ = (
        UniqueConstraint("group_id", "position", name="uq_tenko_route_position"),
    )

    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class AccountResponseState(BaseOrm):
    """群级 random/deterministic 响应策略和指定账号。"""

    __tablename__ = "TenkoAccountResponseState"

    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    response_type: Mapped[str] = mapped_column(
        String(length=32), nullable=False, default="random"
    )
    deterministic_account: Mapped[str | None] = mapped_column(
        String(length=128), nullable=True
    )


class RateLimitEvent(BaseOrm):
    """限流滚动窗口中的一次加权命令事件。"""

    __tablename__ = "TenkoRateLimitEvent"
    __table_args__ = (
        Index(
            "ix_tenko_rate_event_subject_time",
            "group_id",
            "user_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(length=128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(length=128), nullable=False)
    occurred_at: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)


class RateLimitSubjectState(BaseOrm):
    """用户×群的限流冷却和临时黑名单到期时间。"""

    __tablename__ = "TenkoRateLimitSubjectState"

    group_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    cooldown_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    blacklist_until: Mapped[float | None] = mapped_column(Float, nullable=True)


class StartupTime(BaseOrm):
    """一次启动完成所记录的耗时样本。"""

    __tablename__ = "TenkoStartupTime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    duration: Mapped[float] = mapped_column(Float, nullable=False)


class AccountPreference(BaseOrm):
    """平台限定的账户管理偏好；不存协议凭据。"""

    __tablename__ = "TenkoAccountPreference"

    platform: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    alias: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities: Mapped[str] = mapped_column(String, nullable=False, default="{}")


class WebUISetting(BaseOrm):
    """面板和命令写入的运行期设置，与连接配置分开保存。"""

    # 旧表名 TenkoWebUISetting；官方迁移器按类名识别改名，所以类名不能改。
    __tablename__ = "TenkoSetting"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class WebUIAccount(BaseOrm):
    """管理面板唯一账号；``id`` 固定为 1，密码只保存 scrypt 哈希。"""

    __tablename__ = "TenkoWebUIAccount"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WebUISession(BaseOrm):
    """面板登录会话；只保存 cookie 令牌的 sha256，不保存令牌本身。"""

    __tablename__ = "TenkoWebUISession"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class ChatMessage(BaseOrm):
    """面板聊天页的消息记录：某个 bot 账号在群聊或私聊里收到、发出的一条消息。

    每个账号各存一份：同一条群消息的 message_id 在各账号的协议端里并不相同，
    引用回复也必须用发送账号自己的 ID。
    """

    __tablename__ = "TenkoChatMessage"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "account_id",
            "channel_id",
            "message_id",
            name="uq_tenko_chat_message",
        ),
        Index("ix_tenko_chat_conversation", "platform", "account_id", "channel_id"),
        Index("ix_tenko_chat_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outgoing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 归一化后的消息段 JSON，见 tenko.host.chat_log.segments。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


MODEL_CLASSES = (
    MemberPerm,
    GroupPerm,
    GroupSetting,
    FeatureState,
    AccountRoute,
    AccountResponseState,
    RateLimitEvent,
    RateLimitSubjectState,
    StartupTime,
    AccountPreference,
    WebUISetting,
    WebUIAccount,
    WebUISession,
    ChatMessage,
)

__all__ = [
    "ChatMessage",
    "GroupPerm",
    "GroupSetting",
    "FeatureState",
    "AccountRoute",
    "AccountResponseState",
    "RateLimitEvent",
    "RateLimitSubjectState",
    "StartupTime",
    "MODEL_CLASSES",
    "AccountPreference",
    "WebUISetting",
    "WebUIAccount",
    "WebUISession",
    "MemberPerm",
]
