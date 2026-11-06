"""重启 watcher：等待旧进程退出并监视新进程启动。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Mapping, Sequence
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from loguru import logger

from .models import (
    _RESTART_WATCHER_INTERVAL,
    _RESTART_WATCHER_TIMEOUT,
    _STARTUP_FAILURE_THRESHOLD,
    _STARTUP_GRACE_PERIOD,
)


# uv run 和 ``tenko.__main__`` 每启动一次都会在这些变量上再叠一层。
_LAUNCH_LAYER_VARIABLES = ("UV_RUN_RECURSION_DEPTH", "VIRTUAL_ENV", "PYTHONPATH")


def restart_environment(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """返回重启链路使用的环境：去掉上一轮启动叠加的部分，回到手动启动时的状态。

    watcher 继承运行中 Tenko 的环境；不清理的话，每次重启 ``PATH`` 多一份
    ``.venv/bin``、``UV_RUN_RECURSION_DEPTH`` 加一（超过 100 后 ``uv run`` 拒绝启动），
    ``PYTHONPATH`` 也会累积历史版本目录。launcher 会重新加上各自的一层。
    """

    environment = {
        key: value
        for key, value in environ.items()
        if key not in _LAUNCH_LAYER_VARIABLES
    }
    script_dir = "Scripts" if os.name == "nt" else "bin"
    venv_roots = [environ.get("VIRTUAL_ENV")]
    if sys.prefix != sys.base_prefix:
        venv_roots.append(sys.prefix)
    venv_bins = {
        os.path.normcase(os.path.abspath(os.path.join(root, script_dir)))
        for root in venv_roots
        if root
    }
    if "PATH" in environment:
        environment["PATH"] = os.pathsep.join(
            entry
            for entry in environment["PATH"].split(os.pathsep)
            if entry and os.path.normcase(os.path.abspath(entry)) not in venv_bins
        )
    return environment


def spawn_restart_watcher(
    old_pid: int,
    restart_command: Sequence[str],
    *,
    stable_root: str | Path,
    watcher_dir: str | Path,
    timeout: int = _RESTART_WATCHER_TIMEOUT,
    poll_interval: float = _RESTART_WATCHER_INTERVAL,
    upgrade_root: str | Path | None = None,
    startup_grace_period: float = _STARTUP_GRACE_PERIOD,
    startup_failure_threshold: int = _STARTUP_FAILURE_THRESHOLD,
) -> bool:
    """启动等待旧 PID 的 watcher，并在升级时观察新进程的启动窗口。

    普通重启仍然只启动一次 detached 进程。只有传入 ``upgrade_root`` 且 arm
    时存在 ``activate`` handoff，watcher 才会对新进程执行 60 秒启动存活观察：
    连续两次窗口内退出后写入自动回滚 handoff，回滚目标再次失败则停止，
    不再把坏版本拉起成崩溃循环。状态切换仍由标准 launcher 处理。
    """

    if old_pid <= 0 or not restart_command:
        return False
    if timeout <= 0 or poll_interval <= 0:
        raise ValueError("watcher timeout 和 poll_interval 必须大于 0")
    if startup_grace_period <= 0:
        raise ValueError("startup_grace_period 必须大于 0")
    if type(startup_failure_threshold) is not int or startup_failure_threshold <= 0:
        raise ValueError("startup_failure_threshold 必须是正整数")

    command = tuple(str(item) for item in restart_command)
    if not command or any(not item for item in command):
        return False
    stable_root = Path(stable_root).expanduser().resolve()
    watcher_dir = Path(watcher_dir).expanduser().resolve()
    upgrade_root_path = (
        None if upgrade_root is None else Path(upgrade_root).expanduser().resolve()
    )
    record = watcher_dir / f"{old_pid}.json"
    timeout_log = watcher_dir.parent / f"watcher-timeout-{old_pid}.log"
    record_payload = {
        "old_pid": old_pid,
        "command": list(command),
        "cwd": str(stable_root),
        "timeout": timeout,
        "poll_interval": poll_interval,
        "timeout_log": str(timeout_log),
        "upgrade_root": str(upgrade_root_path) if upgrade_root_path else None,
        "startup_grace_period": startup_grace_period,
        "startup_failure_threshold": startup_failure_threshold,
        "created_at": datetime.now(UTC).isoformat(),
    }

    try:
        watcher_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            record,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            descriptor = -1
            json.dump(record_payload, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError:
        return False
    except OSError as exc:
        logger.warning("无法创建 Tenko restart watcher 记录: {}", exc)
        return False
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)

    watcher = (
        textwrap.dedent(
            """
        import json
        import os
        import subprocess
        import sys
        import time
        from datetime import datetime, timezone
        from pathlib import Path

        pid = int(sys.argv[1])
        record = Path(sys.argv[2])
        command = {command_literal}
        stable_root = {stable_root_literal}
        timeout_log = Path({timeout_log_literal})
        upgrade_root = {upgrade_root_literal}
        startup_grace_period = {startup_grace_period_literal}
        startup_failure_threshold = {startup_failure_threshold_literal}
        deadline = time.monotonic() + {timeout_literal}

        def _pid_exists(value):
            if os.name == "nt":
                # Windows 的 os.kill(pid, 0) 是发 Ctrl-C，不能用来探测进程。
                import psutil

                return psutil.pid_exists(value)
            try:
                os.kill(value, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            except OSError:
                return False
            return True

        def _read_json(path):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return None
            return value if isinstance(value, dict) else None

        def _read_pointer(path):
            value = _read_json(path)
            if value is None:
                return None
            version = value.get("version")
            pointer_path = value.get("path")
            if not isinstance(version, str) or not isinstance(pointer_path, str):
                return None
            return version, Path(pointer_path).expanduser().resolve()

        def _active_pointer():
            if upgrade_root is None:
                return None
            return _read_pointer(Path(upgrade_root) / "active.json")

        def _run_state_helper(operation, reason, attempts, target_version=""):
            if upgrade_root is None:
                return True
            helper = (
                "import sys\\n"
                "from pathlib import Path\\n"
                "from tenko.host.updater import (\\n"
                "    record_automatic_rollback_failure,\\n"
                "    record_automatic_startup_failure,\\n"
                "    request_automatic_rollback,\\n"
                ")\\n"
                "root = Path(sys.argv[1])\\n"
                "operation = sys.argv[2]\\n"
                "reason = sys.argv[3]\\n"
                "attempts = int(sys.argv[4])\\n"
                "target = sys.argv[5] or None\\n"
                "if operation == 'startup':\\n"
                "    record_automatic_startup_failure(\\n"
                "        root, target_version=target, reason=reason, "
                "failed_attempts=attempts\\n"
                "    )\\n"
                "elif operation == 'request-rollback':\\n"
                "    if not request_automatic_rollback(\\n"
                "        root, reason=reason, failed_attempts=attempts\\n"
                "    ):\\n"
                "        raise SystemExit(2)\\n"
                "elif operation == 'rollback-failure':\\n"
                "    record_automatic_rollback_failure(\\n"
                "        root, target_version=target, reason=reason, "
                "failed_attempts=attempts\\n"
                "    )\\n"
            )
            helper_environment = os.environ.copy()
            existing_pythonpath = helper_environment.get("PYTHONPATH")
            helper_environment["PYTHONPATH"] = os.pathsep.join(
                value
                for value in (str(stable_root), existing_pythonpath)
                if value
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    helper,
                    str(upgrade_root),
                    operation,
                    reason,
                    str(attempts),
                    target_version,
                ],
                cwd=stable_root,
                env=helper_environment,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                print(
                    "Tenko restart watcher state helper failed: "
                    + (detail or str(completed.returncode)),
                    file=sys.stderr,
                )
                return False
            return True

        def _launch():
            try:
                if os.name == "posix":
                    return subprocess.Popen(
                        command,
                        cwd=stable_root,
                        start_new_session=True,
                    ), None
                return subprocess.Popen(command, cwd=stable_root), None
            except OSError as error:
                return None, "无法启动进程: " + str(error)

        def _stop_process(process):
            if process is None or process.poll() is not None:
                return
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    return
                process.wait(timeout=5)
            except OSError:
                return

        def _wait_for_startup(process):
            startup_deadline = time.monotonic() + startup_grace_period
            while time.monotonic() < startup_deadline:
                return_code = process.poll()
                if return_code is not None:
                    process.wait()
                    return False, "进程在启动窗口内退出，returncode=" + str(return_code)
                time.sleep({poll_interval_literal})
            if process.poll() is None:
                return True, ""
            process.wait()
            return False, "进程在启动窗口结束时退出"

        initial_handoff = None
        target_version = ""
        target_path = None
        if upgrade_root is not None:
            initial_handoff = _read_json(Path(upgrade_root) / "handoff.json")
            if (
                initial_handoff is not None
                and initial_handoff.get("action") == "activate"
            ):
                value = initial_handoff.get("version")
                path_value = initial_handoff.get("path")
                if isinstance(value, str):
                    target_version = value
                if isinstance(path_value, str):
                    target_path = Path(path_value).expanduser().resolve()
        monitor_upgrade = target_path is not None

        def _run_upgrade_watchdog():
            for attempt in range(1, startup_failure_threshold + 1):
                process, launch_error = _launch()
                if process is None:
                    alive, failure_reason = False, launch_error
                else:
                    alive, failure_reason = _wait_for_startup(process)
                if alive:
                    active = _active_pointer()
                    if (
                        target_path is not None
                        and active is not None
                        and active[1] != target_path
                    ):
                        # 启动前健康检查失败时，launcher 已经把 active 恢复到旧版本；
                        # 不要把这次已经完成的恢复再次当作候选版本崩溃。
                        return
                    if target_path is not None and (
                        active is None or active[1] != target_path
                    ):
                        _stop_process(process)
                        alive = False
                        failure_reason = "启动后 active 指针未指向候选版本"
                    else:
                        return
                # 启动前健康检查失败时，launcher 已经把 active 恢复到旧版本；
                # 不要把这次已经完成的恢复再次当作候选版本崩溃。
                active = _active_pointer()
                if (
                    target_path is not None
                    and active is not None
                    and active[1] != target_path
                ):
                    return
                reason = failure_reason or "进程启动失败"
                _run_state_helper(
                    "startup",
                    reason,
                    attempt,
                    target_version,
                )
            rollback_target = _read_pointer(Path(upgrade_root) / "previous.json")
            rollback_reason = (
                "候选版本在启动宽限期内连续 "
                + str(startup_failure_threshold)
                + " 次退出"
            )
            if not _run_state_helper(
                "request-rollback",
                rollback_reason,
                startup_failure_threshold,
                rollback_target[0] if rollback_target is not None else "",
            ):
                _run_state_helper(
                    "rollback-failure",
                    "无法写入自动回滚 handoff",
                    startup_failure_threshold,
                    rollback_target[0] if rollback_target is not None else "",
                )
                return
            process, launch_error = _launch()
            if process is None:
                _run_state_helper(
                    "rollback-failure",
                    launch_error or "无法启动回滚版本",
                    startup_failure_threshold,
                    rollback_target[0] if rollback_target is not None else "",
                )
                return
            alive, failure_reason = _wait_for_startup(process)
            active = _active_pointer()
            rollback_path = rollback_target[1] if rollback_target is not None else None
            if alive:
                if rollback_path is not None and active is not None:
                    if active[1] == rollback_path:
                        return
                _stop_process(process)
                alive = False
                failure_reason = "回滚版本启动后 active 指针未切换"
            _run_state_helper(
                "rollback-failure",
                failure_reason or "回滚版本在启动窗口内退出",
                startup_failure_threshold,
                rollback_target[0] if rollback_target is not None else "",
            )

        try:
            while time.monotonic() < deadline and _pid_exists(pid):
                time.sleep({poll_interval_literal})
            if _pid_exists(pid):
                timeout_entry = dict(
                    event="watcher-timeout",
                    old_pid=pid,
                    timeout_at=datetime.now(timezone.utc).isoformat(),
                    command=command,
                )
                try:
                    with timeout_log.open("a", encoding="utf-8") as file:
                        file.write(json.dumps(timeout_entry, ensure_ascii=False))
                        file.write("\\n")
                except OSError as error:
                    print(
                        f"Tenko restart watcher timeout log failed: {{error}}",
                        file=sys.stderr,
                    )
                sys.exit(0)
            if monitor_upgrade:
                _run_upgrade_watchdog()
            else:
                process, launch_error = _launch()
                if process is None:
                    print(
                        "Tenko restart watcher launch failed: "
                        + (launch_error or "unknown error"),
                        file=sys.stderr,
                    )
        finally:
            record.unlink(missing_ok=True)
        """
        )
        .strip()
        .format(
            command_literal=repr(list(command)),
            stable_root_literal=repr(str(stable_root)),
            timeout_log_literal=repr(str(timeout_log)),
            upgrade_root_literal=repr(
                str(upgrade_root_path) if upgrade_root_path else None
            ),
            timeout_literal=repr(float(timeout)),
            poll_interval_literal=repr(float(poll_interval)),
            startup_grace_period_literal=repr(float(startup_grace_period)),
            startup_failure_threshold_literal=repr(startup_failure_threshold),
        )
    )
    watcher_command = [sys.executable, "-c", watcher, str(old_pid), str(record)]
    popen_kwargs: dict[str, Any] = {
        "cwd": str(stable_root),
        "stdin": subprocess.DEVNULL,
        # watcher、它拉起的 launcher 和自动回滚都从这份干净环境出发。
        "env": restart_environment(),
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    try:
        subprocess.Popen(watcher_command, **popen_kwargs)
    except OSError as exc:
        record.unlink(missing_ok=True)
        logger.warning("无法启动 Tenko restart watcher: {}", exc)
        return False
    return True
