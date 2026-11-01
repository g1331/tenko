"""插件所在 Python 环境的操作：调用 uv、读取已安装的包、编译和应用插件锁、列出入口、导入检查。

这一层只面向环境，不碰 ``state.json``、事务、备份和热加载；那些留在 :class:`PluginManager`。
插件锁的文本格式（``_lock_entries`` 等）和 :class:`PluginManagerError` 也定义在这里：
环境操作要读写锁、报告失败，而管理器依赖本模块，反过来依赖会形成循环导入。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from loguru import logger
from packaging.utils import canonicalize_name

from ..updater import DefaultHealthChecker, HealthCheckResult, tenko_api_version
from ..uv import UvError, run_uv

LOCK_NAME = "plugins.lock"
_UV_TIMEOUT = 600
_LOCK_HEADER = (
    "# Tenko 插件锁：由 uv pip compile 生成，已排除宿主锁里的包，请勿手工编辑。\n"
    "# 恢复环境：/同步插件环境 或 python -m tenko plugins sync\n"
)
_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<op>==|@)\s*(?P<value>\S+)"
)
_COMMIT = re.compile(r"@([0-9a-f]{40})(?![0-9a-f])")
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


def _commit_of(pin: str) -> str:
    """Git 来源比较提交号；``uv pip freeze`` 和插件锁里 URL 的写法可能不同。"""

    return m.group(1) if (m := _COMMIT.search(pin)) else pin


class PluginEnvironment:
    """插件所在 Python 环境的操作集合；方法都只依赖解释器、uv 和宿主锁，不读管理器的状态。"""

    def __init__(
        self,
        *,
        python: str,
        uv: str | None,
        cwd: Path,
        host_root: Path,
    ) -> None:
        self.python = python
        self.uv = uv
        self.cwd = cwd
        self.host_root = host_root

    def run(
        self, step: str, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            return run_uv(
                args, uv=self.uv, timeout=_UV_TIMEOUT, cwd=self.cwd, check=check
            )
        except UvError as error:
            raise PluginManagerError(step, str(error)) from error

    def freeze(self) -> dict[str, str]:
        """环境里已安装的包：规范化包名 -> ``==版本`` 或 ``@ 链接``。"""

        out = self.run("读取环境", "pip", "freeze", "--python", self.python).stdout
        installed: dict[str, str] = {}
        for line in out.splitlines():
            if matched := _PIN.match(line.strip()):
                installed[canonicalize_name(matched["name"])] = (
                    f"{matched['op']}{matched['value']}"
                )
        return installed

    def host_constraints(
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
            exported = self.run(
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

    def compile(
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
            constraints, host_names = self.host_constraints(directory, host_root)
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
            self.run("解析依赖", *args)
            entries = _lock_entries(output.read_text(encoding="utf-8"))
        return _render_lock(
            {n: block for n, block in entries.items() if n not in host_names}
        )

    def apply(self, old_lock: str, new_lock: str) -> None:
        """让环境符合 ``new_lock``：先卸载只在旧锁里的包（孤立依赖），再按锁安装。"""

        old, new = _lock_entries(old_lock), _lock_entries(new_lock)
        if orphans := sorted(set(old) - set(new)):
            self.run("卸载旧包", "pip", "uninstall", "--python", self.python, *orphans)
        if new:
            with tempfile.TemporaryDirectory(prefix="tenko-plugins-") as work:
                path = Path(work) / LOCK_NAME
                path.write_text(new_lock, encoding="utf-8")
                self.run(
                    "安装",
                    "pip", "install", "--python", self.python,
                    "-r", str(path), "--no-deps",
                )  # fmt: skip

    def drift(self, lock_text: str) -> list[str]:
        """锁里有、但环境里缺失或版本不符的包。"""

        entries = _lock_entries(lock_text)
        if not entries:
            return []
        installed = self.freeze()
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

    def entries(self) -> list[dict[str, Any]]:
        """用目标环境的解释器读取 ``tenko.plugins`` 入口，不依赖本进程的缓存。"""

        try:
            completed = subprocess.run(
                [self.python, "-I", "-c", _ENTRIES_SCRIPT],
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                cwd=self.cwd,
            )
            if completed.returncode != 0:
                raise OSError(completed.stderr.strip()[-500:])
            return json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise PluginManagerError("校验", f"无法读取插件入口: {error}") from error

    def default_import_check(self, plugin_ids: Sequence[str]) -> HealthCheckResult:
        checker = DefaultHealthChecker(
            import_check=True, python_executable=self.python, stable_root=self.cwd
        )
        return checker._import_check_sync(self.host_root, plugin_ids)
