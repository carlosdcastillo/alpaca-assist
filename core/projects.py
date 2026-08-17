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

# Bounds for workspace_changes. A Pack workspace mid-task can hold a
# generated build tree or a huge vendored blob, and the whole payload
# crosses the SSH channel as one JSON-RPC response before the panel can
# show anything — so cap per file and in total, and say so in the result
# rather than silently returning a partial diff.
DIFF_FILE_LIMIT = 200
DIFF_FILE_BYTES = 200_000
DIFF_TOTAL_BYTES = 2_000_000


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


def _git(
    workspace: Path,
    *args: str,
    timeout: float = 10,
) -> subprocess.CompletedProcess[str]:
    """Run a read-only git command in `workspace`, never raising on failure."""
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        # A workspace can hold files git treats as text but that aren't
        # valid UTF-8; a diff panel is not worth an exception.
        errors="replace",
        timeout=timeout,
        check=False,
    )


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
        return _git(workspace, *args)

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


def _parse_porcelain(raw: str) -> list[dict[str, Any]]:
    """Parse `git status --porcelain -z` into one record per changed path.

    NUL-separated rather than line-separated because paths with spaces,
    quotes or newlines are ordinary in a workspace and the line format
    escapes them into something that has to be unescaped again. Rename
    and copy entries spend a second field on their origin path.
    """
    fields = raw.split("\0")
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        index_code, worktree_code, path = field[0], field[1], field[3:]
        renamed_from = None
        if "R" in (index_code, worktree_code) or "C" in (index_code, worktree_code):
            if index < len(fields):
                renamed_from = fields[index] or None
                index += 1
        entries.append(
            {
                "path": path,
                "index": index_code,
                "worktree": worktree_code,
                "untracked": index_code == "?",
                "renamed_from": renamed_from,
            },
        )
    return entries


def _entry_diff(
    workspace: Path,
    entry: dict[str, Any],
    has_head: bool,
) -> str:
    """Return the unified diff for one changed path, working tree vs HEAD."""
    if entry["untracked"]:
        # Untracked files have nothing to diff against inside the index,
        # so compare against /dev/null to get the same "new file" shape
        # the panel already knows how to render.
        result = _git(
            workspace,
            "diff",
            "--no-index",
            "--",
            os.devnull,
            entry["path"],
            timeout=30,
        )
    else:
        paths = [entry["path"]]
        if entry["renamed_from"]:
            paths.append(entry["renamed_from"])
        # HEAD covers staged and unstaged changes in one diff, which is
        # what "what does this workspace look like" means. A repository
        # with no commits yet has no HEAD to compare against.
        base = ["HEAD"] if has_head else ["--cached"]
        result = _git(workspace, "diff", *base, "--", *paths, timeout=30)
    return result.stdout


def workspace_changes(workspace_path: str) -> dict[str, Any]:
    """Return `git status` entries plus a per-file diff for a workspace.

    Shaped for display: one record per changed path carrying its own
    diff, so the panel can list files and show one file's changes
    without re-parsing a combined diff.
    """
    workspace = Path(workspace_path).expanduser()
    changes: dict[str, Any] = {
        "workspace_path": str(workspace),
        "exists": workspace.is_dir(),
        "is_git": False,
        "branch": None,
        "head": None,
        "entries": [],
        "omitted_files": 0,
        "truncated": False,
    }
    if not changes["exists"] or not (workspace / ".git").exists():
        return changes
    changes["is_git"] = True

    branch = _git(workspace, "branch", "--show-current")
    if branch.returncode == 0:
        changes["branch"] = branch.stdout.strip() or "detached"
    head = _git(workspace, "log", "-1", "--format=%h %s")
    has_head = head.returncode == 0
    if has_head:
        changes["head"] = head.stdout.strip()

    status = _git(workspace, "status", "--porcelain", "-z", timeout=30)
    if status.returncode != 0:
        return changes
    entries = _parse_porcelain(status.stdout)
    if len(entries) > DIFF_FILE_LIMIT:
        changes["omitted_files"] = len(entries) - DIFF_FILE_LIMIT
        entries = entries[:DIFF_FILE_LIMIT]

    budget = DIFF_TOTAL_BYTES
    for entry in entries:
        entry["truncated"] = False
        if budget <= 0:
            entry["diff"] = ""
            entry["truncated"] = True
            changes["truncated"] = True
            continue
        diff = _entry_diff(workspace, entry, has_head)
        limit = min(DIFF_FILE_BYTES, budget)
        if len(diff) > limit:
            diff = diff[:limit]
            entry["truncated"] = True
            changes["truncated"] = True
        entry["diff"] = diff
        budget -= len(diff)
    changes["entries"] = entries
    return changes


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
