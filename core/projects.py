"""Project definitions and managed workspaces for remote Pack tabs."""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECTS_DIR = Path.home() / "packs"
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    repo_url: str
    branch: str | None
    workspace_base: str
    workspace_naming: str
    runbook: str
    spinup: str
    fingerprint: str

    def workspace_path(self, session_id: str) -> str:
        try:
            leaf = self.workspace_naming.format(
                project=self.name,
                session_id=session_id,
                tab_id=session_id,
            )
        except (KeyError, ValueError) as e:
            raise ValueError(
                f"Invalid workspace_naming for project {self.name!r}: {e}",
            ) from e
        if not leaf or Path(leaf).name != leaf:
            raise ValueError(
                f"Invalid workspace_naming result for project {self.name!r}",
            )
        return posixpath.join(self.workspace_base.rstrip("/"), leaf)

    def to_payload(self, session_id: str) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo_url": self.repo_url,
            "branch": self.branch,
            "workspace_path": self.workspace_path(session_id),
            "runbook": self.runbook,
            "spinup": self.spinup,
            "fingerprint": self.fingerprint,
        }

    def to_info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "repo_url": self.repo_url,
            "branch": self.branch,
            "workspace_base": self.workspace_base,
            "workspace_naming": self.workspace_naming,
            "has_runbook": bool(self.runbook),
            "has_spinup": bool(self.spinup),
        }


def list_projects(projects_dir: Path = PROJECTS_DIR) -> list[ProjectConfig]:
    """Load valid project directories, ignoring malformed entries."""
    if not projects_dir.is_dir():
        return []
    projects: list[ProjectConfig] = []
    for entry in sorted(projects_dir.iterdir(), key=lambda path: path.name.lower()):
        if not entry.is_dir() or not (entry / "project.toml").is_file():
            continue
        try:
            projects.append(load_project(entry.name, projects_dir=projects_dir))
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            continue
    return projects


def load_project(
    name: str,
    host: str | None = None,
    projects_dir: Path = PROJECTS_DIR,
) -> ProjectConfig:
    """Load one project and compose its runbook with an optional host overlay."""
    if not _PROJECT_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid project name: {name!r}")
    project_dir = projects_dir / name
    config_path = project_dir / "project.toml"
    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)
    values = raw.get("project", raw)
    if not isinstance(values, dict):
        raise ValueError(f"Invalid project.toml for {name!r}")

    repo_url = str(values.get("repo_url", "")).strip()
    if not repo_url:
        raise ValueError(f"Project {name!r} has no repo_url")
    branch_value = str(values.get("branch", "")).strip()
    workspace_base = str(values.get("workspace_base", "~/workspaces")).strip()
    workspace_naming = str(
        values.get("workspace_naming", "{project}-{session_id}"),
    ).strip()
    if not workspace_base or not workspace_naming:
        raise ValueError(f"Project {name!r} has an invalid workspace configuration")
    if "{session_id}" not in workspace_naming and "{tab_id}" not in workspace_naming:
        raise ValueError(
            f"Project {name!r} workspace_naming must contain "
            "{session_id} or {tab_id}",
        )

    runbook = _read_optional(project_dir / "RUNBOOK.md")
    if host:
        overlay = _host_overlay(project_dir, host)
        if overlay:
            runbook = (
                f"{runbook}\n\n<host_overlay host={host!r}>\n{overlay}\n</host_overlay>"
                if runbook
                else overlay
            )
    spinup = _read_optional(project_dir / "SPINUP.md")
    fingerprint_source = "\0".join(
        [repo_url, branch_value, workspace_base, workspace_naming, runbook, spinup],
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:16]
    return ProjectConfig(
        name=name,
        repo_url=repo_url,
        branch=branch_value or None,
        workspace_base=workspace_base,
        workspace_naming=workspace_naming,
        runbook=runbook,
        spinup=spinup,
        fingerprint=fingerprint,
    )


def prepare_workspace(payload: dict[str, Any]) -> str:
    """Idempotently clone a project's repository into its managed workspace."""
    workspace = Path(str(payload["workspace_path"])).expanduser()
    repo_url = str(payload["repo_url"])
    branch = payload.get("branch")
    if workspace.is_dir() and (workspace / ".git").exists():
        return str(workspace.resolve())
    if workspace.exists():
        raise RuntimeError(f"Workspace exists but is not a Git repository: {workspace}")

    workspace.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{workspace.name}-clone-", dir=workspace.parent),
    )
    shutil.rmtree(temp_dir)
    command = ["git", "clone"]
    if branch:
        command.extend(["--branch", str(branch)])
    command.extend(["--", repo_url, str(temp_dir)])
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git clone failed ({result.returncode}): {result.stdout.strip()}",
            )
        os.replace(temp_dir, workspace)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
    return str(workspace.resolve())


def probe_workspace(workspace_path: str) -> dict[str, Any]:
    """Return compact, honest Git state for a managed workspace."""
    workspace = Path(workspace_path).expanduser()
    status: dict[str, Any] = {
        "workspace_path": str(workspace),
        "exists": workspace.is_dir(),
        "is_git": False,
        "branch": None,
        "dirty": None,
        "unpushed": None,
    }
    if not status["exists"] or not (workspace / ".git").exists():
        return status
    status["is_git"] = True

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(workspace), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )

    branch = git("branch", "--show-current")
    if branch.returncode == 0:
        status["branch"] = branch.stdout.strip() or "detached"
    changes = git("status", "--porcelain")
    if changes.returncode == 0:
        status["dirty"] = len([line for line in changes.stdout.splitlines() if line])
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream.returncode == 0:
        ahead = git("rev-list", "--count", "@{upstream}..HEAD")
        if ahead.returncode == 0:
            status["unpushed"] = int(ahead.stdout.strip() or "0")
    return status


def _read_optional(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _host_overlay(project_dir: Path, host: str) -> str:
    candidates = [host]
    if "@" in host:
        candidates.append(host.rsplit("@", 1)[1])
    for candidate in candidates:
        if _PROJECT_NAME_RE.fullmatch(candidate):
            overlay = _read_optional(project_dir / "hosts" / f"{candidate}.md")
            if overlay:
                return overlay
    return ""
