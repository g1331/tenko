from __future__ import annotations

import json

from pathlib import Path

import pytest

from tenko import __main__ as main_module
from tenko.config import TenkoConfig
from tenko.host.updater import InstallResult, RollbackResult, UpgradeLayout, Version


@pytest.fixture(autouse=True)
def _notify_target_configured(monkeypatch) -> None:
    # 这些测试没有配置文件；通知接收方的检查单独测试。
    monkeypatch.setattr(TenkoConfig, "notify_target_error", lambda self: None)


def test_main_refuses_to_start_without_any_notify_target(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.undo()
    monkeypatch.chdir(tmp_path)

    assert main_module.main(["--dry-run"]) == 2

    config = tmp_path / "tenko.toml"
    config.write_text('[notify]\ngroup = "40001"\n', encoding="utf-8")
    assert main_module.main(["--config", str(config), "--dry-run"]) == 0


def _write_project_version(root: Path, version: str = "1.0.0") -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nversion = "{version}"\n', encoding="utf-8"
    )


def test_main_without_active_release_runs_stable_code(
    monkeypatch, tmp_path: Path
) -> None:
    _write_project_version(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[TenkoConfig] = []

    from tenko import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "run", calls.append)

    assert main_module.main([]) == 0
    assert len(calls) == 1
    assert calls[0].upgrade.current_version is None


def test_main_dry_run_does_not_consume_handoff(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    handoff = tmp_path / ".tenko" / "upgrades" / "handoff.json"
    handoff.parent.mkdir(parents=True)
    handoff.write_text('{"action":"activate"}\n', encoding="utf-8")
    before = handoff.read_bytes()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("dry-run must not enter startup bootstrap")

    monkeypatch.setattr(main_module, "_run_startup_bootstrap", fail_if_called)

    assert main_module.main(["--dry-run"]) == 0
    assert handoff.read_bytes() == before
    assert not (tmp_path / ".tenko" / "upgrades" / "failed-handoffs").exists()


def test_main_applies_handoff_and_reexecutes_active_release(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = tmp_path / ".tenko" / "upgrades" / "versions" / "2.0.0"
    (candidate / "tenko").mkdir(parents=True)
    layout = UpgradeLayout(tmp_path / ".tenko" / "upgrades")
    layout.write_pointer(candidate, Version("2.0.0"))
    layout.handoff_file.write_text('{"action":"activate"}\n', encoding="utf-8")
    calls: dict[str, object] = {"apply": []}

    class FakeManager:
        def __init__(self) -> None:
            self.layout = layout

        @classmethod
        def from_config(cls, _config, *, project_root):
            calls["project_root"] = project_root
            return cls()

        async def apply_handoff(self):
            calls["apply"].append(True)
            return InstallResult(True, Version("2.0.0"), candidate, False, "ok")

    import tenko.host.updater as updater_module

    monkeypatch.setattr(updater_module, "UpgradeManager", FakeManager)
    reexec: list[tuple[Path, Path, list[str]]] = []
    monkeypatch.setattr(
        main_module,
        "_exec_release_root",
        lambda release, stable, arguments: reexec.append(
            (release, stable, list(arguments))
        ),
    )
    monkeypatch.chdir(tmp_path)

    arguments = ["--config", "config/tenko.toml"]
    assert main_module.main(arguments) == 0
    assert calls["apply"] == [True]
    assert calls["project_root"] == tmp_path.resolve()
    assert reexec == [
        (candidate.resolve(), tmp_path.resolve(), arguments),
    ]


def test_main_continues_with_recovered_active_after_rolled_back_handoff(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = tmp_path / ".tenko" / "upgrades" / "versions" / "1.0.0"
    (candidate / "tenko").mkdir(parents=True)
    layout = UpgradeLayout(tmp_path / ".tenko" / "upgrades")
    layout.write_pointer(candidate, Version("1.0.0"))
    layout.handoff_file.write_text('{"action":"activate"}\n', encoding="utf-8")
    calls: list[bool] = []

    class FakeManager:
        def __init__(self) -> None:
            self.layout = layout

        @classmethod
        def from_config(cls, _config, *, project_root):
            assert project_root == tmp_path.resolve()
            return cls()

        async def apply_handoff(self):
            calls.append(True)
            return InstallResult(
                False, Version("2.0.0"), candidate, True, "health check failed"
            )

    import tenko.host.updater as updater_module

    monkeypatch.setattr(updater_module, "UpgradeManager", FakeManager)
    reexec: list[Path] = []
    monkeypatch.setattr(
        main_module,
        "_exec_release_root",
        lambda release, _stable, _arguments: reexec.append(release),
    )
    monkeypatch.chdir(tmp_path)

    assert main_module.main([]) == 0
    assert calls == [True]
    assert reexec == [candidate.resolve()]


def test_main_stops_when_automatic_rollback_handoff_fails(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = tmp_path / ".tenko" / "upgrades" / "versions" / "2.0.0"
    (candidate / "tenko").mkdir(parents=True)
    layout = UpgradeLayout(tmp_path / ".tenko" / "upgrades")
    layout.write_pointer(candidate, Version("2.0.0"))
    layout.write_handoff(
        {
            "action": "rollback",
            "version": "1.0.0",
            "path": str(tmp_path / ".tenko" / "upgrades" / "versions" / "1.0.0"),
            "automatic_rollback": True,
        }
    )

    class FakeManager:
        def __init__(self) -> None:
            self.layout = layout

        @classmethod
        def from_config(cls, _config, *, project_root):
            assert project_root == tmp_path.resolve()
            return cls()

        async def apply_handoff(self):
            return RollbackResult(
                False,
                Version("1.0.0"),
                candidate,
                True,
                "rollback health check failed",
            )

    import tenko.host.updater as updater_module

    monkeypatch.setattr(updater_module, "UpgradeManager", FakeManager)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="1"):
        main_module._run_startup_bootstrap(TenkoConfig(), [])
    # 进程停止前留下提示，人工重启后由启动通知告诉管理员。
    notice = json.loads(layout.recovery_notice_file.read_text(encoding="utf-8"))
    assert "自动回滚也失败" in notice["message"] and notice["version"] == "2.0.0"


@pytest.mark.parametrize(
    ("stable_version", "active_version", "current_root", "expected_release"),
    [
        ("2.0.0", "1.0.0", "old", "stable"),
        ("1.0.0", "1.0.0", "stable", "active"),
        ("1.0.0", "2.0.0", "stable", "active"),
    ],
    ids=["stable-newer", "same-version", "active-newer"],
)
def test_startup_bootstrap_compares_active_with_stable_version(
    monkeypatch,
    tmp_path: Path,
    stable_version: str,
    active_version: str,
    current_root: str,
    expected_release: str,
) -> None:
    candidate = tmp_path / ".tenko" / "upgrades" / "versions" / "candidate"
    (candidate / "tenko").mkdir(parents=True)
    layout = UpgradeLayout(tmp_path / ".tenko" / "upgrades")
    layout.write_pointer(candidate, Version(active_version))

    class FakeManager:
        def __init__(self) -> None:
            self.layout = layout

        @classmethod
        def from_config(cls, _config, *, project_root):
            assert project_root == tmp_path.resolve()
            return cls()

    import tenko.host.updater as updater_module

    monkeypatch.setattr(updater_module, "UpgradeManager", FakeManager)
    monkeypatch.setattr(
        updater_module,
        "read_project_version",
        lambda _root: Version(stable_version),
    )
    monkeypatch.setattr(
        main_module,
        "_current_code_root",
        lambda: (tmp_path / current_root).resolve(),
    )
    reexec: list[Path] = []
    monkeypatch.setattr(
        main_module,
        "_exec_release_root",
        lambda release, _stable, _arguments: reexec.append(release),
    )
    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        main_module.logger,
        "warning",
        lambda *arguments: warnings.append(arguments),
    )
    monkeypatch.chdir(tmp_path)

    result = main_module._run_startup_bootstrap(TenkoConfig(), [])

    assert result is True
    expected = tmp_path if expected_release == "stable" else candidate
    assert reexec == [expected.resolve()]
    if expected_release == "stable":
        assert len(warnings) == 1
        assert "稳定根版本 {} 新于 active 指针 {}" in warnings[0][0]
    else:
        assert warnings == []
