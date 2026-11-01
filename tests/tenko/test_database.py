from __future__ import annotations

import json
from pathlib import Path

import pytest
from arclet.entari.config import EntariConfig
from graia.amnesia.builtins.sqla import SqlalchemyService
from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    inspect,
    insert,
)
from sqlalchemy.ext.asyncio import create_async_engine

from tenko.config import TenkoConfig
from tenko.db.bootstrap import _official_config
from tenko.db.migration import RETIRED_TABLES, run_database_migrations


def _legacy_metadata() -> MetaData:
    metadata = MetaData()
    Table(
        "MemberPerm",
        metadata,
        Column("group_id", Integer, primary_key=True),
        Column("qq", Integer, primary_key=True),
        Column("perm", Integer, nullable=False, default=16),
    )
    Table(
        "GroupPerm",
        metadata,
        Column("group_id", Integer, primary_key=True),
        Column("group_name", String(length=60), nullable=False),
        Column("perm", Integer, nullable=False, default=1),
        Column("active", Boolean, default=True),
    )
    Table(
        "GroupSetting",
        metadata,
        Column("group_id", Integer, primary_key=True),
        Column("frequency_limitation", Boolean, default=True),
        Column(
            "response_type",
            String,
            default="random",
        ),
        Column("permission_type", String, default="default"),
    )
    return metadata


async def _create_legacy_database(path: Path) -> MetaData:
    metadata = _legacy_metadata()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            insert(metadata.tables["MemberPerm"]),
            [
                {"group_id": 1, "qq": index, "perm": permission}
                for index, permission in enumerate(
                    (-1, 0, 16, 32, 64, 128, 256), start=1
                )
            ],
        )
        await connection.execute(
            insert(metadata.tables["GroupPerm"]),
            {"group_id": 1, "group_name": "旧群", "perm": 2, "active": True},
        )
        await connection.execute(
            insert(metadata.tables["GroupSetting"]),
            {
                "group_id": 1,
                "frequency_limitation": False,
                "response_type": "deterministic",
                "permission_type": "admin",
            },
        )
    await engine.dispose()
    return metadata


def test_runtime_state_bootstrap_creates_tables_before_state_load() -> None:
    config = TenkoConfig.from_mapping(
        {
            "database": {
                "url": "sqlite+aiosqlite:///tmp/tenko.db",
                "create_table_at": "prepared",
            }
        }
    )

    assert _official_config(config.database)["create_table_at"] == "prepared"
    assert (
        _official_config(config.database, runtime_state_service=object())[
            "create_table_at"
        ]
        == "preparing"
    )


@pytest.mark.asyncio
async def test_migration_drops_retired_tables_and_builds_current_schema(
    tmp_path, monkeypatch
):
    """旧库（含 #234 之前的状态记录）升级：退役表被删除，新表按当前模型建立。"""

    if not EntariConfig._inited:
        EntariConfig(Path("/tmp/tenko-database-test.yml"))

    database_path = tmp_path / "legacy.db"
    await _create_legacy_database(database_path)
    service = SqlalchemyService(
        f"sqlite+aiosqlite:///{database_path}",
        engine_options={"echo": False, "pool_pre_ping": True},
    )
    from tenko.db.models import MODEL_CLASSES

    await service.initialize()

    from entari_plugin_database import migration as official_migration

    migration_state = tmp_path / "migrations_lock.json"
    # #234 之前的状态：GroupSetting 由同名模型记录。若不先移除，官方迁移器会把它
    # 当成新 GroupSetting 模型（表名 TenkoGroupSetting）的改名来源。
    migration_state.write_text(
        json.dumps(
            {
                "GroupSetting": {
                    "module": "tenko.db.models",
                    "name": "GroupSetting",
                    "revision": "tenko-g1-legacy-schema-v1",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(official_migration, "_STATE_FILE", migration_state)
    await run_database_migrations(service)

    state = json.loads(migration_state.read_text(encoding="utf-8"))
    current = {model.__tablename__: model for model in MODEL_CLASSES}
    assert not RETIRED_TABLES & set(state)
    assert set(current) <= set(state)
    async with service.engines[""].connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
        assert table_names == set(current)
        for table_name, model in current.items():
            columns = await connection.run_sync(
                lambda sync_connection, name=table_name: inspect(
                    sync_connection
                ).get_columns(name)
            )
            assert [column["name"] for column in columns] == [
                column.name for column in model.__table__.columns
            ]
    await service.engines[""].dispose()
