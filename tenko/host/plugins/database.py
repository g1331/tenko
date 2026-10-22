"""第三方插件的独立 SQLite 库：``Model`` 基类、绑定校验和引擎登记。

每个插件的模型绑定到自己的库 ``.tenko/plugin-data/<id>/data.db``，绑定键为
``plugin:<id>``。引擎在插件模块导入、第一个模型类创建时登记到官方
``SqlalchemyService.engines``：此时 service 还没有 ``initialize()``（它在 Launart
的 preparing 阶段才运行，晚于 ``load_all``），而 ``initialize()`` 只为当时已存在
的引擎建表、构造按模型绑定的会话工厂；迁移器也只认 ``engines`` 里已有的绑定键，
对未知键会静默回退到主库。
"""

from __future__ import annotations

from typing import Any

try:
    from entari_plugin_database import BaseOrm
except (AttributeError, ImportError, LookupError) as error:  # pragma: no cover
    from ...db.errors import DatabaseUnavailableError

    raise DatabaseUnavailableError(
        "官方 entari-plugin-database 尚未加载，插件无法使用 tenko_api.storage.Model"
    ) from error

from graia.amnesia.builtins.sqla.model import register_callback, remove_callback
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from .storage import PluginStorageError, prepare_plugin_db
from .runtime import external_plugin_for_module, external_plugin_version

BIND_PREFIX = "plugin:"


def _service() -> Any:
    import entari_plugin_database

    return entari_plugin_database.service


def _owner(module_name: str) -> str:
    found = external_plugin_for_module(module_name)
    if found is None:
        raise PluginStorageError(
            f"无法确定 {module_name!r} 所属的第三方插件：Model 只能在插件自己的模块里"
            "定义，且包必须在 pyproject 的 tenko.plugins 入口中声明"
        )
    return found


def _ensure_engine(plugin_id: str) -> None:
    service = _service()
    if hasattr(service, "session_factory"):
        raise PluginStorageError(
            f"数据库服务已经初始化，无法再为插件 {plugin_id} 定义新的模型；"
            "请重启 Tenko 后再加载该插件"
        )
    key = f"{BIND_PREFIX}{plugin_id}"
    if key not in service.engines:
        path = prepare_plugin_db(plugin_id, external_plugin_version(plugin_id))
        service.engines[key] = create_async_engine(
            f"sqlite+aiosqlite:///{path}", pool_pre_ping=True
        )


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


def get_session() -> AsyncSession:
    """返回会话；操作 ``Model`` 子类时自动路由到该插件自己的库。"""

    return _service().get_session()


__all__ = ["Model", "get_session", "guard_plugin_model", "install_model_guard"]
