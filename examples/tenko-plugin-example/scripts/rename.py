"""把模板改成你自己的插件：python3 scripts/rename.py <插件 ID> --name <显示名> --author <作者>

一次改完分发名、模块名、src 目录、入口、插件配置节、数据目录、仓库地址和作者，重写
README，最后删除本脚本。只用标准库，在 ``uv sync`` 之前运行即可。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_ID = "example"
OLD_MODULE = "tenko_plugin_example"
OLD_DIST = "tenko-plugin-example"
FILES = ("pyproject.toml", "tests/conftest.py", "tests/test_plugin.py")
README = """# {dist}

{name}：Tenko 扩展插件。

## 安装

```text
uv pip install {dist}
```

在 Tenko 的 WebUI 插件页启用“{name}”，或在聊天里发送 `/启用插件 {id}`。
如有配置，写在 `config/tenko.toml` 的 `[plugin_config.{id}]`。

## 开发

```text
uv sync
uv run pytest -q
uv run ruff check .
```

插件开发说明见 [tenko-plugin-example](https://github.com/g1331/tenko/tree/main/examples/tenko-plugin-example)。
"""


def _origin_repo() -> str | None:
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def rename(plugin_id: str, name: str, author: str, repo: str) -> None:
    module = f"tenko_plugin_{plugin_id}"
    dist = f"tenko-plugin-{plugin_id.replace('_', '-')}"
    url = f"https://github.com/{repo}"
    replacements = [
        (OLD_MODULE, module),
        (OLD_DIST, dist),
        (f'"{OLD_ID}"', f'"{plugin_id}"'),
        (f"plugin_config.{OLD_ID}", f"plugin_config.{plugin_id}"),
        (f"plugin-data/{OLD_ID}/", f"plugin-data/{plugin_id}/"),
        (f"{OLD_ID}_note", f"{plugin_id}_note"),
        (f" {OLD_ID} ", f" {plugin_id} "),
        (f"\n{OLD_ID} = ", f"\n{plugin_id} = "),
        ("示例插件", name),
        ('author=["13"]', f"author=[{author!r}]".replace("'", '"')),
    ]
    source = ROOT / "src" / OLD_MODULE
    files = [ROOT / f for f in FILES] + [source / "__init__.py"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for old, new in replacements:
            text = text.replace(old, new)
        if path.name == "pyproject.toml":
            text = re.sub(r'(?m)^version = ".*"$', 'version = "0.1.0"', text, count=1)
            text = re.sub(
                r'(?m)^description = ".*"$', f'description = "{name}"', text, count=1
            )
            text = re.sub(r'(?m)^(Homepage|Repository) = ".*"$', rf'\1 = "{url}"', text)
        path.write_text(text, encoding="utf-8")
    source.rename(source.with_name(module))
    (ROOT / "README.md").write_text(
        README.format(dist=dist, name=name, id=plugin_id), encoding="utf-8"
    )
    shutil.rmtree(ROOT / "scripts")


def main() -> int:
    parser = argparse.ArgumentParser(description="把模板改成你自己的插件")
    parser.add_argument(
        "plugin_id", help="插件 ID：小写字母开头，只含小写字母、数字和下划线"
    )
    parser.add_argument("--name", required=True, help="显示名，例如 天气")
    parser.add_argument("--author", required=True, help="作者名")
    parser.add_argument("--repo", help="GitHub 仓库 owner/name，默认取 git origin")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.plugin_id):
        parser.error("插件 ID 只能用小写字母开头，只含小写字母、数字和下划线")
    if not (ROOT / "src" / OLD_MODULE).is_dir():
        parser.error("已经改过名了")
    repo = args.repo or _origin_repo()
    if not repo:
        parser.error("读不到 git origin，请用 --repo owner/name 指定仓库")
    rename(args.plugin_id, args.name, args.author, repo)
    print(
        f"已改为 {args.plugin_id}（仓库 {repo}）。接下来：uv sync && uv run pytest -q"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
