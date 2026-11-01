"""候选版本的健康检查与候选进程环境。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


def _same_python_executable(value: str, expected: str) -> bool:
    """判断命令是否使用了健康检查/启动约定的同一 Python。"""

    if value == expected:
        return True
    try:
        return (
            Path(value).expanduser().resolve() == Path(expected).expanduser().resolve()
        )
    except OSError:
        return False


def _candidate_python_command(
    command: Sequence[str], candidate: Path, python_executable: str
) -> list[str]:
    """为 Python 命令加入候选代码 bootstrap。

    稳定根目录必须继续作为 ``cwd``，所以 Python 的 ``sys.path[0]`` 会指向
    稳定根目录。仅设置 ``PYTHONPATH`` 仍可能优先导入稳定根目录中的旧
    ``tenko`` 包；将常见的 ``-m``/``-c`` 命令改写为 fresh interpreter 内的
    ``runpy``/``exec`` 调用，才能同时保留稳定工作目录和候选代码优先级。
    """

    if not command or not _same_python_executable(command[0], python_executable):
        return list(command)

    mode_index = next(
        (
            index
            for index, item in enumerate(command[1:], start=1)
            if item in {"-m", "-c"}
        ),
        None,
    )
    if mode_index is None or mode_index + 1 >= len(command):
        return list(command)

    prefix = list(command[1:mode_index])
    mode = command[mode_index]
    target = command[mode_index + 1]
    arguments = list(command[mode_index + 2 :])
    if mode == "-m":
        wrapper = (
            "import runpy,sys;"
            "release_root=sys.argv[1];"
            "module=sys.argv[2];"
            "sys.path.insert(0,release_root);"
            "sys.argv=[module,*sys.argv[3:]];"
            "runpy.run_module(module,run_name='__main__')"
        )
    else:
        wrapper = (
            "import sys;"
            "release_root=sys.argv[1];"
            "source=sys.argv[2];"
            "sys.path.insert(0,release_root);"
            "sys.argv=['-c',*sys.argv[3:]];"
            "exec(compile(source,'<candidate-command>','exec'),"
            "{'__name__':'__main__'})"
        )
    return [
        command[0],
        *prefix,
        "-c",
        wrapper,
        str(candidate),
        target,
        *arguments,
    ]


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    ok: bool
    reason: str


class DefaultHealthChecker:
    """默认最小健康检查：进程存活（若有）加 Python 编译冒烟。

    实际部署可配置 ``health_command`` 做最小启动检查；命令以独立子进程运行，
    工作目录固定为稳定根目录，代码源通过 bootstrap 优先使用候选版本目录，
    因而不会导入当前进程中的旧模块。命令可使用 ``{python}``、
    ``{release_root}``、``{config_path}`` 和 ``{data_dir}`` 占位符。
    """

    def __init__(
        self,
        command: Sequence[str] = (),
        *,
        timeout: int = 30,
        python_executable: str = sys.executable,
        config_path: Path | None = None,
        data_dir: Path | None = None,
        upgrade_root: Path | None = None,
        stable_root: Path | None = None,
        import_check: bool = False,
    ) -> None:
        if timeout <= 0:
            raise ValueError("health timeout 必须大于 0")
        self.import_check = import_check
        self.command = tuple(command)
        self.timeout = timeout
        self.python_executable = python_executable
        self.config_path = config_path
        self.data_dir = data_dir
        self.upgrade_root = upgrade_root
        self.stable_root = (
            Path(Path.cwd() if stable_root is None else stable_root)
            .expanduser()
            .resolve()
        )

    def _command(self, candidate: Path) -> list[str]:
        values = {
            "{python}": self.python_executable,
            "{release_root}": str(candidate),
            "{config_path}": str(self.config_path) if self.config_path else "",
            "{data_dir}": str(self.data_dir) if self.data_dir else "",
        }
        command = [
            next(
                (replacement for key, replacement in values.items() if item == key),
                item,
            )
            for item in self.command
        ]
        return _candidate_python_command(command, candidate, self.python_executable)

    def _environment(self, candidate: Path) -> dict[str, str]:
        return _candidate_environment(
            candidate,
            config_path=self.config_path,
            data_dir=self.data_dir,
            upgrade_root=self.upgrade_root,
        )

    def _working_directory(self, candidate: Path) -> Path:
        return self.stable_root

    def _check_sync(self, candidate: Path) -> HealthCheckResult:
        package = candidate / "tenko"
        if not candidate.is_dir() or not (package / "__init__.py").is_file():
            return HealthCheckResult(False, "候选版本缺少 tenko 包入口")
        if self.command:
            try:
                completed = subprocess.run(
                    self._command(candidate),
                    check=False,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    env=self._environment(candidate),
                    cwd=self._working_directory(candidate),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return HealthCheckResult(False, f"健康检查命令执行失败: {exc}")
            if completed.returncode != 0:
                output = (completed.stderr or completed.stdout or "").strip()
                return HealthCheckResult(
                    False,
                    f"健康检查命令返回 {completed.returncode}: {output[:500]}",
                )
            return HealthCheckResult(True, "配置的健康检查命令通过")
        try:
            completed = subprocess.run(
                [self.python_executable, "-m", "compileall", "-q", str(package)],
                cwd=self._working_directory(candidate),
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=self._environment(candidate),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return HealthCheckResult(False, f"Python 编译冒烟失败: {exc}")
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return HealthCheckResult(False, f"Python 编译冒烟失败: {output[:500]}")
        return HealthCheckResult(True, "tenko 包入口和 Python 编译冒烟通过")

    def _import_check_sync(
        self, candidate: Path, plugin_ids: Sequence[str] = ()
    ) -> HealthCheckResult:
        """子进程导入宿主、内置插件和已启用的扩展插件；``plugin_ids`` 额外导入
        这些尚未启用的扩展插件（安装、更新插件时用）。"""

        try:
            completed = subprocess.run(
                [
                    self.python_executable,
                    "-c",
                    _IMPORT_CHECK_SCRIPT,
                    str(candidate),
                    *plugin_ids,
                ],
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(self.timeout, _IMPORT_CHECK_MIN_TIMEOUT),
                env=self._environment(candidate),
                cwd=self._working_directory(candidate),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return HealthCheckResult(False, f"导入检查执行失败: {exc}")
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return HealthCheckResult(False, f"导入检查失败: {output[-800:]}")
        return HealthCheckResult(True, "宿主和已启用插件导入检查通过")

    async def check(
        self,
        candidate: Path,
        *,
        phase: str,
    ) -> HealthCheckResult:
        result = await asyncio.to_thread(self._check_sync, candidate)
        # 导入检查依赖候选版本的依赖已同步，所以只在切换后/回滚阶段执行；
        # 自定义 health_command 取代整个默认检查（含导入检查）。
        if (
            result.ok
            and self.import_check
            and not self.command
            and phase != "pre-switch"
        ):
            result = await asyncio.to_thread(self._import_check_sync, candidate)
        if not result.ok:
            return HealthCheckResult(False, f"{phase}: {result.reason}")
        return result


_IMPORT_CHECK_MIN_TIMEOUT = 120
# 在子进程里用候选代码导入宿主，并通过 Entari 加载全部内置插件和已启用的扩展
# 插件。load_plugin 失败只返回错误而不抛异常，所以要逐个检查结果。
# 脚本由当前版本提供，却在候选版本的代码和依赖上运行（回滚时是更旧的版本），
# 所以只能用各版本都有的接口：旧版没有扩展插件和 source 字段，Entari 0.18 的
# load_plugin 失败时返回 None 而不是带 error 的结果。
_IMPORT_CHECK_SCRIPT = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from arclet.entari.config import EntariConfig
from arclet.entari.plugin import load_plugin
EntariConfig(Path(".tenko/entari-boot.yaml"))
import tenko.runtime
from tenko.host.plugins import PluginRuntime
runtime = PluginRuntime()
enabled = getattr(runtime, "_enabled_external_ids", set)() | set(sys.argv[2:])
infos = runtime.discover()
failed = [
    f"{name}: 没有发现这个扩展插件"
    for name in sys.argv[2:]
    if name not in {info.name for info in infos}
]
for info in infos:
    if getattr(info, "source", None) == "external" and info.name not in enabled:
        continue
    result = load_plugin(info.qualified_name)
    if not result:
        error = getattr(result, "error", None)
        detail = f"{type(error).__name__}: {error}" if error else "加载失败"
        failed.append(f"{info.name}: {detail}")
if failed:
    print("插件导入失败:\\n" + "\\n".join(failed), file=sys.stderr)
    raise SystemExit(1)
""".strip()


def _candidate_environment(
    candidate: Path,
    *,
    config_path: Path | None = None,
    data_dir: Path | None = None,
    upgrade_root: Path | None = None,
) -> dict[str, str]:
    """为候选进程准备统一的环境变量和优先导入路径。"""

    environment = os.environ.copy()
    if config_path:
        environment["TENKO_CONFIG_PATH"] = str(config_path)
    if data_dir:
        environment["TENKO_DATA_DIR"] = str(data_dir)
    if upgrade_root:
        environment["TENKO_UPGRADE_ROOT"] = str(upgrade_root)
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(candidate), existing_pythonpath) if value
    )
    return environment


def _health_ok(value: HealthCheckResult | bool) -> bool:
    return value if isinstance(value, bool) else value.ok


def _health_reason(value: HealthCheckResult | bool) -> str:
    if isinstance(value, bool):
        return "健康检查返回 False"
    return value.reason
