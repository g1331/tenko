"""按版本锁文件同步依赖。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

from loguru import logger

from ..uv import UvError, run_uv
from .models import DependencySyncError


_DEPENDENCY_SYNC_TIMEOUT = 600


def _lock_digest(release_root: Path) -> str:
    return hashlib.sha256((release_root / "uv.lock").read_bytes()).hexdigest()


class UvDependencySync:
    """按版本目录的 ``uv.lock`` 把依赖同步到当前运行的虚拟环境。

    扩展插件与宿主共用同一个虚拟环境，所以不用 ``uv sync`` 默认的精确同步
    （会卸载锁文件之外的插件依赖），而是 ``--inexact``：只安装、升级或降级锁文件
    里的包，不删除多余的包。``--no-editable`` 把 ``tenko-api`` 作为普通包装入：
    环境里登记的版本要跟随宿主，插件解析依赖时看到的才是真实版本；可编辑安装又会
    指向可能被清理的版本目录。升级和回滚都调用它：回滚时传入上一版本目录，
    即恢复上一版本的锁文件。
    """

    def __init__(
        self,
        *,
        environment: str | Path | None = None,
        uv: str | None = None,
        timeout: int = _DEPENDENCY_SYNC_TIMEOUT,
    ) -> None:
        self.environment = Path(environment or sys.prefix)
        self.uv = uv
        self.timeout = timeout

    def _sync(self, release_root: Path) -> None:
        if not (release_root / "uv.lock").is_file():
            # 早于 uv 迁移的旧版本没有锁文件，保持环境现状。
            logger.warning("Tenko 版本 {} 没有 uv.lock，跳过依赖同步", release_root)
            return
        environment = os.environ.copy()
        environment["UV_PROJECT_ENVIRONMENT"] = str(self.environment)
        try:
            run_uv(
                [
                    "sync",
                    "--frozen",
                    "--inexact",
                    "--no-editable",
                    "--project",
                    str(release_root),
                ],
                uv=self.uv,
                timeout=self.timeout,
                env=environment,
            )
        except UvError as exc:
            raise DependencySyncError(f"依赖同步失败: {exc}") from exc
        try:
            self._stamp.write_text(_lock_digest(release_root), encoding="utf-8")
        except OSError as exc:
            logger.warning("Tenko 依赖同步标记写入失败: {}", exc)

    @property
    def _stamp(self) -> Path:
        # 记录环境最后按哪份 uv.lock 同步；旧版升级器不写它，缺失即视为未同步。
        return self.environment / "tenko-uv-lock.sha256"

    def ensure(self, release_root: Path) -> bool:
        """环境不是按该版本 uv.lock 同步的就补同步，返回是否同步过。

        旧版升级器切换版本时不同步依赖，新代码跑在旧依赖上会在启动中途崩溃；
        版本自己启动时补这一步，不依赖把它切换进来的那个升级器。
        """

        if not (release_root / "uv.lock").is_file():
            return False
        try:
            if self._stamp.read_text(encoding="utf-8") == _lock_digest(release_root):
                return False
        except OSError:
            pass
        self._sync(release_root)
        return True

    async def __call__(self, release_root: Path) -> None:
        await asyncio.to_thread(self._sync, release_root)
