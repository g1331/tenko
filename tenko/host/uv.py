"""调用 uv：宿主依赖同步和插件管理共用的定位、编码、超时与输出脱敏。"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from .redact import redact_text


class UvError(RuntimeError):
    """uv 找不到、无法执行，或（``check=True`` 时）返回非零。"""


def find_uv(explicit: str | None = None) -> str:
    """定位 uv 可执行文件：显式指定、环境变量 ``UV``，最后是 PATH。"""

    uv = explicit or os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise UvError("找不到 uv 可执行文件（可用环境变量 UV 指定）")
    return uv


def run_uv(
    args: Sequence[str],
    *,
    uv: str | None = None,
    timeout: float,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """运行 ``uv <args>`` 并捕获输出；错误信息里的凭据已脱敏、只保留末尾 800 字。"""

    command = [find_uv(uv), *args]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=None if env is None else dict(env),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UvError(f"uv 无法执行: {error}") from error
    if check and completed.returncode != 0:
        output = redact_text((completed.stderr or completed.stdout or "").strip())
        raise UvError(f"uv 返回 {completed.returncode}: {output[-800:]}")
    return completed
