"""官方迁移器的“Schema 只前进”保护：只允许增量变更，破坏性变更被拒绝。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from arclet.entari.config import EntariConfig
from graia.amnesia.builtins.sqla import SqlalchemyService
from graia.amnesia.builtins.sqla.model import Base
from loguru import logger
from sqlalchemy import Column, Integer, String, Table, inspect, text

from tenko.db.errors import DatabaseUnavailableError
from tenko.db.migration import (
    install_forward_only_guard,
    run_database_migrations,
)

MODULE = "tenko_test_guard_models"
KEEP, EVOLVE = "guard_keep", "guard_evolve"


def _official():
    """官方插件导入时要求 EntariConfig 已初始化，所以不能在模块顶层导入。"""

    if not EntariConfig._inited:
        EntariConfig(Path("/tmp/tenko-migration-guard-test.yml"))
    from entari_plugin_database import migration

    return migration


class GuardHarness:
    """用临时 SQLite 和假模型驱动官方迁移器，不污染全局模型注册表。"""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.state_file = tmp_path / "migrations_lock.json"
        self.models: list[type] = []
        self.errors: list[str] = []
        official = _official()
        monkeypatch.setattr(official, "_STATE_FILE", self.state_file)
        monkeypatch.setattr(official, "get_subclasses", lambda _base: self.models)
        self.service = SqlalchemyService(
            f"sqlite+aiosqlite:///{tmp_path / 'guard.db'}",
            engine_options={"echo": False},
        )

    def define(self, name: str, *columns: Column) -> type:
        """用最新定义替换同名表，并返回对应的假模型。"""

        if name in Base.metadata.tables:
            Base.metadata.remove(Base.metadata.tables[name])
        table = Table(
            name, Base.metadata, Column("id", Integer, primary_key=True), *columns
        )
        model = type(name, (), {"__table__": table, "__module__": MODULE})
        self.models = [m for m in self.models if m.__name__ != name] + [model]
        return model

    async def migrate(self) -> None:
        await run_database_migrations(self.service)

    async def sql(self, statement: str):
        async with self.service.engines[""].begin() as connection:
            result = await connection.execute(text(statement))
            return result.all() if result.returns_rows else []

    async def columns(self, table: str) -> list[str]:
        async with self.service.engines[""].connect() as connection:
            infos = await connection.run_sync(lambda c: inspect(c).get_columns(table))
        return [info["name"] for info in infos]

    def state(self) -> dict:
        return json.loads(self.state_file.read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def guard(tmp_path, monkeypatch):
    harness = GuardHarness(tmp_path, monkeypatch)
    sink = logger.add(
        lambda message: harness.errors.append(str(message)), level="ERROR"
    )
    await harness.service.initialize()
    harness.define(KEEP)
    harness.define(EVOLVE, Column("title", String(20)))
    try:
        await harness.migrate()
        await harness.sql(f"INSERT INTO {EVOLVE} (id, title) VALUES (1, '旧数据')")
        harness.errors.clear()
        yield harness
    finally:
        logger.remove(sink)
        for name in (KEEP, EVOLVE):
            if name in Base.metadata.tables:
                Base.metadata.remove(Base.metadata.tables[name])
        await harness.service.engines[""].dispose()


@pytest.mark.asyncio
async def test_additive_changes_still_apply(guard: GuardHarness) -> None:
    guard.define(EVOLVE, Column("title", String(20)), Column("memo", String(50)))
    guard.define("guard_new", Column("name", String(10)))
    try:
        await guard.migrate()
        assert await guard.columns(EVOLVE) == ["id", "title", "memo"]
        assert await guard.columns("guard_new") == ["id", "name"]
        assert await guard.sql(f"SELECT title FROM {EVOLVE}") == [("旧数据",)]
        assert not guard.errors
    finally:
        Base.metadata.remove(Base.metadata.tables["guard_new"])


@pytest.mark.asyncio
async def test_drop_column_is_refused_and_data_survives(guard: GuardHarness) -> None:
    revision = guard.state()[EVOLVE]["revision"]
    # 同一次迁移里的加列也一并回滚，并且 revision 不被标记为已完成，
    # 之后每次启动都会重新拒绝并报告。
    guard.define(EVOLVE, Column("memo", String(50)))

    await guard.migrate()

    assert await guard.columns(EVOLVE) == ["id", "title"]
    assert await guard.sql(f"SELECT title FROM {EVOLVE}") == [("旧数据",)]
    assert f"删除列 {EVOLVE}.title" in "".join(guard.errors)
    assert guard.state()[EVOLVE]["revision"] == revision


@pytest.mark.asyncio
async def test_column_type_change_is_refused(guard: GuardHarness) -> None:
    guard.define(EVOLVE, Column("title", Integer))

    await guard.migrate()

    assert await guard.sql(f"SELECT typeof(title), title FROM {EVOLVE}") == [
        ("text", "旧数据")
    ]
    assert f"修改列类型 {EVOLVE}.title" in "".join(guard.errors)


@pytest.mark.asyncio
async def test_removed_model_keeps_table_and_state(guard: GuardHarness) -> None:
    guard.models = [m for m in guard.models if m.__name__ != EVOLVE]
    Base.metadata.remove(Base.metadata.tables[EVOLVE])

    await guard.migrate()

    assert await guard.sql(f"SELECT title FROM {EVOLVE}") == [("旧数据",)]
    assert EVOLVE in guard.state()
    assert EVOLVE in "".join(guard.errors)


@pytest.mark.asyncio
async def test_explicit_custom_migration_still_runs(
    guard: GuardHarness, monkeypatch
) -> None:
    """显式注册的迁移脚本是开发者的有意操作，保护不拦截它。"""

    official = _official()
    monkeypatch.setattr(official, "_CUSTOM_MIGRATIONS", {})
    ran: list[str] = []

    @official.register_custom_migration(EVOLVE, script_id="guard_script")
    def upgrade(ops, context, table):
        ran.append(table)
        ops.add_column(table, Column("memo", String(50)))

    guard.define(EVOLVE, Column("title", String(20)), Column("memo", String(50)))

    await guard.migrate()

    assert ran == [EVOLVE]
    assert await guard.columns(EVOLVE) == ["id", "title", "memo"]
    assert await guard.sql(f"SELECT title FROM {EVOLVE}") == [("旧数据",)]
    assert guard.state()[EVOLVE]["custom_scripts"]["guard_script"]["current"] == "1"
    assert not guard.errors


def test_official_private_api_still_exists() -> None:
    """保护依赖 entari-plugin-database 0.4.2 的私有函数；升级后它们消失则必须失败。"""

    official = _official()
    for name in (
        "_execute_table_drops",
        "_normalize_alembic_ops",
        "_update_state_for_model",
        "run_migration",
    ):
        assert callable(getattr(official, name)), name


def test_guard_refuses_to_install_when_private_api_disappears(monkeypatch) -> None:
    monkeypatch.delattr(_official(), "_execute_table_drops")
    with pytest.raises(DatabaseUnavailableError, match="私有接口已变化"):
        install_forward_only_guard()
