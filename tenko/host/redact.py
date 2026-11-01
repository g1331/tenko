"""隐藏错误文本和地址中的凭据（Git、uv 的输出常带 ``user:token@host``）。"""

from __future__ import annotations

import re

_URL_CREDENTIALS_PATTERN = re.compile(
    r"(?P<prefix>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@"
)
_SCP_CREDENTIALS_PATTERN = re.compile(
    r"(?<![\w/@])[^/@\s:]+(?::[^/@\s]*)?@(?=[^/@\s:]+:)"
)


def redact_text(value: object) -> str:
    """隐藏错误文本和地址中的凭据。"""

    text = str(value)
    text = _URL_CREDENTIALS_PATTERN.sub(r"\g<prefix>***@", text)
    return _SCP_CREDENTIALS_PATTERN.sub("***@", text)
