"""原子写文件：读者要么看到旧内容，要么看到完整的新内容。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """写入同目录的唯一临时文件并刷盘，再 ``os.replace`` 到目标路径。

    临时文件名带随机后缀，同时写同一目标的调用不会互相覆盖临时文件；
    写入或替换失败时删除临时文件，原文件保持不变。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
