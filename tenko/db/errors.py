"""数据库边界使用的异常类型和不可用判定。"""

import sqlite3

_DATABASE_IMPORT_ROOTS = (
    "tenko.db",
    "sqlalchemy",
    "aiosqlite",
    "asyncpg",
    "aiomysql",
    "psycopg2",
)
_SQLALCHEMY_CONNECTION_ERRORS = {
    "DisconnectionError",
    "InterfaceError",
    "NoSuchModuleError",
    "OperationalError",
    "TimeoutError",
}


class DatabaseError(RuntimeError):
    """数据库操作失败。"""


class DatabaseUnavailableError(DatabaseError):
    """数据库插件、会话工厂或数据库连接当前不可用。"""


def is_database_unavailable(error: BaseException) -> bool:
    """判断异常是否属于数据库依赖/连接不可用，而不是业务逻辑错误。

    按类名匹配 SQLAlchemy 异常，所以数据库依赖缺失时也能调用。
    """

    if isinstance(error, DatabaseUnavailableError):
        return True
    if isinstance(error, ImportError):
        module_name = getattr(error, "name", None)
        if module_name and any(
            module_name == root or module_name.startswith(f"{root}.")
            for root in _DATABASE_IMPORT_ROOTS
        ):
            return True
        message = str(error)
        return "No module named 'sqlalchemy'" in message or (
            "sqlalchemy" in message and "cannot import" in message
        )

    if isinstance(
        error, ConnectionError | TimeoutError | OSError | sqlite3.OperationalError
    ):
        return True

    for error_type in type(error).__mro__:
        if error_type.__module__ != "sqlalchemy.exc":
            continue
        if error_type.__name__ in _SQLALCHEMY_CONNECTION_ERRORS:
            return True
        if error_type.__name__ == "DBAPIError":
            return bool(getattr(error, "connection_invalidated", False))
    return False
