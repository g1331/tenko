"""插件锁与安装流程：用本地 wheel 目录做索引（UV_FIND_LINKS），不访问网络。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from external_plugin_support import build_wheel

from tenko.host.plugins.manager import (
    PluginManager,
    PluginManagerError,
    parse_source,
)
from tenko.host.plugins.storage import prepare_plugin_db
from tenko.host.plugins.runtime import PluginRuntime
from tenko.host.updater import HealthCheckResult
from tenko_api import __version__ as API_VERSION

OK = HealthCheckResult(True, "")


def uv(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", *args], check=True, capture_output=True, text=True)


class Env:
    """临时虚拟环境加本地索引；虚拟环境通过 .pth 复用开发环境的宿主依赖。"""

    def __init__(self, root: Path) -> None:
        self.index = root / "index"
        self.index.mkdir()
        self.root = root
        uv("venv", str(root / "venv"), "--python", sys.executable)
        python = root / "venv" / "bin" / "python"
        site = subprocess.run(
            [python, "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        host_site = sysconfig_purelib()
        (Path(site) / "host.pth").write_text(host_site + "\n", encoding="utf-8")
        self.python = python
        self.wheel("tenko-api", API_VERSION)  # 宿主锁约束的 tenko-api 要能被解析到

    def wheel(self, dist: str, version: str, **kwargs) -> None:
        build_wheel(self.index, dist, version, **kwargs)

    def plugin(self, name: str, version: str = "1.0.0", **kwargs) -> None:
        self.wheel(
            f"tenko-plugin-{name}",
            version,
            plugin_id=name,
            requires=(f"tenko-api>={API_VERSION},<1",),
            **kwargs,
        )

    def freeze(self) -> str:
        return uv("pip", "freeze", "--python", str(self.python)).stdout

    def manager(self, *, real_import_check: bool = False) -> PluginManager:
        manager = PluginManager(
            python=self.python, runtime=PluginRuntime(), cwd=self.root
        )
        if not real_import_check:
            manager.import_check = lambda _ids: OK
        return manager


def sysconfig_purelib() -> str:
    import sysconfig

    return sysconfig.get_path("purelib")


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    monkeypatch.chdir(tmp_path)
    environment = Env(tmp_path)
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_FIND_LINKS", str(environment.index))
    return environment


def write_db(path: Path, value: str) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists t (v)")
        db.execute("delete from t")
        db.execute("insert into t values (?)", (value,))


def read_db(path: Path) -> str:
    with closing(sqlite3.connect(path)) as db:
        return db.execute("select v from t").fetchone()[0]


def lock_text(tmp_path: Path) -> str:
    path = tmp_path / ".tenko" / "plugins" / "plugins.lock"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_parse_source_requires_ref_and_rejects_credentials() -> None:
    assert parse_source("tenko-plugin-foo>=1").kind == "pypi"
    git = parse_source("https://github.com/o/r@v1.0.0#subdirectory=pkg")
    assert (git.kind, git.requirement) == (
        "git",
        "git+https://github.com/o/r@v1.0.0#subdirectory=pkg",
    )
    with pytest.raises(PluginManagerError, match="@tag"):
        parse_source("https://github.com/o/r")
    with pytest.raises(PluginManagerError, match="令牌"):
        parse_source("https://u:secret@github.com/o/r@v1")
    # 不在目录里的普通包名（比如打错的插件 ID）不能当成插件安装。
    with pytest.raises(PluginManagerError, match="tenko-plugin-"):
        parse_source("requests")


@pytest.mark.asyncio
async def test_install_pins_host_api_and_locks_plugin_only(env, tmp_path) -> None:
    env.plugin("foo")
    # 要求更高 tenko-api 的插件：不能靠换掉环境里的 tenko-api 来满足。
    env.wheel("tenko-plugin-bad", "1.0.0", plugin_id="bad", requires=("tenko-api>=99",))
    manager = env.manager()
    before = env.freeze()

    with pytest.raises(PluginManagerError, match="tenko-api") as raised:
        await manager.install(parse_source("tenko-plugin-bad"))
    assert raised.value.step == "解析依赖" and raised.value.restored is None
    assert env.freeze() == before and lock_text(tmp_path) == ""

    # 目录来源：解析出的哈希不在目录登记的范围内时停止，环境不动。
    pinned = replace(parse_source("tenko-plugin-foo"), trust="listed", pins=("0" * 64,))
    with pytest.raises(PluginManagerError, match="哈希"):
        await manager.install(pinned)
    assert env.freeze() == before

    change = await manager.install(parse_source("tenko-plugin-foo"), actor="1")
    lock = lock_text(tmp_path)
    assert (change.plugin_id, change.new_version) == ("foo", "1.0.0")
    assert "tenko-plugin-foo==1.0.0" in lock and "--hash=sha256:" in lock
    assert "tenko-api" not in lock  # 宿主的包不进插件锁
    record = manager.status()[0]
    assert (record.plugin_id, record.version, record.enabled) == ("foo", "1.0.0", False)


@pytest.mark.asyncio
async def test_resolve_refreshes_index_cache_for_target(env) -> None:
    # uv 缓存的索引页会让刚发布的版本“不存在”，安装和更新都要刷新目标包。
    env.plugin("foo")
    manager = env.manager()
    calls, real = [], manager._uv
    manager._uv = lambda step, *args, **kw: (
        calls.append(args) or real(step, *args, **kw)
    )

    await manager.install(parse_source("tenko-plugin-foo"))
    env.plugin("foo", "1.1.0")
    await manager.update("foo")

    compiles = [a for a in calls if a[:2] == ("pip", "compile")]
    assert len(compiles) == 2
    assert all(
        a[a.index("--refresh-package") + 1] == "tenko-plugin-foo" for a in compiles
    )


@pytest.mark.asyncio
async def test_failed_import_check_rolls_back_update(env, tmp_path) -> None:
    env.plugin("foo", "1.0.0")
    manager = env.manager(real_import_check=True)
    await manager.install(parse_source("tenko-plugin-foo"))
    locked, before = lock_text(tmp_path), env.freeze()

    env.plugin("foo", "1.1.0", source="raise RuntimeError('boom')\n")
    with pytest.raises(PluginManagerError, match="boom") as raised:
        await manager.update("foo")

    assert raised.value.step == "子进程导入检查" and raised.value.restored is True
    assert env.freeze() == before and lock_text(tmp_path) == locked
    assert manager.status()[0].version == "1.0.0"


@pytest.mark.asyncio
async def test_identity_mismatch_rolls_back_install(env, tmp_path) -> None:
    env.plugin("foo")
    manager = env.manager()
    before = env.freeze()

    source = parse_source("tenko-plugin-foo")
    source = type(source)(source.requirement, source.kind, expected_id="other")
    with pytest.raises(PluginManagerError, match="期望的 other") as raised:
        await manager.install(source)

    assert raised.value.restored is True
    assert env.freeze() == before and lock_text(tmp_path) == ""
    assert manager.status() == []


@pytest.mark.asyncio
async def test_uninstall_keeps_data_unless_cleared(env, tmp_path) -> None:
    for name in ("keep", "wipe"):
        env.plugin(name)
    manager = env.manager()
    for name in ("keep", "wipe"):
        await manager.install(parse_source(f"tenko-plugin-{name}"))
        data = tmp_path / ".tenko" / "plugin-data" / name
        data.mkdir(parents=True)
        (data / "data.db").write_bytes(b"x")

    kept = await manager.uninstall("keep")
    assert kept.data_cleared is None
    assert (tmp_path / ".tenko/plugin-data/keep/data.db").is_file()
    wiped = await manager.uninstall("wipe", clear_data=True)
    assert wiped.data_cleared is True
    assert not (tmp_path / ".tenko/plugin-data/wipe").exists()
    # 包留到重启：运行中的插件不能丢文件；启动检查在加载插件前删掉。
    assert manager.status() == [] and "tenko-plugin-keep" in env.freeze()
    assert manager.startup_check() == []
    assert "tenko-plugin" not in env.freeze()


@pytest.mark.asyncio
async def test_rollback_restores_previous_version_and_plugin_db(env, tmp_path) -> None:
    env.plugin("foo", "1.0.0")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"))
    prepare_plugin_db("foo", "1.0.0")
    write_db(tmp_path / ".tenko/plugin-data/foo/data.db", "v1")
    env.plugin("foo", "1.1.0")
    assert [c.new_version for c in await manager.update("foo")] == ["1.1.0"]
    prepare_plugin_db("foo", "1.1.0")  # 新版本启动：旧库备份，库被“迁移”
    write_db(tmp_path / ".tenko/plugin-data/foo/data.db", "v2")

    changes = await manager.rollback()

    assert [(c.old_version, c.new_version) for c in changes] == [("1.1.0", "1.0.0")]
    assert "tenko-plugin-foo==1.0.0" in env.freeze()
    assert manager.status()[0].version == "1.0.0"
    prepare_plugin_db("foo", "1.0.0")  # 旧版本重新启动
    assert read_db(tmp_path / ".tenko/plugin-data/foo/data.db") == "v1"
    # 历史是栈：再回滚一次撤销最初的安装，之后没有可回滚的了。
    assert [c.new_version for c in await manager.rollback()] == [None]
    assert manager.status() == [] and "tenko-plugin" not in env.freeze()
    with pytest.raises(PluginManagerError, match="没有可回滚"):
        await manager.rollback()


@pytest.mark.asyncio
async def test_sync_env_reinstalls_plugins_wiped_by_uv_sync(env) -> None:
    env.plugin("foo")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"))
    uv("pip", "uninstall", "--python", str(env.python), "tenko-plugin-foo")
    assert manager.drift() == ["tenko-plugin-foo==1.0.0"]

    assert await manager.sync_env() == ["tenko-plugin-foo==1.0.0"]

    assert manager.drift() == [] and "tenko-plugin-foo==1.0.0" in env.freeze()


@pytest.mark.asyncio
async def test_reconcile_after_host_upgrade_reapplies_and_checks_plugins(env) -> None:
    env.plugin("foo")
    env.wheel("tenko-api", "0.0.1")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"))
    uv("pip", "install", "--python", str(env.python), f"tenko-api=={API_VERSION}")

    uv("pip", "uninstall", "--python", str(env.python), "tenko-plugin-foo")
    await manager.reconcile()
    assert manager.drift() == []

    # 宿主依赖同步把 tenko-api 换成了插件不支持的版本：升级必须被拦下。
    uv("pip", "install", "--python", str(env.python), "tenko-api==0.0.1")
    with pytest.raises(PluginManagerError, match="兼容性检查"):
        await manager.reconcile()


@pytest.mark.parametrize("loaded_plugin", ["plugin_manager"], indirect=True)
def test_install_confirmation_only_accepts_same_user_in_same_channel(
    loaded_plugin,
) -> None:
    from types import SimpleNamespace as NS

    same_conversation = loaded_plugin.same_conversation

    def event(user: str, channel: str):
        return NS(user=NS(id=user), channel=NS(id=channel))

    origin = event("master", "group-1")
    assert same_conversation(origin, event("master", "group-1"))
    assert not same_conversation(origin, event("other", "group-1"))
    # 同一个 Master 在私聊里回复，不能确认群里发起的安装。
    assert not same_conversation(origin, event("master", "private-master"))


def test_drift_compares_git_sources_by_commit(env, tmp_path) -> None:
    lock = tmp_path / ".tenko/plugins/plugins.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"tenko-plugin-g @ git+https://h/o/r@{'a' * 40}\n")
    manager = env.manager()
    installed = {"tenko-plugin-g": f"@ git+https://h/o/r.git@{'a' * 40}"}
    manager._freeze = lambda: installed
    assert manager.drift() == []
    installed["tenko-plugin-g"] = f"@ git+https://h/o/r@{'b' * 40}"
    assert manager.drift() == ["tenko-plugin-g"]


@pytest.mark.asyncio
async def test_startup_finishes_lock_commit_interrupted_after_state(
    env, tmp_path
) -> None:
    env.plugin("foo")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"))
    locked = lock_text(tmp_path)
    lock = tmp_path / ".tenko/plugins/plugins.lock"
    # state.json 已写入新锁的哈希，替换锁之前进程退出。
    lock.rename(lock.with_name("plugins.lock.tmp"))
    lock.write_text("")

    assert manager.startup_check() == []
    assert lock_text(tmp_path) == locked

    lock.write_text("# edited by hand\n")
    assert "不一致" in manager.startup_check()[0]


@pytest.mark.asyncio
async def test_host_rollback_restores_plugin_lock_saved_at_upgrade(
    env, tmp_path
) -> None:
    env.plugin("foo")
    env.plugin("bar")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"))
    uv("pip", "install", "--python", str(env.python), f"tenko-api=={API_VERSION}")
    old_lock = lock_text(tmp_path)
    old, new = tmp_path / "v1", tmp_path / "v2"
    await manager.reconcile(new, leaving=old)  # 升级 v1 -> v2
    await manager.install(parse_source("tenko-plugin-bar"))

    await manager.reconcile(old, leaving=new, restore=True)  # 回滚 v2 -> v1

    assert lock_text(tmp_path) == old_lock
    assert [row.plugin_id for row in manager.status()] == ["foo"]
    assert "tenko-plugin-bar" not in env.freeze()


@pytest.mark.asyncio
async def test_precheck_reports_plugins_incompatible_with_new_host(
    env, tmp_path
) -> None:
    env.plugin("foo")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"))
    release = tmp_path / "release"
    api = release / "packages/tenko-api/src/tenko_api/__init__.py"
    api.parent.mkdir(parents=True)
    api.write_text('__version__ = "1.0.0"\n')
    env.wheel("tenko-api", "1.0.0")
    before = env.freeze()

    assert list(await manager.incompatible(release)) == ["foo"]  # 插件要求 <1
    assert env.freeze() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_plugin", ["plugin_manager"], indirect=True)
async def test_blocked_plugins_are_disabled_but_kept(
    loaded_plugin, tmp_path, monkeypatch
) -> None:
    from external_plugin_support import entry, patch_entry_points

    from tenko.host.plugins.registry import parse_index

    monkeypatch.chdir(tmp_path)
    patch_entry_points(monkeypatch, entry("evil", "tenko_t_evil"), entry("fine", "x"))
    runtime = PluginRuntime()
    runtime.write_state({"enabled": ["evil", "fine"]})
    index = parse_index(
        {
            "schema_version": 1,
            "plugins": {},
            "blocked": {"evil": {"reason": "恶意代码"}},
        }
    )

    stopped = await loaded_plugin.enforce_blocklist(index)

    assert stopped == ["<evil>：恶意代码"]
    assert runtime.read_state()["enabled"] == ["fine"]
    assert "evil" in {i.name for i in runtime.discover()}  # 只停用，不卸载


@pytest.mark.parametrize("loaded_plugin", ["plugin_manager"], indirect=True)
def test_plugin_detail_data_renders_with_template(loaded_plugin) -> None:
    from tenko.host.plugins.registry import parse_index
    from tenko.host.render import RenderService

    def version(v: str, api: str) -> dict:
        pin = {"sha256": ["a" * 64]}
        return {"version": v, "pin": pin, "requires_api": api, "valid": True}

    index = parse_index(
        {
            "schema_version": 1,
            "plugins": {
                "foo": {
                    "name": "Foo",
                    "tier": "official",
                    "source": {"type": "pypi", "package": "tenko-plugin-foo"},
                    "versions": [
                        version("0.2.0", ">=99"),  # 宿主装不了
                        version("0.1.0", f">={API_VERSION}"),
                    ],
                }
            },
        }
    )
    entry = index.find("foo")

    # 模板用 StrictUndefined，数据少一个键就会退回文字，这里直接渲染 HTML 兜住。
    html = RenderService()._render_html(
        "plugin_detail.html", loaded_plugin.detail_data(entry, "0.1.0")
    )

    assert "可安装 0.1.0" in html and "已是可安装的最新版本" in html


@pytest.mark.asyncio
async def test_suspend_keeps_record_and_resume_restores_it(env, tmp_path) -> None:
    env.plugin("foo")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"), actor="1")
    state = manager.runtime.read_state()
    manager.runtime.write_state({**state, "enabled": ["foo"]})
    installed_at = manager.status()[0].installed_at

    await manager.suspend({"foo": None}, reasons={"foo": "需要 tenko-api<0.4"})
    # 包和锁条目移出环境（包留到重启删除），记录和启用意愿留在 suspended 里。
    held = manager.suspended()["foo"]
    assert manager.status() == [] and "tenko-plugin-foo" not in lock_text(tmp_path)
    assert (held["enabled"], held["reason"]) == (True, "需要 tenko-api<0.4")
    assert manager.runtime.read_state()["enabled"] == []
    with pytest.raises(PluginManagerError, match="已停用"):
        await manager.install(parse_source("tenko-plugin-foo"))
    manager.startup_check()

    env.plugin("foo", "1.1.0")
    change = await manager.resume("foo", parse_source("tenko-plugin-foo==1.1.0"))
    assert (change.old_version, change.new_version) == ("1.0.0", "1.1.0")
    row = manager.status()[0]
    assert (row.version, row.enabled, row.installed_at) == ("1.1.0", True, installed_at)
    assert manager.suspended() == {}


@pytest.mark.asyncio
async def test_startup_resumes_chosen_version_or_keeps_plugin_suspended(env) -> None:
    for name in ("good", "gone"):
        env.plugin(name)
    manager = env.manager()
    for name in ("good", "gone"):
        await manager.install(parse_source(f"tenko-plugin-{name}"))
    env.plugin("good", "2.0.0")
    await manager.suspend(
        {
            "good": parse_source("tenko-plugin-good==2.0.0"),
            "gone": parse_source("tenko-plugin-gone==9.9.9"),  # 索引里没有
        }
    )
    manager.startup_check()

    assert manager.resume_pending() == ["good"]
    assert [(r.plugin_id, r.version) for r in manager.status()] == [("good", "2.0.0")]
    # 装不回的保持停用，记下原因，不再每次启动重试。
    held = manager.suspended()["gone"]
    assert held["resume"] is None and "9.9.9" in held["error"]
    assert manager.resume_pending() == []


@pytest.mark.asyncio
async def test_rollback_undoes_suspend(env) -> None:
    # 停用后宿主没升级成，撤销停用要恢复记录和启用状态。
    env.plugin("foo")
    manager = env.manager()
    await manager.install(parse_source("tenko-plugin-foo"))
    manager.runtime.write_state({**manager.runtime.read_state(), "enabled": ["foo"]})

    await manager.suspend({"foo": None})
    await manager.rollback()

    assert manager.suspended() == {}
    assert [(r.plugin_id, r.enabled) for r in manager.status()] == [("foo", True)]
    assert manager.startup_check() == [] and "tenko-plugin-foo" in env.freeze()
