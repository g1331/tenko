"""Tenko 对官方 database 插件迁移器的窄适配。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from alembic.operations.ops import ModifyTableOps
from loguru import logger

from .errors import DatabaseUnavailableError

LEGACY_SCHEMA_REVISION = "tenko-g1-legacy-schema-v1"

# shortcut: 官方迁移器没有“禁止删表/删列/改列类型”的开关（0.4.2 复核过），这里补丁
# entari-plugin-database 0.4.2 的私有函数来落实 AGENTS.md 的“Schema 只前进”。
# 升级该依赖时必须复核这些私有名称和调用关系（test_migration_guard 会在它们
# 消失时失败）；官方提供等价选项后应改用选项并删除本补丁。
# 上游请求：https://github.com/ArcletProject/entari-plugin-database/issues/1
_GUARDED_NAMES = (
    "_execute_table_drops",
    "_normalize_alembic_ops",
    "_update_state_for_model",
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
      新增表、可空列、索引等增量变更不受影响。
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

    async def refuse_table_drops(obsolete_tables, service, state) -> None:
        del service, state
        logger.error(
            "Schema 只前进：拒绝删除已废弃的表 {}，表与迁移状态均已保留",
            sorted(obsolete_tables),
        )

    def guard_ops(ops_list):
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

    def update_state(state, table_name, *args, **kwargs):
        if table_name not in _refused_tables:
            originals["_update_state_for_model"](state, table_name, *args, **kwargs)

    guard_ops.tenko_guard = True  # type: ignore[attr-defined]
    official._execute_table_drops = refuse_table_drops
    official._normalize_alembic_ops = guard_ops
    official._update_state_for_model = update_state


async def run_database_migrations(service: Any) -> None:
    """执行官方 Alembic 迁移器，并保留旧 schema 的 baseline 语义。

    官方插件会先对当前模型与数据库做结构比对，再把 revision 写入其
    ``migrations_lock.json``。Tenko 模型声明 ``LEGACY_SCHEMA_REVISION``，旧
    库结构完全一致时不会产生 DDL，只会完成 baseline 记录。迁移前先安装
    “只前进”保护（生产路径由 ``load_database_plugin`` 安装）。
    """

    try:
        from entari_plugin_database.migration import run_migration
    except (ImportError, AttributeError, LookupError) as error:
        raise DatabaseUnavailableError("官方数据库插件迁移器不可用") from error
    install_forward_only_guard()
    _refused_tables.clear()
    await run_migration(service)


__all__ = [
    "LEGACY_SCHEMA_REVISION",
    "ForwardOnlyMigrationError",
    "install_forward_only_guard",
    "run_database_migrations",
]
