"""扩展插件的安装、更新、卸载与回滚（设计见 issue #200 第 4 节）。

共享虚拟环境 = 宿主锁（``uv.lock``）∪ 插件锁（``.tenko/plugins/plugins.lock``）。

- ``plugins.lock``：``uv pip compile --generate-hashes`` 的输出（精确版本加哈希，Git 来源
  固定到提交号），已去掉宿主锁里的包，所以它只描述插件自己的包。
- ``state.json`` 的 ``plugins`` 节：每个插件的分发名、版本、来源、哈希或提交号、安装人和
  时间。启用状态仍是同文件的 ``enabled``，由 :class:`PluginRuntime` 管理。

解析时把宿主锁和宿主实际的 ``tenko-api`` 版本作为约束，插件不能改动宿主已有包的版本；
``uv pip`` 自己遇到不兼容的 ``tenko-api`` 范围不会拒绝，而是替换环境里的 ``tenko-api``，
所以必须靠约束让解析器失败。

所有变更（安装、更新、卸载、回滚）走同一个事务：解析（环境未动）→ 备份 → 应用 → 校验 →
提交。应用或校验失败时按备份把环境恢复到原状。操作在重启后才生效，不做热加载；卸载连包也
留到重启时（加载插件之前）再删，运行中的插件不会丢文件。

不兼容新宿主的插件可以“停用并保留记录”（``state.json`` 的 ``suspended`` 节）：包和锁条目
移出环境，安装记录、启用意愿、数据和配置都留下；之后装上兼容版本即恢复原样，也可以指定
一个版本，在进入新宿主的下次启动时自动装回。

``state.json`` 是提交点：它记录插件锁的哈希，锁先写到临时文件，``state.json`` 写完后再
替换。两步之间中断时，启动检查会用临时文件补完提交。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from loguru import logger
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from . import storage as plugin_storage
from .runtime import _MODULE_PATH, _PLUGIN_ID, PluginInfo, PluginRuntime
from ..updater import (
    AuditLogger,
    DefaultHealthChecker,
    DependencySyncError,
    HealthCheckResult,
    _redact_text,
    find_uv,
    get_upgrade_manager,
    tenko_api_version,
)

LOCK_NAME = "plugins.lock"
HISTORY_KEEP = 5
_UV_TIMEOUT = 600
_PROCESS_START = datetime.now(UTC).isoformat()
# 运行中加载的扩展插件及加载时间：之后的变更才需要重启。
_live_loaded: dict[str, str] = {}
_LOCK_HEADER = (
    "# Tenko 插件锁：由 uv pip compile 生成，已排除宿主锁里的包，请勿手工编辑。\n"
    "# 恢复环境：/同步插件环境 或 python -m tenko plugins sync\n"
)
_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<op>==|@)\s*(?P<value>\S+)"
)
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
_COMMIT = re.compile(r"@([0-9a-f]{40})(?![0-9a-f])")
_BACKTICKED = re.compile(r"`([A-Za-z0-9][A-Za-z0-9._-]*)")
# 在目标环境的解释器里列出全部 tenko.plugins 入口；-I 避免工作目录里的同名包干扰。
_ENTRIES_SCRIPT = """
import json
from importlib.metadata import entry_points
rows = []
for e in entry_points(group="tenko.plugins"):
    commit = None
    try:
        direct = json.loads(e.dist.read_text("direct_url.json") or "null")
        commit = ((direct or {}).get("vcs_info") or {}).get("commit_id")
    except Exception:
        pass
    rows.append({"id": e.name, "module": e.value, "dist": e.dist.name,
                 "version": e.dist.version, "commit": commit})
print(json.dumps(rows))
""".strip()

SourceKind = Literal["pypi", "git"]
_PLUGIN_PREFIXES = ("tenko-plugin-", "entari-plugin-")
Trust = Literal["official", "listed", "unlisted"]


class PluginManagerError(RuntimeError):
    """插件管理操作失败。

    ``step`` 是失败的步骤；``restored`` 为 ``None`` 表示失败时环境还没有被改动，
    ``True`` 表示已按备份恢复，``False`` 表示恢复也失败了（需要 /同步插件环境）。
    """

    def __init__(self, step: str, detail: str, *, restored: bool | None = None) -> None:
        super().__init__(f"{step}失败：{detail}")
        self.step = step
        self.detail = detail
        self.restored = restored

    def __str__(self) -> str:
        suffix = {
            None: "",
            True: "\n环境已恢复到操作之前的状态。",
            False: "\n环境恢复失败，请执行 /同步插件环境。",
        }[self.restored]
        return super().__str__() + suffix


@dataclass(frozen=True, slots=True)
class PluginSource:
    """一次安装的来源。

    插件目录的条目由 :func:`tenko.host.plugins.registry.source_for` 转成同样的结构：``requirement``
    固定到版本或提交号，``trust`` 取目录的等级，``expected_id`` 是目录登记的插件 ID，
    ``pins`` 是目录登记的文件哈希（解析出的哈希必须都在其中）。
    """

    requirement: str
    kind: SourceKind
    trust: Trust = "unlisted"
    expected_id: str | None = None
    pins: tuple[str, ...] = ()


def parse_source(spec: str) -> PluginSource:
    """把用户输入解析成来源：PyPI 包名（可带版本范围）或带 tag/提交号的 Git URL。"""

    text = spec.strip()
    if not text:
        raise PluginManagerError("解析来源", "来源不能为空")
    if "://" in text.split("#", 1)[0]:
        return _parse_git(text)
    try:
        requirement = Requirement(text)
    except InvalidRequirement as error:
        raise PluginManagerError(
            "解析来源", f"不是有效的包名或 Git URL: {text}（{error}）"
        ) from error
    if requirement.url:
        raise PluginManagerError(
            "解析来源", "只接受 PyPI 包名或 Git URL，不接受其他直接链接"
        )
    # 防止插件 ID 打错或目录不可用时，把同名的无关 PyPI 包当成插件装上（安装即执行代码）。
    if not canonicalize_name(requirement.name).startswith(_PLUGIN_PREFIXES):
        raise PluginManagerError(
            "解析来源",
            f"{requirement.name} 不在插件目录里，且不是 tenko-plugin-* 或 "
            "entari-plugin-* 包；请检查插件 ID 或包名",
        )
    return PluginSource(str(requirement), "pypi")


def _parse_git(text: str) -> PluginSource:
    base, _, fragment = text.partition("#")
    base = base.removeprefix("git+")
    head, _, last = base.rpartition("/")
    name, _, ref = last.partition("@")
    parts = urlsplit(base)
    if parts.scheme not in {"https", "http", "ssh", "file"} or not head:
        raise PluginManagerError("解析来源", f"不支持的 Git URL: {_redact_text(text)}")
    if parts.password:
        raise PluginManagerError("解析来源", "Git URL 里不能带密码或令牌")
    if not ref:
        raise PluginManagerError(
            "解析来源",
            "Git URL 必须带 @tag 或 @提交号，例如 https://github.com/o/r@v1.0.0",
        )
    requirement = f"git+{head}/{name}@{ref}" + (f"#{fragment}" if fragment else "")
    return PluginSource(requirement, "git")


@dataclass(frozen=True, slots=True)
class PluginChange:
    """一次操作对单个插件的影响；版本为 ``None`` 表示此前未安装或此后已移除。"""

    action: str
    plugin_id: str
    dist: str
    old_version: str | None
    new_version: str | None
    data_cleared: bool | None = None


@dataclass(frozen=True, slots=True)
class PluginStatus:
    """已安装插件的状态，供 /插件列表 和 WebUI 使用。"""

    plugin_id: str
    dist: str
    version: str
    source: str
    trust: str
    requirement: str
    enabled: bool
    loaded: bool
    restart_required: bool
    installed_at: str
    updated_at: str


def _lock_entries(text: str) -> dict[str, str]:
    """把插件锁拆成 ``规范化包名 -> 条目文本``（含续行的哈希）。"""

    entries: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            if current is not None:
                current.append(line)
            continue
        matched = _PIN.match(line)
        if matched is None:
            raise PluginManagerError("读取插件锁", f"无法识别的条目: {line!r}")
        current = entries.setdefault(canonicalize_name(matched["name"]), [])
        current.append(line)
    return {name: "\n".join(lines) for name, lines in entries.items()}


def _render_lock(entries: Mapping[str, str]) -> str:
    body = "\n".join(entries[name] for name in sorted(entries))
    return _LOCK_HEADER + (body + "\n" if body else "")


def _pin(block: str) -> re.Match[str]:
    matched = _PIN.match(block)
    assert matched is not None  # _lock_entries 已经校验过
    return matched


def _records(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    plugins = state.get("plugins")
    return {k: dict(v) for k, v in plugins.items()} if isinstance(plugins, dict) else {}


def _suspended(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    found = state.get("suspended")
    return {k: dict(v) for k, v in found.items()} if isinstance(found, dict) else {}


def _source_of(data: Mapping[str, Any]) -> PluginSource:
    return PluginSource(
        data["requirement"],
        data["kind"],
        data.get("trust", "unlisted"),
        data.get("expected_id"),
        tuple(data.get("pins") or ()),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _commit_of(pin: str) -> str:
    """Git 来源比较提交号；``uv pip freeze`` 和插件锁里 URL 的写法可能不同。"""

    return m.group(1) if (m := _COMMIT.search(pin)) else pin


class PluginManager:
    """插件锁与共享虚拟环境的管理器；公开方法都可由聊天命令或 WebUI 直接调用。"""

    def __init__(
        self,
        *,
        python: str | Path | None = None,
        uv: str | None = None,
        runtime: PluginRuntime | None = None,
        host_root: str | Path | None = None,
        cwd: str | Path | None = None,
        audit: AuditLogger | None = None,
        import_check: Callable[[Sequence[str]], HealthCheckResult] | None = None,
    ) -> None:
        self.python = str(python or sys.executable)
        self.uv = uv
        self.runtime = runtime or PluginRuntime()
        # 宿主锁取自正在运行的这份代码，升级后随版本目录变化。
        self.host_root = Path(host_root or Path(__file__).resolve().parents[3])
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.audit = audit or AuditLogger(Path(".tenko/upgrades/audit.jsonl"))
        self.import_check = import_check or self._default_import_check
        self._busy = threading.Lock()
        # 本进程加载插件时的插件锁；之后只新增过包时，新启用的插件可以直接加载。
        self._startup_lock: dict[str, str] | None = None

    # ----------------------------------------------------------------- 文件

    @property
    def plugin_dir(self) -> Path:
        return self.runtime.state_file.parent

    @property
    def lock_file(self) -> Path:
        return self.plugin_dir / LOCK_NAME

    @property
    def history_dir(self) -> Path:
        return self.plugin_dir / "history"

    def _read_lock(self) -> str:
        try:
            return self.lock_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    @property
    def _staged_lock(self) -> Path:
        return self.lock_file.with_name(f"{LOCK_NAME}.tmp")

    def _commit(self, lock: str, state: dict[str, Any]) -> None:
        """提交插件锁和状态：临时锁 → ``state.json``（提交点）→ 替换锁。"""

        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self._staged_lock.write_text(lock, encoding="utf-8")
        state["lock_sha256"] = _sha(lock)
        self.runtime.write_state(state)
        os.replace(self._staged_lock, self.lock_file)

    def _repair_lock(self) -> str | None:
        """锁与 ``state.json`` 记录的哈希不符时补完上次中断的提交；补不了时返回说明。"""

        want = self.runtime.read_state().get("lock_sha256")
        if want is None or _sha(self._read_lock()) == want:
            self._staged_lock.unlink(missing_ok=True)  # 提交点之前中断：丢弃
            return None
        if self._staged_lock.is_file() and (
            _sha(self._staged_lock.read_text(encoding="utf-8")) == want
        ):
            os.replace(self._staged_lock, self.lock_file)
            logger.warning("插件锁上次没有写完，已按 state.json 补完")
            return None
        return (
            "plugins.lock 与 state.json 记录的不一致（可能被手工修改过），"
            "插件记录可能与环境不符；请用 /回滚插件 或重新安装相关插件"
        )

    # ------------------------------------------------------------------ uv

    def _uv(
        self, step: str, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess:
        try:
            command = [find_uv(self.uv), *args]
        except DependencySyncError as error:
            raise PluginManagerError(step, str(error)) from error
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=_UV_TIMEOUT,
                cwd=self.cwd,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PluginManagerError(step, f"uv 无法执行: {error}") from error
        if check and completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            raise PluginManagerError(
                step, f"uv 返回 {completed.returncode}: {_redact_text(output)[-800:]}"
            )
        return completed

    def _freeze(self) -> dict[str, str]:
        """环境里已安装的包：规范化包名 -> ``==版本`` 或 ``@ 链接``。"""

        out = self._uv("读取环境", "pip", "freeze", "--python", self.python).stdout
        installed: dict[str, str] = {}
        for line in out.splitlines():
            if matched := _PIN.match(line.strip()):
                installed[canonicalize_name(matched["name"])] = (
                    f"{matched['op']}{matched['value']}"
                )
        return installed

    def _host_constraints(
        self, directory: Path, host_root: Path | None = None
    ) -> tuple[Path, frozenset[str]]:
        """宿主锁加上宿主实际的 tenko-api 版本，作为解析约束。"""

        from tenko_api import __version__ as api_version

        if host_root is None:
            host_root = self.host_root
        else:  # 预检新版本：用新版本自带的 tenko-api 版本
            api_version = tenko_api_version(host_root) or api_version
        lines: list[str] = []
        if (host_root / "uv.lock").is_file():
            exported = self._uv(
                "读取宿主锁",
                "export",
                "--frozen",
                "--no-hashes",
                "--no-emit-workspace",
                "--no-emit-project",
                "--no-annotate",
                "--no-header",
                "--project",
                str(host_root),
            ).stdout
            lines = [
                line
                for line in exported.splitlines()
                if (m := _PIN.match(line))
                and canonicalize_name(m["name"]) != "tenko-api"
            ]
        else:
            logger.warning("{} 没有 uv.lock，插件解析只约束 tenko-api", host_root)
        lines.append(f"tenko-api=={api_version}")
        names = frozenset(
            canonicalize_name(m["name"]) for line in lines if (m := _PIN.match(line))
        )
        path = directory / "host-constraints.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path, names

    def _compile(
        self,
        requirements: Sequence[str],
        previous_lock: str,
        *,
        upgrade: Sequence[str] = (),
        refresh: Sequence[str] = (),
        host_root: Path | None = None,
    ) -> str:
        """解析出完整的插件锁（环境不受影响）；以现有锁为偏好，未被要求升级的包不变。"""

        if not requirements:
            return _render_lock({})
        with tempfile.TemporaryDirectory(prefix="tenko-plugins-") as work:
            directory = Path(work)
            constraints, host_names = self._host_constraints(directory, host_root)
            source, output = directory / "requirements.in", directory / LOCK_NAME
            source.write_text("\n".join(requirements) + "\n", encoding="utf-8")
            output.write_text(previous_lock, encoding="utf-8")
            args = [
                "pip", "compile", str(source), "-c", str(constraints),
                "--generate-hashes", "--no-header", "--no-annotate",
                "--python", self.python, "-o", str(output),
            ]  # fmt: skip
            for name in upgrade:
                args += ["--upgrade-package", name]
            # uv 会缓存索引页，不刷新就看不到刚发布的版本。
            for name in dict.fromkeys([*upgrade, *refresh]):
                args += ["--refresh-package", name]
            self._uv("解析依赖", *args)
            entries = _lock_entries(output.read_text(encoding="utf-8"))
        return _render_lock(
            {n: block for n, block in entries.items() if n not in host_names}
        )

    def _apply(self, old_lock: str, new_lock: str) -> None:
        """让环境符合 ``new_lock``：先卸载只在旧锁里的包（孤立依赖），再按锁安装。"""

        old, new = _lock_entries(old_lock), _lock_entries(new_lock)
        if orphans := sorted(set(old) - set(new)):
            self._uv("卸载旧包", "pip", "uninstall", "--python", self.python, *orphans)
        if new:
            with tempfile.TemporaryDirectory(prefix="tenko-plugins-") as work:
                path = Path(work) / LOCK_NAME
                path.write_text(new_lock, encoding="utf-8")
                self._uv(
                    "安装",
                    "pip", "install", "--python", self.python,
                    "-r", str(path), "--no-deps",
                )  # fmt: skip

    def _drift(self, lock_text: str) -> list[str]:
        """锁里有、但环境里缺失或版本不符的包。"""

        entries = _lock_entries(lock_text)
        if not entries:
            return []
        installed = self._freeze()
        bad = []
        for name, block in entries.items():
            pin = _pin(block)
            have, want = installed.get(name), f"{pin['op']}{pin['value']}"
            if have is None or (
                have != want
                if pin["op"] == "=="
                else _commit_of(have) != _commit_of(want)
            ):
                bad.append(
                    f"{name}{pin['op']}{pin['value']}" if pin["op"] == "==" else name
                )
        return bad

    def drift(self) -> list[str]:
        """环境与插件锁不一致的包；没有插件锁时不调用 uv，直接返回空。"""

        return self._drift(self._read_lock())

    def _entries(self) -> list[dict[str, Any]]:
        """用目标环境的解释器读取 ``tenko.plugins`` 入口，不依赖本进程的缓存。"""

        try:
            completed = subprocess.run(
                [self.python, "-I", "-c", _ENTRIES_SCRIPT],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.cwd,
            )
            if completed.returncode != 0:
                raise OSError(completed.stderr.strip()[-500:])
            return json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise PluginManagerError("校验", f"无法读取插件入口: {error}") from error

    def _default_import_check(self, plugin_ids: Sequence[str]) -> HealthCheckResult:
        checker = DefaultHealthChecker(
            import_check=True, python_executable=self.python, stable_root=self.cwd
        )
        return checker._import_check_sync(self.host_root, plugin_ids)

    # ------------------------------------------------------------ 事务

    @contextmanager
    def _exclusive(self, *, wait: bool = False):
        """同一时间只执行一个插件任务。"""

        if not self._busy.acquire(blocking=wait):
            raise PluginManagerError("排队", "已有插件任务正在执行，请稍后再试")
        try:
            yield
        finally:
            self._busy.release()

    def _snapshot(
        self,
        action: str,
        label: str,
        lock: str,
        records: Mapping,
        suspended: Mapping | None = None,
    ) -> Path:
        """备份：当前插件锁、插件记录、停用记录和 ``uv pip freeze``，回滚命令靠它恢复。"""

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        folder = self.history_dir / f"{stamp}-{action}-{label}"
        folder.mkdir(parents=True)
        (folder / LOCK_NAME).write_text(lock, encoding="utf-8")
        (folder / "records.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (folder / "suspended.json").write_text(
            json.dumps(suspended or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        freeze = self._uv("备份", "pip", "freeze", "--python", self.python).stdout
        (folder / "freeze.txt").write_text(freeze, encoding="utf-8")
        for stale in sorted(p for p in self.history_dir.iterdir() if p.is_dir())[
            :-HISTORY_KEEP
        ]:
            shutil.rmtree(stale, ignore_errors=True)
        return folder

    def _verify(
        self,
        new_lock: str,
        old_records: Mapping[str, Any],
        new_records: Mapping[str, Mapping[str, Any]],
        entries: list[dict[str, Any]],
    ) -> None:
        """环境必须与插件锁一致，且每个插件的身份、版本、提交号都符合记录。"""

        if bad := self._drift(new_lock):
            raise PluginManagerError("校验", "环境与插件锁不一致: " + ", ".join(bad))
        locked = _lock_entries(new_lock)
        for plugin_id, record in new_records.items():
            found = [e for e in entries if e["id"] == plugin_id]
            if len(found) != 1 or canonicalize_name(
                found[0]["dist"]
            ) != canonicalize_name(record["dist"]):
                raise PluginManagerError(
                    "校验", f"安装后的 tenko.plugins 入口 {plugin_id} 与记录不符"
                )
            entry = found[0]
            try:
                same = Version(entry["version"]) == Version(record["version"])
            except InvalidVersion:
                same = entry["version"] == record["version"]
            if not same:
                raise PluginManagerError(
                    "校验",
                    f"{plugin_id} 安装的版本 {entry['version']} 不是锁定的 {record['version']}",
                )
            if record.get("commit") and entry.get("commit") != record["commit"]:
                raise PluginManagerError(
                    "校验",
                    f"{plugin_id} 的提交号 {entry.get('commit')} 不是锁定的 {record['commit']}",
                )
            if canonicalize_name(record["dist"]) not in locked:
                raise PluginManagerError("校验", f"{record['dist']} 不在插件锁里")
        for plugin_id, record in old_records.items():
            if plugin_id not in new_records and any(
                e["id"] == plugin_id
                and canonicalize_name(e["dist"]) == canonicalize_name(record["dist"])
                for e in entries
            ):
                raise PluginManagerError("校验", f"{plugin_id} 卸载后仍然存在")

    def _transaction(
        self,
        action: str,
        actor: str | None,
        label: str,
        new_lock: str,
        make_records: Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]],
        *,
        consume: Path | None = None,
        defer: bool = False,
        state_hook: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """备份 → 应用 → 校验 → 提交；应用或校验失败时恢复环境并抛出异常。

        ``defer`` 只用于卸载和停用：不动环境，把要删的包记进 ``pending_removal``，
        下次启动加载插件之前再删。``state_hook`` 在提交前修改 ``state.json`` 的其他节，
        与锁一起提交。
        """

        state = self.runtime.read_state()
        old_records = _records(state)
        old_lock = self._read_lock()
        data_root = plugin_storage.PLUGIN_DATA_ROOT
        had_data = (
            {p.name for p in data_root.iterdir()} if data_root.is_dir() else set()
        )
        snapshot = (
            consume
            if consume is not None
            else self._snapshot(action, label, old_lock, old_records, _suspended(state))
        )
        step, new_records = "应用", None
        try:
            if defer:
                new_records = make_records([])
                gone = set(_lock_entries(old_lock)) - set(_lock_entries(new_lock))
                state["pending_removal"] = sorted(
                    gone | set(state.get("pending_removal") or ())
                )
            else:
                self._apply(old_lock, new_lock)
                step = "校验"
                entries = self._entries()
                new_records = make_records(entries)
                self._verify(new_lock, old_records, new_records, entries)
                changed = [
                    pid
                    for pid, rec in new_records.items()
                    if rec.get("version") != old_records.get(pid, {}).get("version")
                    or rec.get("requirement")
                    != old_records.get(pid, {}).get("requirement")
                ]
                step = "子进程导入检查"
                if not (result := self.import_check(changed)).ok:
                    raise PluginManagerError(step, result.reason)
        except Exception as error:
            restored = self._restore(old_lock, new_lock)
            for plugin_id in set(new_records or ()) - set(old_records) - had_data:
                if consume is None:
                    plugin_storage.clear_plugin_data(plugin_id)
            if consume is None:
                shutil.rmtree(snapshot, ignore_errors=True)
            if isinstance(error, PluginManagerError):
                error.restored = restored
                raise
            raise PluginManagerError(
                step, f"{type(error).__name__}: {error}", restored=restored
            ) from error

        # 记录和启用列表写在同一个 state.json 里，和锁一起提交。
        state["plugins"] = new_records
        state["enabled"] = sorted(
            i for i in state["enabled"] if i in new_records or i not in old_records
        )
        if state_hook is not None:
            state_hook(state)
        self._commit(new_lock, state)
        if consume is not None:
            shutil.rmtree(consume, ignore_errors=True)
        return new_records

    def _restore(self, old_lock: str, new_lock: str) -> bool:
        try:
            self._apply(new_lock, old_lock)
            if bad := self._drift(old_lock):
                raise PluginManagerError("回滚校验", ", ".join(bad))
        except Exception:
            logger.exception("插件环境恢复失败，需要执行 /同步插件环境")
            return False
        return True

    def _audited(
        self, action: str, actor: str | None, fn: Callable[[], Any], **details
    ):
        """记录审计：成功时记录 ``fn`` 返回的变更，失败时记录步骤和原因。"""

        try:
            result = fn()
        except PluginManagerError as error:
            self.audit.record(
                f"plugin-{action}",
                result="failed",
                actor=actor,
                step=error.step,
                error=_redact_text(error.detail),
                restored=error.restored,
                **details,
            )
            raise
        changes = result if isinstance(result, list) else [result]
        self.audit.record(
            f"plugin-{action}",
            result="success",
            actor=actor,
            changes=[
                {
                    "plugin": c.plugin_id,
                    "dist": c.dist,
                    "from": c.old_version,
                    "to": c.new_version,
                }
                for c in changes
                if isinstance(c, PluginChange)
            ],
            **details,
        )
        return result

    # ------------------------------------------------------------ 记录

    def _record(
        self,
        dist: str,
        source: SourceKind,
        trust: str,
        requirement: str,
        lock: str,
        entries: list[dict[str, Any]],
        *,
        expected_id: str | None,
        actor: str | None,
        known_ids: Mapping[str, Any],
        previous: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        mine = [e for e in entries if canonicalize_name(e["dist"]) == dist]
        if len(mine) != 1:
            raise PluginManagerError(
                "校验",
                f"{dist} 声明了 {len(mine)} 个 tenko.plugins 入口，需要恰好 1 个",
            )
        entry = mine[0]
        plugin_id = entry["id"]
        if not (
            _PLUGIN_ID.fullmatch(plugin_id) and _MODULE_PATH.fullmatch(entry["module"])
        ):
            raise PluginManagerError(
                "校验", f"入口 {plugin_id!r} = {entry['module']!r} 不合法"
            )
        if expected_id and plugin_id != expected_id:
            raise PluginManagerError(
                "校验", f"插件 ID 是 {plugin_id}，与期望的 {expected_id} 不符"
            )
        if plugin_id in self.runtime.builtin_names():
            raise PluginManagerError("校验", f"插件 ID {plugin_id} 与内置插件重名")
        if sum(e["id"] == plugin_id for e in entries) > 1 or (
            plugin_id in known_ids
            and canonicalize_name(known_ids[plugin_id]["dist"]) != dist
        ):
            raise PluginManagerError("校验", f"插件 ID {plugin_id} 已被其他分发包占用")
        block = _lock_entries(lock)[dist]
        pin = _pin(block)
        if source == "git":
            requirement = block.splitlines()[0].rstrip(" \\")
        hashes = _HASH.findall(block)
        now = _now()
        return plugin_id, {
            "dist": entry["dist"],
            "version": entry["version"],
            "requirement": requirement,
            "source": source,
            "trust": trust,
            "hash": hashes[0] if hashes else None,
            "commit": (m.group(1) if (m := _COMMIT.search(pin["value"])) else None),
            "installed_by": (previous or {}).get("installed_by", actor),
            "installed_at": (previous or {}).get("installed_at", now),
            "updated_at": now,
        }

    @staticmethod
    def _check_pins(source: PluginSource, lock: str, dist: str) -> None:
        """目录来源：解析出的文件哈希必须都是目录登记过的。"""

        if not source.pins:
            return
        block = _lock_entries(lock).get(canonicalize_name(dist), "")
        hashes = set(_HASH.findall(block))
        if not hashes or not hashes <= set(source.pins):
            raise PluginManagerError(
                "解析依赖", f"{dist} 的文件哈希与插件目录登记的不一致，已停止"
            )

    @staticmethod
    def _git_dist(old_lock: str, new_lock: str) -> str:
        old, new = _lock_entries(old_lock), _lock_entries(new_lock)
        urls = [
            name
            for name, block in new.items()
            if _pin(block)["op"] == "@" and old.get(name) != block
        ]
        if len(urls) != 1:
            raise PluginManagerError("解析依赖", f"无法确定 Git 仓库对应的包: {urls}")
        return urls[0]

    # ------------------------------------------------------------ 操作

    def _install(
        self, source: PluginSource, actor: str | None, *, resuming: str | None = None
    ) -> PluginChange:
        """安装；``resuming`` 是要恢复的已停用插件，装好后沿用它的记录和启用意愿。"""

        with self._exclusive():
            state = self.runtime.read_state()
            records, suspended = _records(state), _suspended(state)
            held = suspended.get(resuming) if resuming else None
            if resuming and held is None:
                raise PluginManagerError("解析来源", f"{resuming} 没有被停用")
            if held is not None:
                source = replace(source, expected_id=resuming)
            old_lock = self._read_lock()
            new_lock = self._compile(
                [*(r["requirement"] for r in records.values()), source.requirement],
                old_lock,
                refresh=[Requirement(source.requirement).name]
                if source.kind == "pypi"
                else [],
            )
            dist = (
                canonicalize_name(Requirement(source.requirement).name)
                if source.kind == "pypi"
                else self._git_dist(old_lock, new_lock)
            )
            if any(canonicalize_name(r["dist"]) == dist for r in records.values()):
                raise PluginManagerError("解析来源", f"{dist} 已安装，请用 /更新插件")
            self._check_pins(source, new_lock, dist)
            created: list[tuple[str, dict[str, Any]]] = []

            def make(entries):
                created.append(
                    self._record(
                        dist, source.kind, source.trust, source.requirement,
                        new_lock, entries, expected_id=source.expected_id,
                        actor=actor, known_ids=records,
                        previous=held and held["record"],
                    )
                )  # fmt: skip
                if held is None and created[0][0] in suspended:
                    raise PluginManagerError(
                        "校验", f"插件 {created[0][0]} 已停用，请恢复或卸载它"
                    )
                return {**records, created[0][0]: created[0][1]}

            def restore(new_state: dict[str, Any]) -> None:
                if held is None:
                    return
                rest = _suspended(new_state)
                rest.pop(resuming, None)
                new_state["suspended"] = rest
                if held.get("enabled"):
                    new_state["enabled"] = sorted({*new_state["enabled"], resuming})

            action = "install" if held is None else "resume"
            self._transaction(action, actor, dist, new_lock, make, state_hook=restore)
            plugin_id, record = created[0]
            return PluginChange(
                action,
                plugin_id,
                record["dist"],
                held and held["record"]["version"],
                record["version"],
            )

    def _update(
        self,
        plugin_id: str | None,
        sources: Mapping[str, PluginSource],
        actor: str | None,
    ) -> list[PluginChange]:
        with self._exclusive():
            records = _records(self.runtime.read_state())
            if plugin_id is not None and plugin_id not in records:
                raise PluginManagerError(
                    "解析来源", f"{plugin_id} 不是由插件管理器安装的"
                )
            for pid, source in sources.items():
                if pid not in records or source.kind != records[pid]["source"]:
                    raise PluginManagerError(
                        "解析来源", "新来源必须属于已安装的插件，且与原来的来源类型一致"
                    )
            targets = (
                [plugin_id]
                if plugin_id
                else [i for i, r in records.items() if r["source"] == "pypi"]
            )
            requirements = {
                i: sources[i].requirement if i in sources else r["requirement"]
                for i, r in records.items()
            }
            old_lock = self._read_lock()
            new_lock = self._compile(
                list(requirements.values()),
                old_lock,
                upgrade=[
                    records[i]["dist"]
                    for i in targets
                    if records[i]["source"] == "pypi" and i not in sources
                ],
                refresh=[
                    records[i]["dist"] for i, s in sources.items() if s.kind == "pypi"
                ],
            )
            if new_lock == old_lock and not sources:
                return []
            for pid, source in sources.items():
                self._check_pins(source, new_lock, records[pid]["dist"])
            new_records: dict[str, dict[str, Any]] = {}

            def make(entries):
                nonlocal new_records
                new_records = {}
                for pid, old in records.items():
                    if pid not in targets and pid not in sources:
                        new_records[pid] = old
                        continue
                    trust = sources[pid].trust if pid in sources else old["trust"]
                    pid_new, rec = self._record(
                        canonicalize_name(old["dist"]), old["source"], trust,
                        requirements[pid], new_lock, entries,
                        expected_id=pid, actor=actor, known_ids={}, previous=old,
                    )  # fmt: skip
                    new_records[pid_new] = rec
                return new_records

            self._transaction("update", actor, plugin_id or "all", new_lock, make)
            return [
                PluginChange(
                    "update", pid, rec["dist"], records[pid]["version"], rec["version"]
                )
                for pid, rec in new_records.items()
                if rec["version"] != records[pid]["version"]
                or rec["requirement"] != records[pid]["requirement"]
            ]

    def _uninstall(
        self, plugin_id: str, clear_data: bool, actor: str | None
    ) -> PluginChange:
        with self._exclusive():
            state = self.runtime.read_state()
            records, suspended = _records(state), _suspended(state)
            if plugin_id not in records and plugin_id not in suspended:
                raise PluginManagerError(
                    "解析来源", f"{plugin_id} 不是由插件管理器安装的"
                )
            if clear_data and self._loaded(plugin_id):
                raise PluginManagerError(
                    "预检",
                    f"插件 {plugin_id} 正在运行，仍持有数据库连接。请先 /停用插件 并 /重启，"
                    "再带「清除数据」卸载；或先不带它卸载，重启后用 /清除插件数据",
                )
            if plugin_id in suspended:  # 包已移出环境，只需删掉保留的记录
                old = suspended.pop(plugin_id)["record"]
                state["suspended"] = suspended
                self.runtime.write_state(state)
                cleared = (
                    plugin_storage.clear_plugin_data(plugin_id) if clear_data else None
                )
                return PluginChange(
                    "uninstall", plugin_id, old["dist"], old["version"], None, cleared
                )
            old = records.pop(plugin_id)
            new_lock = self._compile(
                [r["requirement"] for r in records.values()], self._read_lock()
            )
            self._transaction(
                "uninstall", actor, plugin_id, new_lock, lambda _e: dict(records),
                defer=True,
            )  # fmt: skip
            cleared = (
                plugin_storage.clear_plugin_data(plugin_id) if clear_data else None
            )
            return PluginChange(
                "uninstall", plugin_id, old["dist"], old["version"], None, cleared
            )

    def _rollback(self, actor: str | None) -> list[PluginChange]:
        """撤销最近一次安装、更新或卸载：恢复当时备份的插件锁和记录。"""

        with self._exclusive():
            folders = (
                sorted(p for p in self.history_dir.iterdir() if p.is_dir())
                if self.history_dir.is_dir()
                else []
            )
            if not folders:
                raise PluginManagerError("回滚", "没有可回滚的插件操作")
            folder = folders[-1]
            target_lock = (folder / LOCK_NAME).read_text(encoding="utf-8")
            target = json.loads((folder / "records.json").read_text(encoding="utf-8"))
            saved = folder / "suspended.json"
            held = (
                json.loads(saved.read_text(encoding="utf-8"))
                if saved.is_file()
                else None
            )
            state = self.runtime.read_state()
            current, now_held = _records(state), _suspended(state)

            def restore(new_state: dict[str, Any]) -> None:
                if held is None:  # 旧版本的备份没有停用记录
                    return
                new_state["suspended"] = held
                # 撤销停用：插件回到记录里，按停用前的意愿恢复启用。
                back = {
                    pid
                    for pid, item in now_held.items()
                    if pid in target and pid not in held and item.get("enabled")
                }
                new_state["enabled"] = sorted({*new_state["enabled"], *back})

            self._transaction(
                "rollback",
                actor,
                folder.name,
                target_lock,
                lambda _e: target,
                consume=folder,
                state_hook=restore,
            )
            changes = []
            for pid in sorted(set(current) | set(target)):
                before, after = current.get(pid), target.get(pid)
                if (before or {}).get("version") == (after or {}).get("version"):
                    continue
                changes.append(
                    PluginChange(
                        "rollback",
                        pid,
                        (after or before)["dist"],
                        before and before["version"],
                        after and after["version"],
                    )  # fmt: skip
                )
                if after:  # 回到旧版本时，插件库也还原成旧版本时的备份。
                    plugin_storage.request_db_restore(pid, after["version"])
            return changes

    def _suspend(
        self,
        choices: Mapping[str, PluginSource | None],
        reasons: Mapping[str, str],
        actor: str | None,
    ) -> list[PluginChange]:
        """停用并保留记录：包移出环境（重启前删除），记录、启用意愿、数据和配置保留。

        ``choices`` 的值是下次启动时要自动装回的来源，``None`` 表示等待手动处理。
        """

        with self._exclusive():
            state = self.runtime.read_state()
            records = _records(state)
            if unknown := sorted(set(choices) - set(records)):
                raise PluginManagerError(
                    "解析来源", f"{'、'.join(unknown)} 不是由插件管理器安装的"
                )
            enabled = set(state["enabled"])
            kept = {pid: r for pid, r in records.items() if pid not in choices}
            new_lock = self._compile(
                [r["requirement"] for r in kept.values()], self._read_lock()
            )
            now = _now()

            def hold(new_state: dict[str, Any]) -> None:
                held = _suspended(new_state)
                for pid, source in choices.items():
                    held[pid] = {
                        "record": records[pid],
                        "enabled": pid in enabled,
                        "reason": _redact_text(reasons.get(pid, ""))[-300:],
                        "suspended_at": now,
                        "resume": source and asdict(source),
                        "error": None,
                    }
                new_state["suspended"] = held

            self._transaction(
                "suspend", actor, "+".join(sorted(choices)), new_lock,
                lambda _e: dict(kept), defer=True, state_hook=hold,
            )  # fmt: skip
            return [
                PluginChange(
                    "suspend", pid, records[pid]["dist"], records[pid]["version"], None
                )
                for pid in sorted(choices)
            ]

    def resume_pending(self) -> list[str]:
        """启动加载插件之前：按停用时选定的来源装回插件，返回已恢复的插件。

        装不上的保持停用，记下原因并清掉自动恢复，不在每次启动时反复重试。
        """

        resumed = []
        for pid, held in _suspended(self.runtime.read_state()).items():
            if not held.get("resume"):
                continue
            source = _source_of(held["resume"])
            try:
                self._audited(
                    "resume", "startup",
                    lambda pid=pid, source=source: self._install(
                        source, "startup", resuming=pid
                    ),
                    plugin=pid,
                )  # fmt: skip
            except PluginManagerError as error:
                logger.warning("停用的插件 {} 未能自动恢复: {}", pid, error)
                state = self.runtime.read_state()
                rest = _suspended(state)
                if pid in rest:
                    rest[pid].update(resume=None, error=_redact_text(str(error))[-300:])
                    state["suspended"] = rest
                    self.runtime.write_state(state)
            else:
                resumed.append(pid)
        return resumed

    def _sync_env(self) -> list[str]:
        """按插件锁重装缺失或版本不符的包（例如 ``uv sync`` 清掉插件之后）。"""

        with self._exclusive(wait=True):
            lock = self._read_lock()
            if not (bad := self._drift(lock)):
                return []
            self._apply(lock, lock)
            if left := self._drift(lock):
                raise PluginManagerError(
                    "校验", "仍不一致: " + ", ".join(left), restored=None
                )
            return bad

    def _remove_pending(self) -> list[str]:
        """删除卸载时留到重启才删的包；之后又被装回插件锁或属于宿主的包不删。"""

        state = self.runtime.read_state()
        if not (pending := state.get("pending_removal")):
            return []
        keep = set(_lock_entries(self._read_lock()))
        with tempfile.TemporaryDirectory(prefix="tenko-plugins-") as work:
            keep |= self._host_constraints(Path(work))[1]
        if names := [n for n in pending if n not in keep]:
            self._uv("卸载", "pip", "uninstall", "--python", self.python, *names)
        state["pending_removal"] = []
        self.runtime.write_state(state)
        return names

    def _release_file(self, release_root: Path) -> Path:
        return self.plugin_dir / "releases" / f"{release_root.name}.json"

    def _save_release(self, release_root: Path) -> None:
        """宿主切换前，把插件锁和记录存到离开的那个版本名下，回滚到它时恢复。"""

        path = self._release_file(release_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = self.runtime.read_state()
        path.write_text(
            json.dumps(
                {"lock": self._read_lock(), "plugins": _records(state)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        saved = sorted(path.parent.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for stale in saved[:-HISTORY_KEEP]:
            stale.unlink(missing_ok=True)

    def _restore_release(self, release_root: Path) -> None:
        path = self._release_file(release_root)
        if not path.is_file():
            return
        saved = json.loads(path.read_text(encoding="utf-8"))
        state = self.runtime.read_state()
        old_records = _records(state)
        self._apply(self._read_lock(), saved["lock"])
        state["plugins"] = saved["plugins"]
        state["enabled"] = sorted(
            i for i in state["enabled"] if i in saved["plugins"] or i not in old_records
        )
        self._commit(saved["lock"], state)
        logger.info("已恢复 {} 时的插件锁", release_root.name)

    def _reconcile(
        self, release_root: Path | None, leaving: Path | None, restore: bool
    ) -> None:
        with self._exclusive(wait=True):
            self._repair_lock()
            if leaving is not None:
                self._save_release(leaving)
            if restore and release_root is not None:
                self._restore_release(release_root)
            self._remove_pending()
            lock = self._read_lock()
            names = set(_lock_entries(lock))
            if not names:
                return
            if self._drift(lock):
                self._apply(lock, lock)
            checked = self._uv(
                "兼容性检查", "pip", "check", "--python", self.python, check=False
            )
            output = (checked.stderr or "") + (checked.stdout or "")
            if checked.returncode != 0:
                # 只关心涉及插件包的不兼容，宿主环境里别的历史问题不阻断。
                lines = [
                    line
                    for line in output.splitlines()
                    if any(
                        canonicalize_name(n) in names for n in _BACKTICKED.findall(line)
                    )
                ]
                if lines:
                    raise PluginManagerError(
                        "兼容性检查", "；".join(line.strip() for line in lines)
                    )

    # ------------------------------------------------------------ 公开接口

    async def install(
        self, source: PluginSource, *, actor: str | None = None
    ) -> PluginChange:
        """安装插件；新装的插件默认未启用，重启后才生效。"""

        return await asyncio.to_thread(
            self._audited, "install", actor, lambda: self._install(source, actor),
            source=source.requirement, trust=source.trust,
        )  # fmt: skip

    async def update(
        self,
        plugin_id: str | None = None,
        source: PluginSource | None = None,
        *,
        sources: Mapping[str, PluginSource] | None = None,
        actor: str | None = None,
    ) -> list[PluginChange]:
        """更新插件；``[]`` 表示已是最新。

        不带 ID 时把全部 PyPI 来源的插件升级到兼容的最新版本；``sources`` 为若干插件
        指定新来源（目录里的新版本、新的 Git tag）。``source`` 是只更新 ``plugin_id``
        时的简写。
        """

        targets = dict(sources or {})
        if source is not None:
            if plugin_id is None:
                raise PluginManagerError("解析来源", "指定新来源时必须给出插件 ID")
            targets[plugin_id] = source
        return await asyncio.to_thread(
            self._audited, "update", actor, lambda: self._update(plugin_id, targets, actor),
            plugin=plugin_id,
        )  # fmt: skip

    async def uninstall(
        self, plugin_id: str, *, clear_data: bool = False, actor: str | None = None
    ) -> PluginChange:
        """卸载插件；数据目录默认保留，``clear_data`` 时在卸载成功后删除。"""

        return await asyncio.to_thread(
            self._audited, "uninstall", actor,
            lambda: self._uninstall(plugin_id, clear_data, actor),
            plugin=plugin_id, clear_data=clear_data,
        )  # fmt: skip

    async def suspend(
        self,
        choices: Mapping[str, PluginSource | None],
        *,
        reasons: Mapping[str, str] | None = None,
        actor: str | None = None,
    ) -> list[PluginChange]:
        """停用并保留记录（见 :meth:`_suspend`），重启后生效。"""

        return await asyncio.to_thread(
            self._audited, "suspend", actor,
            lambda: self._suspend(choices, reasons or {}, actor),
            plugins=sorted(choices),
        )  # fmt: skip

    async def resume(
        self, plugin_id: str, source: PluginSource, *, actor: str | None = None
    ) -> PluginChange:
        """把已停用的插件装成 ``source``，恢复它的记录和启用意愿。"""

        return await asyncio.to_thread(
            self._audited, "resume", actor,
            lambda: self._install(source, actor, resuming=plugin_id),
            plugin=plugin_id, source=source.requirement,
        )  # fmt: skip

    async def rollback(self, *, actor: str | None = None) -> list[PluginChange]:
        """撤销最近一次插件变更（版本、依赖和插件库一起恢复）。"""

        return await asyncio.to_thread(
            self._audited, "rollback", actor, lambda: self._rollback(actor)
        )

    async def sync_env(self, *, actor: str | None = None) -> list[str]:
        """按插件锁恢复环境，返回被重装的包。"""

        repaired = await asyncio.to_thread(self._audited, "sync", actor, self._sync_env)
        return repaired

    async def reconcile(
        self,
        release_root: Path | None = None,
        leaving: Path | None = None,
        restore: bool = False,
    ) -> None:
        """宿主升级或回滚同步依赖之后调用：补回缺失的插件包，并检查与新依赖是否兼容。

        ``leaving`` 是切换前的宿主版本目录，当时的插件锁存到它名下；``restore`` 时
        （回滚，或升级失败退回）恢复 ``release_root`` 名下存过的插件锁。
        """

        await asyncio.to_thread(
            self._audited, "reconcile", None,
            lambda: self._reconcile(release_root, leaving, restore),
        )  # fmt: skip

    def _incompatible(self, host_root: Path) -> dict[str, str]:
        records = _records(self.runtime.read_state())
        lock = self._read_lock()

        def failure(requirements: list[str]) -> str | None:
            try:
                self._compile(requirements, lock, host_root=host_root)
            except PluginManagerError as error:
                return error.detail
            return None

        if (reason := failure([r["requirement"] for r in records.values()])) is None:
            return {}
        bad = {
            pid: why
            for pid, rec in records.items()
            if (why := failure([rec["requirement"]])) is not None
        }
        return bad or {"*": reason}  # 单个都能解析，合在一起冲突

    async def incompatible(self, host_root: Path) -> dict[str, str]:
        """用新版本的宿主锁重新解析插件锁（环境不受影响），返回不兼容的插件和原因。

        键 ``"*"`` 表示每个插件单独都兼容，但合在一起冲突。
        """

        return await asyncio.to_thread(self._incompatible, host_root)

    def startup_check(self) -> list[str]:
        """启动加载插件之前：补完中断的提交，删除待删的包，返回需要告警的问题。"""

        with self._exclusive(wait=True):
            problems = [p for p in [self._repair_lock()] if p]
            if removed := self._remove_pending():
                logger.info("已删除卸载插件留下的包: {}", "、".join(removed))
            if drift := self._drift(self._read_lock()):
                problems.append(
                    f"插件环境与 plugins.lock 不一致（{'、'.join(drift)}），"
                    "Master 可执行 /同步插件环境 恢复"
                )
            self._startup_lock = _lock_entries(self._read_lock())
            return problems

    async def load_now(self, runtime: PluginRuntime, info: PluginInfo) -> str | None:
        """已启用但未加载的扩展插件尝试不重启直接加载；返回仍需重启的原因。

        只在不会新旧代码混用时加载：启动以来插件锁只新增过包（已导入的依赖版本
        都没变），且插件模块从未在本进程导入过。插件定义了数据库模型等运行中
        无法完成的初始化时，加载失败，由调用方提示重启。
        """

        if not runtime.restart_required(info):
            return None
        if self._startup_lock is None:
            return "本次启动没有记录插件环境"
        current = _lock_entries(self._read_lock())
        if any(
            current.get(name) != entry for name, entry in self._startup_lock.items()
        ):
            return "启动之后已有的插件包发生过变化"
        if info.qualified_name.split(".")[0] in sys.modules:
            return "插件模块在本次运行中已导入过"
        if not self._busy.acquire(blocking=False):
            return "有插件任务正在执行"
        try:
            await runtime.load_external(info)
        except Exception as error:  # noqa: BLE001 - 插件代码的任何错误都只意味着要重启
            # Entari 把插件抛出的异常包成 ImportError，原异常在 __context__ 上。
            cause = error.__cause__ or error.__context__ or error
            logger.warning("扩展插件 {} 无法在运行中加载: {}", info.name, cause)
            if isinstance(cause, plugin_storage.PluginStorageError):
                return "插件使用了独立数据库"
            return f"运行中加载失败（{cause}）"
        finally:
            self._busy.release()
        _live_loaded[info.name] = _now()
        logger.info("扩展插件 {} 已在运行中加载", info.name)
        return None

    # ------------------------------------------------------------ 查询

    def _loaded(self, plugin_id: str) -> bool:
        try:
            return self.runtime.is_loaded(plugin_id)
        except KeyError:
            return False

    def suspended(self) -> dict[str, dict[str, Any]]:
        """已停用并保留记录的插件：``record`` 是停用前的安装记录。"""

        return _suspended(self.runtime.read_state())

    def status(self) -> list[PluginStatus]:
        state = self.runtime.read_state()
        enabled = set(state["enabled"])
        rows = []
        for plugin_id, rec in sorted(_records(state).items()):
            loaded = self._loaded(plugin_id)
            rows.append(
                PluginStatus(
                    plugin_id,
                    rec["dist"],
                    rec["version"],
                    rec["source"],
                    rec["trust"],
                    rec["requirement"],
                    plugin_id in enabled,
                    loaded,
                    # 上次变更发生在插件加载之后：已加载的仍是旧代码；已启用但
                    # 未加载的要等重启。
                    (
                        loaded
                        and rec["updated_at"]
                        > _live_loaded.get(plugin_id, _PROCESS_START)
                    )
                    or (plugin_id in enabled and not loaded),
                    rec["installed_at"],
                    rec["updated_at"],
                )  # fmt: skip
            )
        return rows


_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    """进程内共享的管理器；审计写入与升级系统相同的 ``audit.jsonl``。"""

    global _manager
    if _manager is None:
        _manager = PluginManager(audit=get_upgrade_manager().audit)
    return _manager
