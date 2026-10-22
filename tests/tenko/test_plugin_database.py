"""第三方插件的独立 SQLite 库：Model 绑定、自动迁移和绑定主库的拒绝。"""

from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path

import pytest
import pytest_asyncio
from arclet.entari.config import EntariConfig
from external_plugin_support import entry, patch_entry_points
from graia.amnesia.builtins.sqla import SqlalchemyService
from graia.amnesia.builtins.sqla.model import Base, remove_callback
from loguru import logger
from sqlalchemy import exc as sa_exc

from tenko_api.storage import get_session

from tenko.db.migration import install_forward_only_guard, run_database_migrations
from tenko.host.plugins.storage import PluginStorageError

MODULE = "tenko_t_plugin_db"
IMPORTS = (
    "from sqlalchemy import String\n"
    "from sqlalchemy.orm import Mapped, mapped_column\n"
    "from tenko_api.storage import Model\n"
)
NOTE_V1 = (
    IMPORTS
    + """
class Note(Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(20))
"""
)
TABLE = "db_demo_note"


class PluginDatabaseHost:
    """用临时目录里的真实 SQLite 驱动官方迁移器；``restart`` 模拟一次进程重启。"""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        if not EntariConfig._inited:
            EntariConfig(Path("/tmp/tenko-plugin-database-test.yml"))
        import entari_plugin_database
        from entari_plugin_database import migration

        from tenko.host.plugins import database as plugin_models

        self.root, self.models, self.monkeypatch = root, [], monkeypatch
        self.module, self.errors = entari_plugin_database, []
        self.data_db = root / ".tenko" / "plugin-data" / "db_demo" / "data.db"
        monkeypatch.setattr(migration, "_STATE_FILE", root / "migrations_lock.json")
        monkeypatch.setattr(migration, "get_subclasses", lambda _base: self.models)
        install_forward_only_guard()
        plugin_models.install_model_guard()
        self.restart()

    def restart(self) -> None:
        self.service = SqlalchemyService(
            f"sqlite+aiosqlite:///{self.root / 'main.db'}",
            engine_options={"echo": False},
        )
        self.monkeypatch.setattr(self.module, "service", self.service)

    def define(self, source: str) -> dict:
        namespace = {"__name__": MODULE}
        for table in [t for t in Base.metadata.tables if t.startswith("db_demo_")]:
            Base.metadata.remove(Base.metadata.tables[table])
        with warnings.catch_warnings():
            # 重启后重新定义同名模型，SQLAlchemy 会提示替换旧类。
            warnings.simplefilter("ignore", sa_exc.SAWarning)
            exec(compile(source, MODULE, "exec", dont_inherit=True), namespace)
        self.models = [
            value
            for value in namespace.values()
            if isinstance(value, type) and hasattr(value, "__table__")
        ]
        return namespace

    async def boot(self) -> None:
        await self.service.initialize()
        await run_database_migrations(self.service)

    def rows(self, sql: str, database: Path | None = None) -> list:
        with sqlite3.connect(database or self.data_db) as connection:
            return connection.execute(sql).fetchall()


@pytest_asyncio.fixture
async def plugin_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    patch_entry_points(monkeypatch, entry("db_demo", MODULE, version="1.0.0"))
    host = PluginDatabaseHost(tmp_path, monkeypatch)
    sink = logger.add(lambda message: host.errors.append(str(message)), level="ERROR")
    try:
        yield host
    finally:
        logger.remove(sink)
        from tenko.host.plugins import database as plugin_models

        remove_callback(plugin_models.guard_plugin_model)
        for table in [t for t in Base.metadata.tables if t.startswith("db_demo_")]:
            Base.metadata.remove(Base.metadata.tables[table])
        for engine in host.service.engines.values():
            await engine.dispose()


@pytest.mark.asyncio
async def test_model_lands_in_the_plugin_database_not_the_main_one(plugin_db) -> None:
    namespace = plugin_db.define(NOTE_V1)
    note_model = namespace["Note"]
    await plugin_db.boot()

    async with get_session() as session:
        session.add(note_model(id=1, text="你好"))
        await session.commit()

    assert note_model.__table__.name == TABLE
    assert plugin_db.rows(f"SELECT text FROM {TABLE}") == [("你好",)]
    main = plugin_db.root / "main.db"
    assert not main.exists() or not plugin_db.rows(
        f"SELECT name FROM sqlite_master WHERE name = '{TABLE}'", main
    )


@pytest.mark.asyncio
async def test_auto_migration_adds_a_nullable_column(plugin_db) -> None:
    plugin_db.define(NOTE_V1)
    await plugin_db.boot()
    with sqlite3.connect(plugin_db.data_db) as connection:
        connection.execute(f"INSERT INTO {TABLE} VALUES (1, '旧')")
    await plugin_db.service.engines["plugin:db_demo"].dispose()

    plugin_db.restart()
    plugin_db.define(
        NOTE_V1 + "    memo: Mapped[str | None] = mapped_column(String(50))\n"
    )
    await plugin_db.boot()

    assert plugin_db.rows(f"SELECT id, text, memo FROM {TABLE}") == [(1, "旧", None)]
    assert not plugin_db.errors


@pytest.mark.asyncio
async def test_destructive_change_is_refused_for_plugin_databases(plugin_db) -> None:
    plugin_db.define(NOTE_V1)
    await plugin_db.boot()
    with sqlite3.connect(plugin_db.data_db) as connection:
        connection.execute(f"INSERT INTO {TABLE} VALUES (1, '旧')")
    await plugin_db.service.engines["plugin:db_demo"].dispose()

    plugin_db.restart()
    plugin_db.define(
        IMPORTS
        + "class Note(Model):\n    id: Mapped[int] = mapped_column(primary_key=True)\n"
    )
    await plugin_db.boot()

    assert plugin_db.rows(f"SELECT id, text FROM {TABLE}") == [(1, "旧")]
    assert f"删除列 {TABLE}.text" in "".join(plugin_db.errors)


@pytest.mark.asyncio
async def test_model_bound_to_the_main_database_is_rejected(plugin_db) -> None:
    source = (
        "from entari_plugin_database import BaseOrm\n"
        "from sqlalchemy.orm import Mapped, mapped_column\n"
        "class Bad(BaseOrm):\n"
        "    __tablename__ = 'db_demo_bad'\n"
        "    id: Mapped[int] = mapped_column(primary_key=True)\n"
    )
    with pytest.raises(PluginStorageError, match="主库"):
        plugin_db.define(source)
    assert "db_demo_bad" not in Base.metadata.tables
    assert "plugin:db_demo" not in plugin_db.service.engines
