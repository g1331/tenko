"""Tenko 对官方 database 插件迁移器的窄适配。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from alembic.operations.ops import ModifyTableOps
from loguru import logger
from sqlalchemy import MetaData, inspect

from .errors import DatabaseUnavailableError

# 退役的表：#234（v4.0.0-rc35）起由带平台主键的新表取代，之后不再读写，当时也没有
# 迁移数据；MemberPerm、GroupPerm、GroupSetting 是 G1 旧库遗留的表。它们是“只前进”
# 保护唯一允许删除的表：每次迁移前删掉，并从官方迁移状态里移除，官方迁移器就不会
# 把它们当成废弃表（被保护拒绝）或改名来源（旧库直接升级时旧表会被改名成新表）。
# 删表前升级器已经备份了数据库。新增条目同样要求表已停用并跨过至少两个版本。
RETIRED_TABLES = frozenset(
    {
        "MemberPerm",
        "GroupPerm",
        "GroupSetting",
        "TenkoFeatureState",
        "TenkoAccountRoute",
        "TenkoAccountResponseState",
        "TenkoRateLimitEvent",
        "TenkoRateLimitSubjectState",
    }
)

# shortcut: 官方迁移器没有“禁止删表/删列/改列类型”的开关（0.4.2 复核过），这里补丁
# entari-plugin-database 0.4.2 的私有函数来落实 AGENTS.md 的“Schema 只前进”。
# 升级该依赖时必须复核这些私有名称和调用关系（test_migration_guard 会在它们
# 消失时失败）；官方提供等价选项后应改用选项并删除本补丁。
# 上游请求：https://github.com/ArcletProject/entari-plugin-database/issues/1
_GUARDED_NAMES = (
    "_execute_table_drops",
    "_normalize_alembic_ops",
    "_update_state_for_model",
    "_run_migration",
    "_load_state",
    "_save_state",
    "_resolve_engine",
)

# 本进程内被拒绝迁移的表；它们的 revision 不能被官方迁移器标记为已完成，
# 否则被回滚的加列等变更之后永远不会再被重试和报告。
_refused_tables: set[str] = set()


class ForwardOnlyMigrationError(RuntimeError):
    """迁移计划包含删表、删列或改列类型，已中止并回滚。"""


def _walk_ops(ops: Iterable[Any]) -> Iterable[tuple[str | None, Any]]:
    for op in ops:
        table = getattr(op, "table_name", None)
        if isinstance(op, ModifyTableOps):  # 其中嵌套了具体的列操作
            yield from _walk_ops(op.ops)
        else:
            yield table, op


def install_forward_only_guard() -> None:
    """让官方迁移器拒绝一切破坏性结构变更；重复调用无副作用。

    - 废弃表不再被 ``DROP TABLE``，表和迁移状态条目都保留；
    - 计划里出现 ``DropTableOp``、``DropColumnOp`` 或改列类型的
      ``AlterColumnOp`` 时，在迁移事务内抛错回滚该模块的结构迁移。
      新增表、可空列、索引等增量变更不受影响；
    - 唯一的例外是 ``RETIRED_TABLES``：每次迁移开始前删除（见 ``_drop_retired``）。
    """

    try:
        from alembic.operations.ops import AlterColumnOp, DropColumnOp, DropTableOp
        from entari_plugin_database import migration as official

        originals = {name: getattr(official, name) for name in _GUARDED_NAMES}
    except (ImportError, AttributeError, LookupError) as error:
        raise DatabaseUnavailableError(
            "官方数据库插件迁移器的私有接口已变化，无法启用 Schema 只前进保护"
        ) from error
    if getattr(originals["_normalize_alembic_ops"], "tenko_guard", False):
        return

    async def refuse_table_drops(
        obsolete_tables: Any, service: Any, state: Any
    ) -> None:
        del service, state
        logger.error(
            "Schema 只前进：拒绝删除已废弃的表 {}，表与迁移状态均已保留",
            sorted(obsolete_tables),
        )

    def guard_ops(ops_list: Iterable[Any]) -> Any:
        ops_list = list(ops_list)
        found = list(_walk_ops(ops_list))
        violations: list[str] = []
        for table, op in found:
            if isinstance(op, DropTableOp):
                violations.append(f"删除表 {op.table_name}")
            elif isinstance(op, DropColumnOp):
                violations.append(f"删除列 {table}.{op.column_name}")
            elif isinstance(op, AlterColumnOp) and op.modify_type is not None:
                violations.append(f"修改列类型 {table}.{op.column_name}")
        if violations:
            # 整个结构迁移事务都会回滚，其中涉及的表都不能被标记为已迁移。
            _refused_tables.update(table for table, _ in found if table)
            logger.error(
                "Schema 只前进：拒绝执行 {}，该模块的结构迁移已回滚",
                "、".join(violations),
            )
            raise ForwardOnlyMigrationError("；".join(violations))
        return originals["_normalize_alembic_ops"](ops_list)

    def update_state(state: Any, table_name: str, *args: Any, **kwargs: Any) -> None:
        if table_name not in _refused_tables:
            originals["_update_state_for_model"](state, table_name, *args, **kwargs)

    async def run_migration(service: Any) -> None:
        await _drop_retired(official, service)
        await originals["_run_migration"](service)

    # 在函数对象上打标记，重复安装时据此识别；函数没有声明这个属性。
    guard_ops.tenko_guard = True  # type: ignore[attr-defined]
    official._execute_table_drops = refuse_table_drops
    official._normalize_alembic_ops = guard_ops
    official._update_state_for_model = update_state
    official._run_migration = run_migration


async def _drop_retired(official: Any, service: Any) -> None:
    """删除主库里的退役表，并把它们从官方迁移状态里移除。

    失败只记日志：表和状态条目原样保留，下次启动重试，其余迁移照常进行。
    """

    def drop(connection: Any) -> list[str]:
        names = sorted(RETIRED_TABLES & set(inspect(connection).get_table_names()))
        if names:
            metadata = MetaData()
            metadata.reflect(connection, only=names)
            for name in names:
                metadata.tables[name].drop(connection)
        return names

    try:
        engine = official._resolve_engine(service, "")
        async with engine.begin() as connection:
            dropped = await connection.run_sync(drop)
    except Exception:
        logger.exception("删除退役的表失败，下次启动重试")
        return
    if dropped:
        logger.warning("已删除退役的表 {}（#234 起不再使用）", "、".join(dropped))
    state = official._load_state()
    stale = RETIRED_TABLES & set(state)
    if stale:
        for name in stale:
            del state[name]
        official._save_state(state)


async def run_database_migrations(service: Any) -> None:
    """执行官方 Alembic 迁移器。

    官方插件会先对当前模型与数据库做结构比对，再把 revision 写入其
    ``migrations_lock.json``。迁移前先安装“只前进”保护（生产路径由
    ``load_database_plugin`` 安装），它也负责删除 ``RETIRED_TABLES``。
    """

    try:
        from entari_plugin_database.migration import run_migration
    except (ImportError, AttributeError, LookupError) as error:
        raise DatabaseUnavailableError("官方数据库插件迁移器不可用") from error
    install_forward_only_guard()
    _refused_tables.clear()
    await run_migration(service)


__all__ = [
    "RETIRED_TABLES",
    "ForwardOnlyMigrationError",
    "install_forward_only_guard",
    "run_database_migrations",
]
