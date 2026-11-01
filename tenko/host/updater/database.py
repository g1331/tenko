"""升级前的 SQLite 数据库备份辅助函数。"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path


from .models import UpgradeConfigError


DATABASE_BACKUPS_KEEP = 3
# 备份后至少还要剩下的空间，免得备份把磁盘写满、拖垮宿主和数据库写入。
DATABASE_BACKUP_FREE_MARGIN = 1 << 30


def resolve_sqlite_database_path(
    database_url: str | Path,
    project_root: str | Path,
) -> Path | None:
    """从 SQLAlchemy URL 解析可做文件备份的 SQLite 路径。

    ``:memory:`` 和 SQLite URI 没有稳定的单一文件目标，因此交由调用方按
    “不适用”处理。非 SQLite URL 同样返回 ``None``；升级控制面不应因为部署
    使用了其他数据库而伪造一份看似有效的本地备份。
    """

    if isinstance(database_url, Path):
        return database_url.expanduser().resolve()
    if not isinstance(database_url, str) or not database_url:
        raise UpgradeConfigError("database URL 必须是非空字符串或路径")
    try:
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError

        url = make_url(database_url)
    except (ArgumentError, ValueError) as exc:
        raise UpgradeConfigError(f"无法解析 database URL: {database_url!r}") from exc
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    path = Path(database).expanduser()
    root = Path(project_root).expanduser().resolve()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _remove_sqlite_temporary_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def backup_sqlite_database(source: str | Path, destination: str | Path) -> None:
    """使用 SQLite online backup API 生成一致的数据库快照。

    目标先写入同目录临时文件，关闭连接并刷盘后再原子替换；SQLite backup API
    会从源连接复制一致快照，WAL 中尚未合并的页面也会进入目标库，所以不需要
    另行复制源库的 ``-wal``/``-shm`` 旁车文件。
    """

    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(
        f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with sqlite3.connect(str(source_path)) as source_connection:
            with sqlite3.connect(str(temporary)) as destination_connection:
                source_connection.backup(destination_connection)
                destination_connection.commit()
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        os.replace(temporary, destination_path)
    finally:
        _remove_sqlite_temporary_files(temporary)
