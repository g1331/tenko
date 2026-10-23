"""扩展插件的独立 SQLite 库：``Model`` 基类、绑定校验、引擎登记和运行中切换。

每个插件的模型绑定到自己的库 ``.tenko/plugin-data/<id>/data.db``，绑定键为
``plugin:<id>``。引擎在插件模块导入、第一个模型类创建时登记到官方
``SqlalchemyService.engines``；启动时这早于 service 的 ``initialize()`` 和迁移，
迁移器只认 ``engines`` 里已有的绑定键，对未知键会静默回退到主库。

运行中加载或热更新插件时，:func:`plugin_db_reload` 包住导入：先把旧版本的表移出
共用的元数据（否则新代码定义同名表会冲突），成功后重开引擎（版本变化时先备份）并
只迁移这个插件的表。:func:`get_session` 按模型的绑定键实时查引擎，所以换掉的引擎
对新会话立即生效，不依赖启动时构造的按类绑定。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

try:
    from entari_plugin_database import BaseOrm
except (AttributeError, ImportError, LookupError) as error:  # pragma: no cover
    from ...db.errors import DatabaseUnavailableError

    raise DatabaseUnavailableError(
        "官方 entari-plugin-database 尚未加载，插件无法使用 tenko_api.storage.Model"
    ) from error

from graia.amnesia.builtins.sqla.model import Base, register_callback, remove_callback
from graia.amnesia.builtins.utils import get_subclasses
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from .storage import PluginMigrationError, PluginStorageError, prepare_plugin_db
from .runtime import external_plugin_for_module, external_plugin_version

BIND_PREFIX = "plugin:"


def _service() -> Any:
    import entari_plugin_database

    return entari_plugin_database.service


def _owner(module_name: str) -> str:
    found = external_plugin_for_module(module_name)
    if found is None:
        raise PluginStorageError(
            f"无法确定 {module_name!r} 所属的扩展插件：Model 只能在插件自己的模块里"
            "定义，且包必须在 pyproject 的 tenko.plugins 入口中声明"
        )
    return found


def _open_engine(service: Any, plugin_id: str) -> None:
    path = prepare_plugin_db(plugin_id, external_plugin_version(plugin_id))
    service.engines[f"{BIND_PREFIX}{plugin_id}"] = create_async_engine(
        f"sqlite+aiosqlite:///{path}", pool_pre_ping=True
    )


def _ensure_engine(plugin_id: str) -> None:
    service = _service()
    if f"{BIND_PREFIX}{plugin_id}" not in service.engines:
        _open_engine(service, plugin_id)


def _tables(key: str) -> list[Table]:
    return [t for t in Base.metadata.tables.values() if t.info.get("bind_key") == key]


@asynccontextmanager
async def plugin_db_reload(plugin_id: str) -> AsyncIterator[None]:
    """包住运行中对插件的加载或重载；导入失败时还原旧版本的表，成功后迁移插件库。

    迁移失败时抛出 :class:`PluginMigrationError`，此时新代码已经在运行。
    """

    key = f"{BIND_PREFIX}{plugin_id}"
    old = _tables(key)
    for table in old:  # 旧模型类仍持有自己的 Table，移出元数据不影响它们继续运行
        Base.metadata.remove(table)
    try:
        yield
    except BaseException:
        for table in _tables(key):
            Base.metadata.remove(table)
        for table in old:
            Base.metadata._add_table(table.name, table.schema, table)
        raise
    if not _tables(key):
        return
    service = _service()
    # 先摘下再关闭：切换期间的会话直接报错，而不是打开已关闭的引擎。
    if (engine := service.engines.pop(key, None)) is not None:
        await engine.dispose()
    _open_engine(service, plugin_id)  # 版本变了就先备份；回滚时还原成旧版本的库
    try:
        await _migrate(service, key)
    except Exception as error:
        raise PluginMigrationError(
            f"插件 {plugin_id} 的数据库迁移失败：{error}"
        ) from error


async def _migrate(service: Any, key: str) -> None:
    """按官方迁移器的流程只迁移 ``key`` 下当前元数据里的表（与启动时的结果一致）。"""

    # shortcut: 官方迁移器只提供迁移全部模型的入口，这里复用它 0.4.2 的私有步骤；
    # 升级 entari-plugin-database 时与 tenko/db/migration.py 的补丁一起复核。
    from entari_plugin_database import migration as official

    live = {t.name: t for t in _tables(key)}
    grouped: dict[str, list[type[Base]]] = {}
    for model in set(get_subclasses(Base)):
        table = getattr(model, "__table__", None)
        if table is not None and live.get(table.name) is table:  # 跳过被替换的旧类
            grouped.setdefault(model.__module__, []).append(model)
    async with official._MIGRATION_LOCK:
        state = official._load_state()
        for module, models in grouped.items():
            plan = official._plan_migrations(module, models, state)
            await official._execute_migration_plan(plan, module, service, state)
            for table, info in plan.model_info.items():
                if state.get(table, {}).get("revision") != info["revision"]:
                    official._update_state_for_model(state, table, info, module)
            official._save_state(state)


def guard_plugin_model(cls: type, kwargs: dict[str, Any]) -> None:
    """Base 子类创建前的回调：外部插件的模型必须绑定到自己的库，否则拒绝加载。"""

    if cls.__module__.startswith("tenko.") or cls.__dict__.get("__abstract__"):
        return
    plugin_id = external_plugin_for_module(cls.__module__)
    if plugin_id is None:
        return
    actual = getattr(cls, "__bind_key__", kwargs.get("bind_key"))
    if actual != f"{BIND_PREFIX}{plugin_id}":
        raise PluginStorageError(
            f"插件 {plugin_id} 的模型 {cls.__qualname__} 绑定到了"
            f"{'主库' if not actual else repr(actual)}：插件不能写入宿主数据库，"
            "请继承 tenko_api.storage.Model"
        )
    _ensure_engine(plugin_id)


def install_model_guard() -> None:
    """注册 :func:`guard_plugin_model`；重复调用无副作用。"""

    remove_callback(guard_plugin_model)
    register_callback(guard_plugin_model)


class Model(BaseOrm, tablename="tenko_plugin_model"):
    """插件模型的声明式基类，绑定到调用方插件自己的 SQLite 库。

    默认表名为 ``<插件ID>_<类名小写>``；显式写 ``__tablename__`` 时，名称在所有
    插件之间必须唯一（它们共用一份 SQLAlchemy 元数据）。
    """

    __abstract__ = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        plugin_id = _owner(cls.__module__)
        cls.__bind_key__ = f"{BIND_PREFIX}{plugin_id}"
        # 官方的自动命名依赖调用栈上的插件上下文，经过这一层后不可靠，所以在这里
        # 命名；传 ``tablename`` 还能让官方回调直接跳过它。
        if not (
            {"__tablename__", "__table__"} & cls.__dict__.keys()
            or "tablename" in kwargs
        ):
            kwargs["tablename"] = f"{plugin_id}_{cls.__name__.lower()}"
        super().__init_subclass__(**kwargs)


class _RoutedSession(Session):
    """插件模型按 ``__bind_key__`` 实时找引擎，其余仍按官方会话的规则。"""

    def get_bind(self, mapper: Any = None, clause: Any = None, **kw: Any) -> Any:
        key = getattr(getattr(mapper, "class_", mapper), "__bind_key__", None)
        if not (isinstance(key, str) and key.startswith(BIND_PREFIX)):
            return super().get_bind(mapper, clause=clause, **kw)
        engine = _service().engines.get(key)
        if engine is None:
            raise PluginStorageError(f"插件库 {key} 正在切换，请稍后重试")
        return engine.sync_engine


def get_session() -> AsyncSession:
    """返回会话；操作 ``Model`` 子类时自动路由到该插件自己的库。"""

    service = _service()
    return AsyncSession(
        service.engines[""],
        sync_session_class=_RoutedSession,
        **service.session_options,
    )


__all__ = [
    "Model",
    "get_session",
    "guard_plugin_model",
    "install_model_guard",
    "plugin_db_reload",
]
