"""有界日志读取；仅允许官方日志目录中的普通日志文件。"""

from __future__ import annotations

import gzip
import re
import traceback
import threading
import zipfile
from collections import deque
from pathlib import Path
from collections.abc import Callable


class LogReader:
    def __init__(self, secrets: Callable[[], tuple[str, ...]]) -> None:
        self.secrets = secrets
        self.records: deque[dict] = deque(maxlen=1000)
        self.sequence = 0
        self.lock = threading.Lock()

    def redact(self, text: str) -> str:
        for value in sorted(self.secrets(), key=len, reverse=True):
            if value:
                text = text.replace(value, "[REDACTED]")
        text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", text)
        return re.sub(
            r"""(?i)((?:access_token|admin_token|satori_token|token|password)["']?\s*[:=]\s*["']?)[^\s,"'&}]+""",
            r"\1[REDACTED]",
            text,
        )

    def sink(self, message) -> None:
        record = message.record
        if record.get("name") == "uvicorn.access" and "/webui" in record["message"]:
            return
        text = record["message"]
        # logger.exception 的堆栈在 record["exception"] 里，message 只有一句提示。
        exception = record.get("exception")
        if exception is not None and exception.type is not None:
            text += "\n" + "".join(
                traceback.format_exception(
                    exception.type, exception.value, exception.traceback
                )
            ).rstrip("\n")
        text = self.redact(text)
        if len(text) > 4000:  # 保留开头的提示和结尾真正的异常行
            text = f"{text[:1000]}\n…\n{text[-3000:]}"
        with self.lock:
            self.sequence += 1
            self.records.append(
                {
                    "id": self.sequence,
                    "time": record["time"].isoformat(),
                    "level": record["level"].name,
                    "message": text,
                }
            )

    def live(self, after: int, query: str, level: str) -> dict:
        with self.lock:
            rows = list(self.records)
            cursor = self.sequence
        return {
            "records": [
                row
                for row in rows
                if row["id"] > after
                and query.casefold() in row["message"].casefold()
                and (not level or row["level"] == level)
            ],
            "cursor": cursor,
            "reset": after > cursor,
            "truncated": bool(rows and after and after < rows[0]["id"] - 1),
        }

    @staticmethod
    def files(directory: Path | None) -> list[str]:
        if directory is None or not directory.is_dir():
            return []
        return sorted(
            (
                p.name
                for p in directory.iterdir()
                if not p.is_symlink()
                and p.is_file()
                and (
                    p.name.endswith(".log")
                    or p.name.endswith(".log.gz")
                    or p.name.endswith(".log.zip")
                )
            ),
            reverse=True,
        )

    @staticmethod
    def _tail(stream, maximum: int) -> tuple[bytes, bool]:
        """流式读取并只保留最后 maximum 字节，返回数据与是否发生丢弃。"""

        buffer = bytearray()
        cut = False
        while chunk := stream.read(1024 * 1024):
            buffer += chunk
            if len(buffer) > maximum:
                del buffer[: len(buffer) - maximum]
                cut = True
        return bytes(buffer), cut

    def history(self, directory: Path | None, name: str, query: str) -> dict:
        if directory is None or name not in self.files(directory):
            raise ValueError("日志文件不存在或不可读取")
        path = directory / name
        # shortcut: 每个文件只扫描末尾 8 MiB；日志规模增长后改为索引检索。
        maximum = 8 * 1024 * 1024
        if name.endswith(".gz"):
            with gzip.open(path, "rb") as stream:
                data, cut = self._tail(stream, maximum)
        elif name.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != 1:
                    raise ValueError("压缩日志必须只包含一个文件")
                with archive.open(names[0]) as stream:
                    data, cut = self._tail(stream, maximum)
        else:
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - maximum))
                data, cut = stream.read(), size > maximum
        if cut:
            # 截断点可能落在行中间，丢弃首个不完整的行。
            data = data.partition(b"\n")[2]
        lines = self.redact(data.decode("utf-8", errors="replace")).splitlines()
        matches = [line[:4000] for line in lines if query.casefold() in line.casefold()]
        return {
            "lines": matches[-500:],
            "truncated": cut or len(matches) > 500,
            "matched": len(matches),
            "scan_limit_bytes": maximum,
        }
