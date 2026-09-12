"""Cross-platform command executor.

POSIX commands are passed directly to the requested executable. Windows
commands run through PowerShell so built-ins, pipelines, environment variables,
and the ``.cmd`` wrappers commonly installed by developer tools all work.
"""

import os
import platform
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
# Memory-safety backstop only (e.g. a runaway `find /`) — not a context-size
# control. The real context boundary is core.tool_output_gate, which sits
# downstream of this and needs the full output to gate correctly.
MAX_OUTPUT_SIZE = 5 * 1024 * 1024  # 5MB


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


class ShellExecutor:
    @staticmethod
    def _windows_powershell_command(command: str) -> list[str] | None:
        """Build a non-interactive PowerShell invocation for a Windows command."""
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            return None

        # PowerShell itself commonly exits 0 after a failed native command unless
        # the script explicitly forwards that command's status. Capture `$?`
        # immediately so the bookkeeping below cannot overwrite it.
        script = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            f"& {{ {command} }}; "
            "$alpacaSuccess = $?; $alpacaExitCode = $LASTEXITCODE; "
            "if ($alpacaSuccess) { exit 0 }; "
            "if ($null -ne $alpacaExitCode) { exit $alpacaExitCode }; exit 1"
        )
        return [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]

    def _parse_command(self, command: str) -> list[str]:
        return shlex.split(command)

    def _resolve_executable(self, cmd: str) -> str | None:
        return shutil.which(cmd)

    def run(
        self,
        command: str,
        working_directory: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> ExecutionResult:
        start_time = time.time()
        workspace = os.environ.get("ALPACA_WORKSPACE")
        cwd = working_directory or workspace or os.getcwd()
        if workspace and working_directory and not os.path.isabs(working_directory):
            cwd = os.path.join(workspace, working_directory)

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

        if not command.strip():
            return ExecutionResult(
                command=command,
                cwd=cwd,
                exit_code="ERROR",
                stdout="",
                stderr="",
                duration=time.time() - start_time,
                error_message="Empty command",
            )

        is_windows = platform.system() == "Windows"

        # Windows needs a real command language: many routine commands are
        # PowerShell cmdlets or cmd.exe built-ins rather than executables, and
        # direct argv execution cannot implement pipes, redirects, or chaining.
        if is_windows:
            args = self._windows_powershell_command(command)
            if args is None:
                return ExecutionResult(
                    command=command,
                    cwd=cwd,
                    exit_code="ERROR",
                    stdout="",
                    stderr="",
                    duration=time.time() - start_time,
                    error_message="PowerShell was not found in PATH",
                )
        else:
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

        # PowerShell was resolved while building the Windows invocation. On
        # POSIX, resolve the requested executable before launching it.
        if not is_windows:
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

        if is_windows:
            # Prevent console window popup and handle process groups
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW") | getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
            )
            startupinfo = getattr(subprocess, "STARTUPINFO")()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW")
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE")
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
