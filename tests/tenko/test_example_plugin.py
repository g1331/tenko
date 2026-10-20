"""仓库自带的示例插件模板：运行它自己的测试，保证模板与当前宿主和 tenko-api 同步。"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "tenko-plugin-example"


def test_example_plugin_suite_passes_against_this_checkout(tmp_path: Path) -> None:
    """示例的测试需要 ``tenko.plugins`` 入口，而它没有被安装进当前环境。

    这里按它的 pyproject 声明生成一个最小 dist-info 放在 PYTHONPATH 上，
    使入口由真实的 importlib.metadata 发现；宿主和 tenko-api 取自当前检出。
    """

    project = tomllib.loads((EXAMPLE / "pyproject.toml").read_text(encoding="utf-8"))
    name = project["project"]["name"]
    version = project["project"]["version"]
    dist_info = tmp_path / "dist" / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )
    entries = "".join(
        f"{plugin_id} = {module}\n"
        for plugin_id, module in project["project"]["entry-points"][
            "tenko.plugins"
        ].items()
    )
    (dist_info / "entry_points.txt").write_text(
        f"[tenko.plugins]\n{entries}", encoding="utf-8"
    )

    python_path = os.pathsep.join(
        [str(dist_info.parent), str(EXAMPLE / "src"), str(ROOT)]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(EXAMPLE),
            "-c",
            str(EXAMPLE / "pyproject.toml"),
            str(EXAMPLE / "tests"),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": python_path},
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr
