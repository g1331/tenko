"""扩展插件的独立数据存储：数据目录和带版本的 JSON 存储。

所有入口按调用方（或模型）所在的模块归属到插件 ID，规则见
:func:`tenko.host.plugins.runtime.external_plugin_for_module`：模块必须属于某个
``tenko.plugins`` 入口声明的包，否则抛出 :class:`PluginStorageError`。

沙盒处理链里（#235）读写的是数据副本 ``.tenko/sandbox-data/<id>/``：插件第一次在
沙盒里访问存储时复制整个数据目录（SQLite 库用在线备份取一致快照），之后沙盒里的
读写都落在副本上，真实数据不受影响。沙盒面板的“重置数据”删除副本。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from ..sandbox import origin as sandbox_origin
from .runtime import _PLUGIN_ID, external_plugin_for_module

# 与 .tenko/plugins/state.json 一样相对工作目录；测试可以替换它。
PLUGIN_DATA_ROOT = Path(".tenko/plugin-data")
SANDBOX_DATA_ROOT = Path(".tenko/sandbox-data")
_STORE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
# 插件库在版本变化时的备份保留份数。
DB_BACKUPS_KEEP = 3


class PluginStorageError(RuntimeError):
    """无法确定调用方插件，或存储文件不可用。"""


class PluginMigrationError(PluginStorageError):
    """运行中加载或热更新后，插件库迁移失败；此时插件的新代码已经在运行。"""


class StoreVersionError(PluginStorageError):
    """存储文件的版本无法被当前插件处理；文件保持原样。"""


def _plugin_of(module_name: str) -> str:
    found = external_plugin_for_module(module_name)
    if found is None:
        raise PluginStorageError(
            f"无法确定 {module_name!r} 所属的扩展插件：存储接口只能在插件自己的"
            "模块里调用，且包必须在 pyproject 的 tenko.plugins 入口中声明"
        )
    return found


def _caller_module(depth: int) -> str:
    return sys._getframe(depth + 1).f_globals.get("__name__", "")


def _real_dir(plugin_id: str) -> Path:
    path = (PLUGIN_DATA_ROOT / plugin_id).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backup_sqlite(source: Path, target: Path) -> None:
    # 在线备份而不是复制文件：库可能正被插件写着，WAL 里的内容也要带上。
    with closing(sqlite3.connect(source)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


def sandbox_dir(plugin_id: str) -> Path:
    """插件在沙盒里的数据副本；第一次访问时从真实数据目录复制。"""

    path = (SANDBOX_DATA_ROOT / plugin_id).resolve()
    if path.is_dir():
        return path
    real = PLUGIN_DATA_ROOT / plugin_id
    staging = path.with_name(f"{path.name}.copying")
    shutil.rmtree(staging, ignore_errors=True)
    if real.is_dir():
        shutil.copytree(
            real, staging, ignore=shutil.ignore_patterns("data.db*", "backups")
        )
        if (real / "data.db").is_file():
            _backup_sqlite(real / "data.db", staging / "data.db")
    else:
        staging.mkdir(parents=True)
    staging.rename(path)
    return path


def _data_dir(plugin_id: str) -> Path:
    return sandbox_dir(plugin_id) if sandbox_origin() else _real_dir(plugin_id)


async def reset_sandbox_data() -> bool:
    """删除所有插件的沙盒数据副本，下次在沙盒里访问时重新复制。"""

    # 只有用到插件库时才会导入 database 模块；导入了就先关掉指向副本的连接。
    database = sys.modules.get("tenko.host.plugins.database")
    if database is not None:
        await database.close_sandbox_engines()
    if not SANDBOX_DATA_ROOT.is_dir():
        return False
    shutil.rmtree(SANDBOX_DATA_ROOT)
    return True


def data_dir() -> Path:
    """返回调用方插件的数据目录 ``.tenko/plugin-data/<id>/``，不存在时创建。"""

    return _data_dir(_plugin_of(_caller_module(1)))


def prepare_plugin_db(plugin_id: str, version: str) -> Path:
    """返回插件库 ``data.db`` 的路径；插件版本较上次启动变化时先备份已有的库。

    必须在库被打开或迁移之前调用。备份放在 ``backups/<时间>-v<旧版本>.db``，只保留
    最近 ``DB_BACKUPS_KEEP`` 份；上次启动的版本记在 ``db-version``，备份成功后才更新，
    所以备份失败时下次启动会重试。
    """

    folder = _real_dir(plugin_id)
    database, marker = folder / "data.db", folder / "db-version"
    last = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    if last == version:
        return database
    if database.is_file():
        backups = folder / "backups"
        backups.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        target = backups / f"{stamp}-v{last or 'unknown'}.db"
        # 用 SQLite 的在线备份而不是复制文件，异常退出遗留的 WAL 也会被一并带上。
        _backup_sqlite(database, target)
        _restore_requested_backup(folder, database, version)
        for stale in sorted(backups.glob("*.db"))[:-DB_BACKUPS_KEEP]:
            stale.unlink()
        logger.info(
            "插件 {} 的版本从 {} 变为 {}，数据库已备份为 {}",
            plugin_id,
            last or "未知",
            version,
            target.name,
        )
    marker.write_text(version + "\n", encoding="utf-8")
    return database


def request_db_restore(plugin_id: str, version: str) -> bool:
    """插件回滚到 ``version`` 时调用：下次启动该版本时，用升级前备份的库覆盖当前库。

    回滚后的旧版本不应该读新版本迁移过的库。真正的还原发生在
    :func:`prepare_plugin_db`（库尚未被打开），当前库会先备份。目录不存在时返回 ``False``。
    """

    folder = PLUGIN_DATA_ROOT / plugin_id
    if not folder.is_dir():
        return False
    (folder / "restore-db-to").write_text(version + "\n", encoding="utf-8")
    return True


def _restore_requested_backup(folder: Path, database: Path, version: str) -> None:
    request = folder / "restore-db-to"
    if not request.is_file():
        return
    wanted = request.read_text(encoding="utf-8").strip()
    request.unlink()
    saved = sorted((folder / "backups").glob(f"*-v{wanted}.db"))
    if wanted != version or not saved:
        return
    for suffix in ("-wal", "-shm"):
        database.with_name(database.name + suffix).unlink(missing_ok=True)
    shutil.copy2(saved[-1], database)
    logger.info("插件数据库已还原为 v{} 时的备份 {}", version, saved[-1].name)


def clear_plugin_data(plugin_id: str) -> bool:
    """删除插件的整个数据目录（JSON 存储、数据库和备份），返回目录原先是否存在。

    调用方负责确认插件当前没有被加载：已加载插件的数据库连接仍然打开着。
    """

    if not _PLUGIN_ID.fullmatch(plugin_id):
        raise ValueError(f"非法的插件 ID: {plugin_id!r}")
    path = PLUGIN_DATA_ROOT / plugin_id
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True


class Store:
    """插件数据目录里带版本号的 JSON 文件。

    文件内容是 ``{"version": N, "data": ...}``。同步读写：数据应当很小，并且
    读改写之间没有 ``await``，所以多个协程不会交错。

    - 版本一致：直接读取；文件不存在时 ``load()`` 返回 ``default``。
    - 文件版本较旧：先把原文件备份为 ``<name>.json.bak-v<旧版本>``，再调用
      ``migrate(旧版本, 数据) -> 新数据`` 并写回；没有 ``migrate`` 时抛错。
    - 文件版本比声明的新（例如插件被降级）：抛 :class:`StoreVersionError`，
      不读取也不覆盖。
    """

    def __init__(
        self,
        name: str,
        version: int,
        migrate: Callable[[int, Any], Any] | None = None,
    ) -> None:
        if not _STORE_NAME.fullmatch(name):
            raise ValueError(
                f"Store 名称只能包含字母、数字、下划线、点和连字符: {name!r}"
            )
        if type(version) is not int or version < 1:
            raise ValueError("version 必须是正整数")
        self.version = version
        self.migrate = migrate
        self.name = name
        self.plugin_id = _plugin_of(_caller_module(1))
        _real_dir(self.plugin_id)

    @property
    def path(self) -> Path:
        """每次访问时解析：同一个 Store 在沙盒里读写的是数据副本。"""

        return _data_dir(self.plugin_id) / f"{self.name}.json"

    def _read(self) -> tuple[int, Any] | None:
        """读取并校验文件；缺失返回 ``None``，损坏或版本更新时抛错且不改文件。"""

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            stored, data = raw["version"], raw["data"]
            if type(stored) is not int:
                raise TypeError("version 必须是整数")
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PluginStorageError(
                f"存储文件 {self.path} 已损坏，未做任何修改: {error}"
            ) from error
        if stored > self.version:
            raise StoreVersionError(
                f"{self.path} 的版本 v{stored} 比插件声明的 v{self.version} 新，"
                "已拒绝读写；请升级插件，不要降级"
            )
        return stored, data

    def load(self, default: Any = None) -> Any:
        """读取数据；文件不存在时返回 ``default``。"""

        found = self._read()
        if found is None:
            return default
        stored, data = found
        if stored == self.version:
            return data
        if self.migrate is None:
            raise StoreVersionError(
                f"{self.path} 的版本 v{stored} 低于 v{self.version}，且没有提供 migrate"
            )
        backup = self.path.with_name(f"{self.path.name}.bak-v{stored}")
        shutil.copy2(self.path, backup)
        data = self.migrate(stored, data)
        self.save(data)
        logger.info(
            "存储 {} 已从 v{} 迁移到 v{}，原文件备份为 {}",
            self.path,
            stored,
            self.version,
            backup.name,
        )
        return data

    def save(self, data: Any) -> None:
        """原子写入：先写同目录临时文件再 ``os.replace``。

        已有文件损坏或版本比声明的新时抛错，避免覆盖无法处理的数据。
        """

        self._read()
        text = json.dumps({"version": self.version, "data": data}, ensure_ascii=False)
        temp = self.path.with_name(f"{self.path.name}.tmp")
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(temp, self.path)
