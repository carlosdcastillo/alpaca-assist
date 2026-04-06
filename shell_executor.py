"""
Cross-platform shell command executor with allowlist security.
"""
import json
import os
import platform
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Optional


DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
# Memory-safety backstop only (e.g. a runaway `find /`) — not a context-size
# control. The real context boundary is core.tool_output_gate, which sits
# downstream of this and needs the full output to gate correctly.
MAX_OUTPUT_SIZE = 5 * 1024 * 1024  # 5MB

DEFAULT_ALLOWLIST = {
    "python": ["python3", "py"],
    "pip": ["pip3"],
    "git": [],
    "node": ["nodejs"],
    "npm": [],
    "npx": [],
    "cargo": [],
    "rustc": [],
    "make": [],
    "cmake": [],
}


@dataclass
class ExecutionResult:
    command: str
    cwd: str
    exit_code: int | str
    stdout: str
    stderr: str
    duration: float
    error_message: str | None = None

    def format_output(self) -> str:
        lines = [
            "=" * 80,
            "COMMAND EXECUTION RESULT",
            "=" * 80,
            f"Command:           {self.command}",
            f"Working Directory: {self.cwd}",
            f"Exit Code:         {self.exit_code}",
            f"Duration:          {self.duration:.2f}s",
            "",
        ]

        if self.error_message:
            lines.extend(
                [
                    "=" * 35 + " ERROR " + "=" * 38,
                    self.error_message,
                    "",
                ],
            )
        else:
            lines.extend(
                [
                    "=" * 35 + " STDOUT " + "=" * 37,
                    self.stdout if self.stdout else "(empty)",
                    "",
                ],
            )
            if self.stderr:
                lines.extend(
                    [
                        "=" * 35 + " STDERR " + "=" * 37,
                        self.stderr,
                        "",
                    ],
                )

        lines.append("=" * 80)
        return "\n".join(lines)


class AllowlistManager:
    def __init__(self):
        self.allowlist = self._load_allowlist()

    def _get_config_path(self) -> Path:
        if platform.system() == "Windows":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
            return Path(base) / "mcp-shell" / "allowlist.json"
        else:
            return Path.home() / ".config" / "mcp-shell" / "allowlist.json"

    def _load_allowlist(self) -> dict[str, list[str]]:
        allowlist = dict(DEFAULT_ALLOWLIST)
        config_path = self._get_config_path()

        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    user_config = json.load(f)

                # Add additional commands
                for cmd, aliases in user_config.get("additional_commands", {}).items():
                    if isinstance(aliases, list):
                        allowlist[cmd] = aliases
                    elif isinstance(aliases, dict):
                        allowlist[cmd] = aliases.get("aliases", [])

                # Remove disabled commands
                for cmd in user_config.get("disabled_commands", []):
                    allowlist.pop(cmd, None)

            except (json.JSONDecodeError, OSError):
                pass  # Use defaults on error

        return allowlist

    def is_allowed(self, command_name: str) -> bool:
        cmd = command_name.lower()
        # Remove extension on Windows
        if platform.system() == "Windows":
            for ext in [".exe", ".bat", ".cmd", ".com"]:
                if cmd.endswith(ext):
                    cmd = cmd[: -len(ext)]
                    break

        # Check direct match
        if cmd in self.allowlist:
            return True

        # Check aliases
        for main_cmd, aliases in self.allowlist.items():
            if cmd in [a.lower() for a in aliases]:
                return True

        return False

    def get_allowed_commands(self) -> list[str]:
        return sorted(self.allowlist.keys())


class ShellExecutor:
    def __init__(self):
        self.allowlist_manager = AllowlistManager()

    def _parse_command(self, command: str) -> list[str]:
        if platform.system() == "Windows":
            return self._split_windows_command(command)
        else:
            return shlex.split(command)

    @staticmethod
    def _split_windows_command(command: str) -> list[str]:
        """Split a command string into argv-style tokens on Windows.

        Neither shlex mode is correct here on its own:
          - posix=False preserves backslashes everywhere (good for Windows
            paths) but doesn't strip quote characters and doesn't understand
            a backslash-escaped quote (`\\"`) inside a double-quoted span —
            it ends the span right there, mis-splitting anything like
            `python -c "...strftime(\\"%H:%M\\")..."`
          - posix=True handles escaped quotes correctly inside quotes, but
            also treats backslash as an escape character in *unquoted* text,
            mangling bare Windows paths (`C:\\Users\\x` -> `C:Usersx`).

        So: quoted spans follow POSIX rules (single-quoted = fully literal;
        double-quoted = backslash only escapes `"` or `\\`), while unquoted
        text is left untouched — backslashes there are Windows path
        separators, not shell escapes.
        """
        tokens: list[str] = []
        current: list[str] = []
        in_token = False
        i = 0
        n = len(command)
        while i < n:
            ch = command[i]
            if ch.isspace():
                if in_token:
                    tokens.append("".join(current))
                    current = []
                    in_token = False
                i += 1
                continue
            in_token = True
            if ch == "'":
                end = command.find("'", i + 1)
                if end == -1:
                    current.append(command[i + 1 :])
                    i = n
                else:
                    current.append(command[i + 1 : end])
                    i = end + 1
                continue
            if ch == '"':
                i += 1
                while i < n and command[i] != '"':
                    if (
                        command[i] == "\\"
                        and i + 1 < n
                        and command[i + 1]
                        in (
                            '"',
                            "\\",
                        )
                    ):
                        current.append(command[i + 1])
                        i += 2
                    else:
                        current.append(command[i])
                        i += 1
                i += 1  # skip closing quote (no-op if the span was unterminated)
                continue
            current.append(ch)
            i += 1
        if in_token:
            tokens.append("".join(current))
        return tokens

    def _extract_base_command(self, args: list[str]) -> str:
        if not args:
            return ""
        cmd = args[0]
        # Strip path
        cmd = os.path.basename(cmd)
        return cmd

    def _resolve_executable(self, cmd: str) -> str | None:
        return shutil.which(cmd)

    def run(
        self,
        command: str,
        working_directory: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> ExecutionResult:
        start_time = time.time()
        cwd = working_directory or os.getcwd()

        # Validate working directory
        if not os.path.isdir(cwd):
            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="ERROR",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message=f"Working directory does not exist: {cwd}",
            )

        # Parse command
        try:
            args = self._parse_command(command)
        except ValueError as e:
            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="ERROR",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message=f"Failed to parse command: {e}",
            )

        if not args:
            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="ERROR",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message="Empty command",
            )

        # Extract and validate base command
        base_cmd = self._extract_base_command(args)
        if not self.allowlist_manager.is_allowed(base_cmd):
            allowed = ", ".join(self.allowlist_manager.get_allowed_commands())
            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="BLOCKED",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message=f"Command '{base_cmd}' is not in the allowlist.\nAllowed commands: {allowed}",
            )

        # Resolve executable path
        executable = self._resolve_executable(args[0])
        if not executable:
            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="ERROR",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message=f"Command '{args[0]}' not found in PATH",
            )

        # Execute
        args[0] = executable
        timeout = min(timeout, MAX_TIMEOUT)

        # Windows-specific subprocess configuration
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdin": subprocess.DEVNULL,  # Prevent hanging on input
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout,
            "shell": False,
        }

        if platform.system() == "Windows":
            # Prevent console window popup and handle process groups
            kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = startupinfo

        try:
            result = subprocess.run(args, **kwargs)

            # Decode output with fallback for encoding issues
            try:
                stdout_str = result.stdout.decode("utf-8")
            except UnicodeDecodeError:
                stdout_str = result.stdout.decode("cp1252", errors="replace")

            try:
                stderr_str = result.stderr.decode("utf-8")
            except UnicodeDecodeError:
                stderr_str = result.stderr.decode("cp1252", errors="replace")

            stdout = stdout_str[:MAX_OUTPUT_SIZE]
            stderr = stderr_str[:MAX_OUTPUT_SIZE]

            if len(stdout_str) > MAX_OUTPUT_SIZE:
                stdout += "\n... (output truncated)"
            if len(stderr_str) > MAX_OUTPUT_SIZE:
                stderr += "\n... (output truncated)"

            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
                duration=time.time() - start_time,
            )

        except subprocess.TimeoutExpired as e:
            # Try to capture any partial output
            stdout_partial = ""
            stderr_partial = ""
            if e.stdout:
                try:
                    stdout_partial = e.stdout.decode("utf-8", errors="replace")[:1000]
                except Exception:
                    pass
            if e.stderr:
                try:
                    stderr_partial = e.stderr.decode("utf-8", errors="replace")[:1000]
                except Exception:
                    pass

            error_msg = f"Command timed out after {timeout} seconds"
            if stdout_partial:
                error_msg += f"\n\nPartial stdout:\n{stdout_partial}"
            if stderr_partial:
                error_msg += f"\n\nPartial stderr:\n{stderr_partial}"

            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="TIMEOUT",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message=error_msg,
            )
        except OSError as e:
            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="ERROR",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message=f"Failed to execute command: {e}",
            )
