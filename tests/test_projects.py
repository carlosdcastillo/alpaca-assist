from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.projects import list_projects
from core.projects import load_project
from core.projects import prepare_workspace
from core.projects import probe_workspace
from core.projects import workspace_changes


def _write_project(root: Path, name: str = "alpaca") -> Path:
    project = root / name
    project.mkdir(parents=True)
    (project / "project.toml").write_text(
        'repo_url = "git@example.test:org/repo.git"\n'
        'branch = "main"\n'
        'workspace_base = "~/pack-workspaces"\n',
        encoding="utf-8",
    )
    (project / "RUNBOOK.md").write_text("Always run tests.", encoding="utf-8")
    (project / "SPINUP.md").write_text("Install dependencies.", encoding="utf-8")
    return project


def test_load_project_composes_host_overlay_and_remote_workspace(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    (project / "hosts").mkdir()
    (project / "hosts" / "builder.md").write_text(
        "Use the large build cache.",
        encoding="utf-8",
    )

    config = load_project("alpaca", host="user@builder", projects_dir=tmp_path)

    assert "Always run tests." in config.runbook
    assert "Use the large build cache." in config.runbook
    assert config.spinup == "Install dependencies."
    # Expansion happens on the Pack host, not on the local machine.
    assert config.workspace_path("session-1") == "~/pack-workspaces/alpaca-session-1"


def test_list_projects_skips_invalid_definitions(tmp_path: Path) -> None:
    _write_project(tmp_path, "valid")
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "project.toml").write_text('branch = "main"\n', encoding="utf-8")

    assert [project.name for project in list_projects(tmp_path)] == ["valid"]


def test_prepare_and_probe_workspace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(source)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"],
        check=True,
    )
    (source / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    workspace = tmp_path / "workspaces" / "project-session"
    payload = {
        "workspace_path": str(workspace),
        "repo_url": str(source),
        "branch": "main",
    }

    first = prepare_workspace(payload)
    second = prepare_workspace(payload)
    status = probe_workspace(first)

    assert first == second == str(workspace.resolve())
    assert status["exists"] is True
    assert status["is_git"] is True
    assert status["branch"] == "main"
    assert status["dirty"] == 0
    assert status["unpushed"] == 0

    (workspace / "README.md").write_text("changed\n", encoding="utf-8")
    assert probe_workspace(first)["dirty"] == 1


def test_workspace_naming_rejects_unknown_placeholder(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    (project / "project.toml").write_text(
        'repo_url = "example"\nworkspace_naming = "{unknown}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="workspace_naming"):
        load_project("alpaca", projects_dir=tmp_path)


def test_internal_tools_default_to_pack_workspace(tmp_path: Path, monkeypatch) -> None:
    import internal_tools
    from shell_executor import ShellExecutor

    monkeypatch.setenv("ALPACA_WORKSPACE", str(tmp_path))
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")

    listed = internal_tools.list_files({})
    read = internal_tools.read_file({"filepath": "hello.txt"})
    shell = ShellExecutor().run("pwd")

    assert "hello.txt" in listed["content"][0]["text"]
    assert "hello" in read["content"][0]["text"]
    assert shell.cwd == str(tmp_path)
    if os.name == "nt":
        # ShellExecutor resolves "pwd" to Git for Windows' MSYS pwd.exe,
        # which prints its own POSIX-mount translation of the cwd (e.g.
        # the temp dir under a /tmp mount point) rather than the literal
        # Windows path — a quirk of that binary, not of ShellExecutor.
        # shell.cwd above already proves the right directory was used.
        assert shell.stdout.strip()
    else:
        assert shell.stdout.strip() == str(tmp_path)


def test_project_prompt_includes_runbook_and_one_shot_spinup() -> None:
    from core.app_core import AppCore

    core = AppCore.__new__(AppCore)
    # Lightweight stand-in for SkillManager — only to_prompt_xml() is used below.
    core.skill_manager = SimpleNamespace(to_prompt_xml=lambda: "<skills />")  # type: ignore[assignment]
    tab = SimpleNamespace(
        project_name="alpaca",
        workspace_path="/home/user/workspaces/alpaca-session",
        project_runbook="Always run tests.",
        project_spinup="Install dependencies.",
        project_spinup_pending=True,
    )

    first_prompt = core.get_system_prompt(tab)
    tab.project_spinup_pending = False
    later_prompt = core.get_system_prompt(tab)

    assert "<skills />" in first_prompt
    assert "Always run tests." in first_prompt
    assert "Install dependencies." in first_prompt
    assert "/home/user/workspaces/alpaca-session" in first_prompt
    assert "Always run tests." in later_prompt
    assert "Install dependencies." not in later_prompt


def test_pack_daemon_configures_project_context(monkeypatch, tmp_path: Path) -> None:
    from pack_daemon import PackDaemonAdapter
    from pack_daemon import make_dispatcher

    workspace = str(tmp_path / "workspace")
    monkeypatch.setenv("ALPACA_WORKSPACE", "before-test")
    monkeypatch.setattr("pack_daemon.prepare_workspace", lambda payload: workspace)
    monkeypatch.setattr(
        "pack_daemon.probe_workspace",
        lambda path: {"workspace_path": path, "exists": True, "is_git": True},
    )
    tab = MagicMock()
    tab.is_streaming = False
    tab.project_name = None
    tab.workspace_path = None
    core = MagicMock()
    dispatch = make_dispatcher(tab, PackDaemonAdapter(), False, core)

    result = dispatch(
        "configure_project",
        {
            "name": "alpaca",
            "repo_url": "example",
            "workspace_path": workspace,
            "runbook": "Always run tests.",
            "spinup": "Install dependencies.",
            "fingerprint": "abc123",
        },
    )

    assert result["workspace_path"] == workspace
    assert tab.project_name == "alpaca"
    assert tab.project_runbook == "Always run tests."
    assert tab.project_spinup == "Install dependencies."
    assert tab.project_spinup_pending is True
    assert os.environ["ALPACA_WORKSPACE"] == workspace
    core.save_session.assert_called_once_with()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)


def test_workspace_changes_reports_status_and_per_file_diffs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    (workspace / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (workspace / "removed.txt").write_text("gone\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    (workspace / "tracked.txt").write_text("one\nTWO\n", encoding="utf-8")
    (workspace / "removed.txt").unlink()
    (workspace / "fresh.txt").write_text("brand new\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)

    changes = workspace_changes(str(workspace))
    by_path = {entry["path"]: entry for entry in changes["entries"]}

    assert changes["is_git"] is True
    assert changes["branch"] == "main"
    assert changes["head"].endswith("initial")
    assert set(by_path) == {"tracked.txt", "removed.txt", "fresh.txt"}
    # Staged and unstaged work alike: both are diffed against HEAD.
    assert "+TWO" in by_path["tracked.txt"]["diff"]
    assert "-gone" in by_path["removed.txt"]["diff"]
    # Untracked files have no index entry, so they diff against /dev/null.
    assert by_path["fresh.txt"]["untracked"] is True
    assert "+brand new" in by_path["fresh.txt"]["diff"]
    assert changes["truncated"] is False
    assert changes["omitted_files"] == 0


def test_workspace_changes_bounds_large_diffs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("core.projects.DIFF_FILE_LIMIT", 1)
    monkeypatch.setattr("core.projects.DIFF_FILE_BYTES", 120)
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    (workspace / "a.txt").write_text("line\n" * 400, encoding="utf-8")
    (workspace / "b.txt").write_text("other\n", encoding="utf-8")

    changes = workspace_changes(str(workspace))

    assert len(changes["entries"]) == 1
    assert changes["omitted_files"] == 1
    assert changes["truncated"] is True
    assert len(changes["entries"][0]["diff"]) == 120
    assert changes["entries"][0]["truncated"] is True


def test_workspace_changes_on_clean_and_non_git_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    plain = tmp_path / "plain"
    plain.mkdir()

    clean = workspace_changes(str(workspace))
    not_git = workspace_changes(str(plain))
    missing = workspace_changes(str(tmp_path / "nowhere"))

    assert clean["is_git"] is True
    assert clean["entries"] == []
    # No commits yet, so there is no HEAD to name.
    assert clean["head"] is None
    assert not_git["exists"] is True and not_git["is_git"] is False
    assert missing["exists"] is False and missing["is_git"] is False


def test_workspace_changes_handles_renames_and_spaces(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    (workspace / "old name.txt").write_text("stable\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "mv", "old name.txt", "new name.txt"],
        check=True,
    )

    entries = workspace_changes(str(workspace))["entries"]

    assert len(entries) == 1
    assert entries[0]["path"] == "new name.txt"
    assert entries[0]["renamed_from"] == "old name.txt"
    assert entries[0]["index"] == "R"


def test_pack_daemon_serves_workspace_changes(tmp_path: Path) -> None:
    from pack_daemon import PackDaemonAdapter
    from pack_daemon import make_dispatcher

    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    (workspace / "note.txt").write_text("hello\n", encoding="utf-8")
    tab = MagicMock()
    tab.workspace_path = str(workspace)
    dispatch = make_dispatcher(tab, PackDaemonAdapter(), False, MagicMock())

    result = dispatch("workspace_changes", {})

    assert result["is_git"] is True
    assert [entry["path"] for entry in result["entries"]] == ["note.txt"]

    tab.workspace_path = None
    assert dispatch("workspace_changes", {})["entries"] == []
