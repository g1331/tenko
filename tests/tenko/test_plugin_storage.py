"""第三方插件的独立存储：数据目录和带版本的 JSON Store。"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from external_plugin_support import entry, patch_entry_points

from tenko.host.plugins.storage import (
    PluginStorageError,
    StoreVersionError,
    clear_plugin_data,
    data_dir,
    prepare_plugin_db,
)

MODULE = "tenko_t_store"


def run_in_module(source: str, module: str = MODULE):
    """在名为 ``module`` 的模块全局里执行代码，模拟插件自己的模块调用存储接口。"""

    namespace = {"__name__": module}
    exec(compile(source, module, "exec"), namespace)
    return namespace


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    """工作目录切到临时目录，``store_demo`` 插件拥有 ``tenko_t_store`` 包。"""

    monkeypatch.chdir(tmp_path)
    patch_entry_points(monkeypatch, entry("store_demo", MODULE, version="1.0.0"))
    return tmp_path / ".tenko" / "plugin-data" / "store_demo"


# ---------------------------------------------------------------- data_dir


def test_data_dir_is_created_per_plugin_and_covers_submodules(plugin) -> None:
    source = (
        "from tenko_api.storage import data_dir\ndef where():\n    return data_dir()\n"
    )
    for module in (MODULE, f"{MODULE}.helpers.deep"):
        assert run_in_module(source, module)["where"]() == plugin
    assert plugin.is_dir()


def test_data_dir_rejects_callers_outside_external_plugins(plugin) -> None:
    # 本测试模块不属于任何第三方插件。
    with pytest.raises(PluginStorageError, match="tenko.plugins 入口"):
        data_dir()
    namespace = run_in_module(
        "from tenko_api.storage import data_dir\nwhere = lambda: data_dir()",
        "tenko_t_store_other",
    )
    with pytest.raises(PluginStorageError, match="tenko_t_store_other"):
        namespace["where"]()
    assert not plugin.exists()


def test_longest_entry_point_prefix_owns_the_module(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    patch_entry_points(monkeypatch, entry("outer", "pkg"), entry("inner", "pkg.inner"))
    source = "from tenko_api.storage import data_dir\ndef where():\n    return data_dir().name\n"
    assert run_in_module(source, "pkg.inner.mod")["where"]() == "inner"
    assert run_in_module(source, "pkg.other")["where"]() == "outer"
    with pytest.raises(PluginStorageError):
        run_in_module(source, "pkgx")["where"]()


# ------------------------------------------------------------------- Store


def make_store(source: str):
    return run_in_module(f"from tenko_api.storage import Store\n{source}")


def test_store_creates_loads_and_saves_atomically(plugin, monkeypatch) -> None:
    store = make_store('store = Store("settings", version=1)')["store"]
    assert store.path == plugin / "settings.json"
    assert store.load() is None
    assert store.load(default={}) == {}
    store.save({"数": [1, 2]})
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "version": 1,
        "data": {"数": [1, 2]},
    }
    assert store.load() == {"数": [1, 2]}
    assert not list(plugin.glob("*.tmp"))

    # 写入中途失败（模拟崩溃）：旧文件保持完整。
    def crash(*_args):
        raise OSError("磁盘错误")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        store.save({"数": "新"})
    monkeypatch.undo()
    assert store.load() == {"数": [1, 2]}


def test_store_migrates_older_file_after_backing_it_up(plugin) -> None:
    v1 = make_store('store = Store("n", version=1)')["store"]
    v1.save({"name": "a"})
    original = v1.path.read_text(encoding="utf-8")
    calls = []

    def migrate(old_version, data):
        calls.append((old_version, data))
        return {"names": [data["name"]]}

    store = run_in_module(
        "from tenko_api.storage import Store\n"
        "def build(migrate):\n    return Store('n', 2, migrate)\n"
    )["build"](migrate)

    assert store.load() == {"names": ["a"]}
    assert calls == [(1, {"name": "a"})]
    assert (plugin / "n.json.bak-v1").read_text(encoding="utf-8") == original
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 2
    # 已迁移后再次读取不会重复迁移。
    assert store.load() == {"names": ["a"]}
    assert len(calls) == 1


def test_store_failed_migration_keeps_original_file(plugin) -> None:
    make_store('store = Store("n", version=1)')["store"].save([1])
    original = (plugin / "n.json").read_text(encoding="utf-8")

    def broken(_old, _data):
        raise ValueError("迁移失败")

    build = run_in_module(
        "from tenko_api.storage import Store\n"
        "def build(migrate):\n    return Store('n', 2, migrate)\n"
    )["build"]
    with pytest.raises(ValueError, match="迁移失败"):
        build(broken).load()
    assert (plugin / "n.json").read_text(encoding="utf-8") == original


def test_store_refuses_newer_file_without_overwriting(plugin) -> None:
    make_store('store = Store("n", version=3)')["store"].save({"x": 1})
    original = (plugin / "n.json").read_text(encoding="utf-8")
    store = make_store('store = Store("n", version=2)')["store"]

    with pytest.raises(StoreVersionError, match="v3"):
        store.load()
    with pytest.raises(StoreVersionError):
        store.save({"x": 2})
    assert (plugin / "n.json").read_text(encoding="utf-8") == original


def test_store_requires_migrate_for_older_file_and_rejects_corruption(plugin) -> None:
    make_store('store = Store("n", version=1)')["store"].save(1)
    with pytest.raises(StoreVersionError, match="migrate"):
        make_store('store = Store("n", version=2)')["store"].load()

    (plugin / "bad.json").write_text("{坏的", encoding="utf-8")
    bad = make_store('store = Store("bad", version=1)')["store"]
    with pytest.raises(PluginStorageError, match="已损坏"):
        bad.load()
    with pytest.raises(PluginStorageError):
        bad.save({})
    assert (plugin / "bad.json").read_text(encoding="utf-8") == "{坏的"


@pytest.mark.parametrize("name", ["../x", "a/b", "", ".hidden"])
def test_store_rejects_unsafe_names(plugin, name) -> None:
    with pytest.raises(ValueError):
        make_store(f"store = Store({name!r}, version=1)")


# ------------------------------------------------------- 插件库备份与清除


def test_plugin_db_is_backed_up_when_the_version_changes_keeping_three(plugin) -> None:
    database = prepare_plugin_db("store_demo", "1.0.0")
    assert database == plugin / "data.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE t (v)")
        connection.execute("INSERT INTO t VALUES ('数据')")
    # 同一版本再次启动不备份。
    prepare_plugin_db("store_demo", "1.0.0")
    assert not (plugin / "backups").exists()

    for version in ("1.1.0", "1.2.0", "1.3.0", "1.4.0"):
        prepare_plugin_db("store_demo", version)

    backups = sorted((plugin / "backups").glob("*.db"))
    assert [b.name.split("-v")[1] for b in backups] == [
        "1.1.0.db",
        "1.2.0.db",
        "1.3.0.db",
    ]
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT v FROM t").fetchall() == [("数据",)]


def test_clear_plugin_data_removes_the_whole_directory(plugin) -> None:
    run_in_module("from tenko_api.storage import Store\nStore('n', 1).save(1)", MODULE)
    assert (plugin / "n.json").is_file()

    assert clear_plugin_data("store_demo") is True
    assert not plugin.exists()
    assert clear_plugin_data("store_demo") is False
    with pytest.raises(ValueError):
        clear_plugin_data("../x")
