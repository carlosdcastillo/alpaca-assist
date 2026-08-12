"""
Comprehensive tests for shell_executor.py module.

This module tests shell command execution and parsing. There is no
command allowlist — the actual injection boundary is shell=False
(subprocess receives argv directly, so ;/&&/backticks/etc. in a command
string are literal arguments, never shell operators). See
test_command_injection_attempts for coverage of that boundary.
"""
import subprocess
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from shell_executor import ExecutionResult
from shell_executor import ShellExecutor


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_execution_result_creation(self) -> None:
        """Test creating ExecutionResult instance."""
        result = ExecutionResult(
            stdout="output",
            stderr="error",
            exit_code=0,
            command="test command",
            cwd="/tmp",
            duration=0.1,
        )

        assert result.stdout == "output"
        assert result.stderr == "error"
        assert result.exit_code == 0
        assert result.command == "test command"

    def test_execution_result_format_output_success(self) -> None:
        """Test formatting successful execution output."""
        result = ExecutionResult(
            stdout="Hello World",
            stderr="",
            exit_code=0,
            command="echo 'Hello World'",
            cwd="/tmp",
            duration=0.1,
        )

        formatted = result.format_output()

        assert "Hello World" in formatted
        assert "Command" in formatted
        assert "echo" in formatted

    def test_execution_result_format_output_error(self) -> None:
        """Test formatting error execution output."""
        result = ExecutionResult(
            stdout="",
            stderr="Error occurred",
            exit_code=1,
            command="bad_command",
            cwd="/tmp",
            duration=0.1,
        )

        formatted = result.format_output()

        assert "Error occurred" in formatted
        assert "Exit Code:" in formatted
        assert "1" in formatted

    def test_execution_result_format_output_empty(self) -> None:
        """Test formatting empty output."""
        result = ExecutionResult(
            stdout="",
            stderr="",
            exit_code=0,
            command="true",
            cwd="/tmp",
            duration=0.1,
        )

        formatted = result.format_output()

        assert "true" in formatted
        assert "Exit Code:" in formatted
        assert "0" in formatted


class TestShellExecutorParseCommand:
    """Tests for command parsing."""

    def test_parse_command_simple(self, shell_executor: ShellExecutor) -> None:
        """Test parsing simple command."""
        result = shell_executor._parse_command("python script.py")

        assert "python" in result
        assert "script.py" in result

    def test_parse_command_with_args(self, shell_executor: ShellExecutor) -> None:
        """Test parsing command with arguments."""
        result = shell_executor._parse_command("python script.py --flag value")

        assert "python" in result
        assert "script.py" in result
        assert "--flag" in result
        assert "value" in result

    def test_parse_command_with_quotes(self, shell_executor: ShellExecutor) -> None:
        """Test parsing command with quoted arguments."""
        result = shell_executor._parse_command('python script.py "arg with spaces"')

        assert "python" in result
        assert "script.py" in result
        # The quoted argument should be preserved as one element, with the
        # surrounding quote characters stripped (not passed through to argv).
        assert "arg with spaces" in result

    def test_parse_command_strips_double_quotes_from_code_arg(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """`-c "code"` must hand python bare code, not a quoted string literal."""
        result = shell_executor._parse_command('python -c "print(1)"')

        assert result == ["python", "-c", "print(1)"]

    def test_parse_command_strips_single_quotes_from_code_arg(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """Single-quoted args should also have their quotes stripped."""
        result = shell_executor._parse_command("python -c 'print(1)'")

        assert result == ["python", "-c", "print(1)"]

    def test_parse_command_preserves_windows_path_backslashes(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """Backslashes in quoted Windows paths must not be eaten as escapes."""
        result = shell_executor._parse_command(
            'git log "C:\\Users\\Carlos\\file.txt"',
        )

        assert "C:\\Users\\Carlos\\file.txt" in result

    def test_parse_command_handles_escaped_quote_inside_double_quotes(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """A backslash-escaped quote inside a double-quoted span must not end

        the span early — it should become a literal `"` in the token instead
        of splitting the rest of the argument into separate tokens.
        """
        result = shell_executor._parse_command(
            'python -c "print(\\"hello\\")"',
        )

        assert result == ["python", "-c", 'print("hello")']

    def test_parse_command_realistic_nested_quote_script(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """Regression test for a real failing command: a `-c` script with a

        single-quoted raw path and an f-string whose .strftime() format uses
        shell-escaped double quotes.
        """
        cmd = (
            "python -c \"import datetime; root = r'C:\\Users\\Carlos'; "
            'print(f\'{datetime.datetime.now().strftime(\\"%Y-%m-%d\\")}\')"'
        )

        result = shell_executor._parse_command(cmd)

        assert result[0] == "python"
        assert result[1] == "-c"
        code = result[2]
        assert "r'C:\\Users\\Carlos'" in code
        assert '.strftime("%Y-%m-%d")' in code
        compile(code, "<test>", "exec")  # must be syntactically valid Python

    def test_parse_command_single_quotes_are_fully_literal(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """Single-quoted spans get no escape processing at all, per POSIX rules."""
        result = shell_executor._parse_command("python -c 'a\\\"b'")

        assert result == ["python", "-c", 'a\\"b']

    def test_parse_command_unquoted_backslash_path_untouched(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """A bare, unquoted Windows path (no spaces) needs no quoting at all.

        This exercises Windows-only parsing behavior, so `platform.system`
        is patched — otherwise on non-Windows hosts `_parse_command` falls
        through to POSIX `shlex.split`, which (unlike `_split_windows_command`)
        treats backslash as an escape character even outside quotes and
        strips it from the path.
        """
        with patch("shell_executor.platform.system", return_value="Windows"):
            result = shell_executor._parse_command("dir C:\\Users\\Carlos")

        assert "C:\\Users\\Carlos" in result

    def test_parse_command_empty(self, shell_executor: ShellExecutor) -> None:
        """Test parsing empty command."""
        result = shell_executor._parse_command("")

        assert result == [] or result == [""]


class TestShellExecutorResolveExecutable:
    """Tests for executable resolution."""

    def test_resolve_executable_found(self, shell_executor: ShellExecutor) -> None:
        """Test resolving an executable that exists."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/python"
            result = shell_executor._resolve_executable("python")

            assert result == "/usr/bin/python"

    def test_resolve_executable_not_found(self, shell_executor: ShellExecutor) -> None:
        """Test resolving an executable that doesn't exist."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            result = shell_executor._resolve_executable("nonexistent_command")

            assert result is None


class TestShellExecutorRun:
    """Tests for command execution."""

    def test_run_successful_command(self, shell_executor: ShellExecutor) -> None:
        """Test running a successful command."""
        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = b"output"
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = shell_executor.run("python --version")

            assert isinstance(result, ExecutionResult)
            assert result.exit_code == 0
            assert "output" in result.stdout

    def test_run_passes_unquoted_code_arg_to_subprocess(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """Regression test: `python -c "code"` must reach subprocess.run

        without the surrounding quote characters, otherwise python receives
        a quoted string-literal statement instead of executable code and
        silently produces no output.
        """
        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = b"1\n"
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            shell_executor.run('python -c "print(1)"')

            called_args = mock_run.call_args[0][0]
            assert "print(1)" in called_args
            assert '"print(1)"' not in called_args

    def test_run_command_not_found(self, shell_executor: ShellExecutor) -> None:
        """Test running a command that doesn't exist."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None

            result = shell_executor.run("nonexistent_command")

            assert isinstance(result, ExecutionResult)
            assert result.exit_code == "ERROR"
            assert result.error_message is not None
            assert "not found" in result.error_message.lower()

    def test_run_with_working_directory(
        self,
        shell_executor: ShellExecutor,
        temp_dir: Path,
    ) -> None:
        """Test running command with working directory."""
        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = b""
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = shell_executor.run(
                "python --version",
                working_directory=str(temp_dir),
            )

            assert isinstance(result, ExecutionResult)
            # Verify cwd was passed to subprocess
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("cwd") == str(temp_dir)

    def test_run_with_timeout(self, shell_executor: ShellExecutor) -> None:
        """Test running command with timeout."""
        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = b""
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = shell_executor.run("python --version", timeout=5)

            # Verify timeout was passed to subprocess
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("timeout") == 5

    def test_run_timeout_expired(self, shell_executor: ShellExecutor) -> None:
        """Test handling of timeout expiration."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)

            result = shell_executor.run("python --version", timeout=1)

            assert isinstance(result, ExecutionResult)
            assert result.exit_code == "TIMEOUT"
            assert result.error_message is not None
            assert "timed out" in result.error_message.lower()

    def test_run_os_error(self, shell_executor: ShellExecutor) -> None:
        """Test handling of OS errors."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/bin/cmd"
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = OSError("Permission denied")

                result = shell_executor.run("cmd")

                assert isinstance(result, ExecutionResult)
                assert result.exit_code == "ERROR"
                assert result.error_message is not None
                assert (
                    "error" in result.error_message.lower()
                    or "permission" in result.error_message.lower()
                )


class TestShellExecutorEdgeCases:
    """Tests for edge cases and error handling."""

    def test_run_empty_command(self, shell_executor: ShellExecutor) -> None:
        """Test running empty command."""
        result = shell_executor.run("")

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == "ERROR"

    def test_run_whitespace_command(self, shell_executor: ShellExecutor) -> None:
        """Test running whitespace-only command."""
        result = shell_executor.run("   ")

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == "ERROR"

    def test_run_complex_command(self, shell_executor: ShellExecutor) -> None:
        """Test running complex command with pipes and redirects."""
        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = b"output"
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = shell_executor.run("python script.py arg1")

            assert isinstance(result, ExecutionResult)

    def test_run_unicode_output(self, shell_executor: ShellExecutor) -> None:
        """Test handling of unicode output."""
        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = "Unicode: ñ 中文".encode()
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = shell_executor.run("python --version")

            assert "ñ" in result.stdout
            assert "中文" in result.stdout

    def test_run_very_long_output(self, shell_executor: ShellExecutor) -> None:
        """Test handling of very long output (truncated at MAX_OUTPUT_SIZE)."""
        long_output = b"x" * (6 * 1024 * 1024)  # Exceeds 5MB MAX_OUTPUT_SIZE

        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = long_output
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = shell_executor.run("python --version")

            # Output should be truncated with a notice appended
            assert "truncated" in result.stdout


class TestShellExecutorIntegration:
    """Integration tests for ShellExecutor."""

    @pytest.mark.slow
    def test_run_real_python_version(self, shell_executor: ShellExecutor) -> None:
        """Test running a real, unmocked python --version command."""
        result = shell_executor.run("python --version")

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0


class TestSecurityFeatures:
    """Tests for the injection boundary that actually exists: shell=False.

    There is deliberately no command allowlist — see the module docstring
    in shell_executor.py. The real protection is that subprocess.run is
    always called with shell=False, so argv is passed directly to the
    process and shell metacharacters (;/&&/backticks/$()) are never
    interpreted — they're just literal argument text to whatever command
    args[0] resolves to.
    """

    def test_command_injection_attempts_are_never_shell_interpreted(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """Shell metacharacters in a command string must reach the

        underlying process as literal argv text, never as shell operators
        — verified by asserting subprocess.run is always called with
        shell=False, regardless of what the command string contains.
        """
        injection_attempts = [
            "echo hello; rm -rf /",
            "echo hello && rm -rf /",
            "echo hello || rm -rf /",
            "echo `rm -rf /`",
            "echo $(rm -rf /)",
        ]

        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.stdout = b""
            mock_result.stderr = b""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            for cmd in injection_attempts:
                result = shell_executor.run(cmd)

                assert isinstance(result, ExecutionResult)
                assert mock_run.call_args[1]["shell"] is False
