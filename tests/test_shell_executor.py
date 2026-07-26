"""
Comprehensive tests for shell_executor.py module.

This module tests shell command execution, allowlist management, and security features.
"""
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from shell_executor import AllowlistManager
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


class TestAllowlistManagerInitialization:
    """Tests for AllowlistManager initialization."""

    def test_init_loads_allowlist(self, temp_dir: Path) -> None:
        """Test that initialization loads the allowlist."""
        config_path = temp_dir / "allowlist.json"
        # Config uses additional_commands format
        config_path.write_text(json.dumps({"additional_commands": {"mycommand": []}}))

        with patch.object(
            AllowlistManager,
            "_get_config_path",
            return_value=config_path,
        ):
            manager = AllowlistManager()
            # Should have default commands plus the extra one
            assert isinstance(manager.allowlist, dict)
            assert "mycommand" in manager.allowlist

    def test_init_creates_default_allowlist(self, temp_dir: Path) -> None:
        """Test that initialization creates default allowlist if none exists."""
        config_path = temp_dir / "nonexistent_allowlist.json"

        with patch.object(
            AllowlistManager,
            "_get_config_path",
            return_value=config_path,
        ):
            manager = AllowlistManager()
            # Should have default commands
            assert isinstance(manager.allowlist, dict)
            assert len(manager.allowlist) > 0

    def test_init_handles_corrupted_config(self, temp_dir: Path) -> None:
        """Test handling of corrupted config file."""
        config_path = temp_dir / "corrupted.json"
        config_path.write_text("not valid json")

        with patch.object(
            AllowlistManager,
            "_get_config_path",
            return_value=config_path,
        ):
            # Should not raise, should use defaults
            manager = AllowlistManager()
            assert isinstance(manager.allowlist, dict)
            assert len(manager.allowlist) > 0


class TestAllowlistManagerIsAllowed:
    """Tests for command allowlist checking."""

    def test_is_allowed_true(self, allowlist_manager: AllowlistManager) -> None:
        """Test that allowed commands return True."""
        # Set allowlist directly using the real attribute name
        allowlist_manager.allowlist = {"python": [], "git": []}

        assert allowlist_manager.is_allowed("python") is True
        assert allowlist_manager.is_allowed("git") is True

    def test_is_allowed_false(self, allowlist_manager: AllowlistManager) -> None:
        """Test that disallowed commands return False."""
        allowlist_manager.allowlist = {"python": []}

        assert allowlist_manager.is_allowed("rm") is False
        assert allowlist_manager.is_allowed("sudo") is False

    def test_is_allowed_empty_allowlist(
        self,
        allowlist_manager: AllowlistManager,
    ) -> None:
        """Test behavior with empty allowlist."""
        allowlist_manager.allowlist = {}

        assert allowlist_manager.is_allowed("python") is False

    def test_is_allowed_case_insensitive(
        self,
        allowlist_manager: AllowlistManager,
    ) -> None:
        """Test that allowlist checking lowercases the input."""
        # is_allowed() lowercases the command before lookup, so "Python" matches "python"
        allowlist_manager.allowlist = {"python": []}

        assert allowlist_manager.is_allowed("python") is True
        assert allowlist_manager.is_allowed("Python") is True

    def test_is_allowed_via_alias(self, allowlist_manager: AllowlistManager) -> None:
        """Test that commands can be allowed via aliases."""
        allowlist_manager.allowlist = {"python": ["python3", "py"]}

        assert allowlist_manager.is_allowed("python3") is True
        assert allowlist_manager.is_allowed("py") is True


class TestAllowlistManagerGetAllowedCommands:
    """Tests for getting allowed commands."""

    def test_get_allowed_commands(self, allowlist_manager: AllowlistManager) -> None:
        """Test getting list of allowed commands."""
        allowlist_manager.allowlist = {"python": [], "git": [], "ls": []}

        commands = allowlist_manager.get_allowed_commands()

        assert isinstance(commands, list)
        assert len(commands) == 3
        assert "python" in commands
        assert "git" in commands
        assert "ls" in commands

    def test_get_allowed_commands_empty(
        self,
        allowlist_manager: AllowlistManager,
    ) -> None:
        """Test getting allowed commands when empty."""
        allowlist_manager.allowlist = {}

        commands = allowlist_manager.get_allowed_commands()

        assert isinstance(commands, list)
        assert len(commands) == 0

    def test_get_allowed_commands_sorted(
        self,
        allowlist_manager: AllowlistManager,
    ) -> None:
        """Test that allowed commands are returned sorted."""
        allowlist_manager.allowlist = {"npm": [], "git": [], "python": []}

        commands = allowlist_manager.get_allowed_commands()

        assert commands == sorted(commands)


class TestShellExecutorInitialization:
    """Tests for ShellExecutor initialization."""

    def test_init_creates_allowlist_manager(self) -> None:
        """Test that initialization creates AllowlistManager."""
        executor = ShellExecutor()
        assert hasattr(executor, "allowlist_manager")
        assert isinstance(executor.allowlist_manager, AllowlistManager)


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


class TestShellExecutorExtractBaseCommand:
    """Tests for base command extraction."""

    def test_extract_base_command_simple(self, shell_executor: ShellExecutor) -> None:
        """Test extracting base command from simple args."""
        result = shell_executor._extract_base_command(["python", "script.py"])

        assert result == "python"

    def test_extract_base_command_with_path(
        self,
        shell_executor: ShellExecutor,
    ) -> None:
        """Test extracting base command from path."""
        result = shell_executor._extract_base_command(["/usr/bin/python", "script.py"])

        assert result == "python"

    def test_extract_base_command_empty(self, shell_executor: ShellExecutor) -> None:
        """Test extracting base command from empty args."""
        result = shell_executor._extract_base_command([])

        assert result == ""


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
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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

    def test_run_disallowed_command(self, shell_executor: ShellExecutor) -> None:
        """Test running a disallowed command."""
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=False,
        ):
            result = shell_executor.run("rm -rf /")

            assert isinstance(result, ExecutionResult)
            assert result.exit_code == "BLOCKED"
            assert result.error_message is not None
            assert "not in the allowlist" in result.error_message.lower()

    def test_run_command_not_found(self, shell_executor: ShellExecutor) -> None:
        """Test running a command that doesn't exist."""
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)

                result = shell_executor.run("python --version", timeout=1)

                assert isinstance(result, ExecutionResult)
                assert result.exit_code == "TIMEOUT"
                assert result.error_message is not None
                assert "timed out" in result.error_message.lower()

    def test_run_os_error(self, shell_executor: ShellExecutor) -> None:
        """Test handling of OS errors."""
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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
        assert result.exit_code in ("ERROR", "BLOCKED")

    def test_run_whitespace_command(self, shell_executor: ShellExecutor) -> None:
        """Test running whitespace-only command."""
        result = shell_executor.run("   ")

        assert isinstance(result, ExecutionResult)
        assert result.exit_code in ("ERROR", "BLOCKED")

    def test_run_complex_command(self, shell_executor: ShellExecutor) -> None:
        """Test running complex command with pipes and redirects."""
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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
        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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

        with patch.object(
            shell_executor.allowlist_manager,
            "is_allowed",
            return_value=True,
        ):
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
        """Test running real python --version command (if python is in allowlist)."""
        if not shell_executor.allowlist_manager.is_allowed("python"):
            pytest.skip("python command not in allowlist")

        result = shell_executor.run("python --version")

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0

    def test_allowlist_persistence(self, temp_dir: Path) -> None:
        """Test that allowlist config is read on initialization."""
        config_path = temp_dir / "allowlist.json"
        # Write a config that adds "mygit" as an additional command
        config_path.write_text(
            json.dumps({"additional_commands": {"mygit": []}}),
        )

        with patch.object(
            AllowlistManager,
            "_get_config_path",
            return_value=config_path,
        ):
            manager1 = AllowlistManager()
            assert manager1.is_allowed("mygit") is True

            # Create new manager reading same config, should see same commands
            manager2 = AllowlistManager()
            assert manager2.is_allowed("mygit") is True


class TestSecurityFeatures:
    """Tests for security features."""

    def test_dangerous_commands_blocked(self, shell_executor: ShellExecutor) -> None:
        """Test that dangerous commands are blocked by default."""
        dangerous_commands = [
            "rm -rf /",
            "sudo command",
            "chmod 777 /",
        ]

        for cmd in dangerous_commands:
            result = shell_executor.run(cmd)
            # Should be blocked by allowlist (exit_code == "BLOCKED") or have a
            # non-zero numeric exit code
            assert result.exit_code != 0

    def test_command_injection_attempts(self, shell_executor: ShellExecutor) -> None:
        """Test handling of command injection attempts."""
        injection_attempts = [
            "echo hello; rm -rf /",
            "echo hello && rm -rf /",
            "echo hello || rm -rf /",
            "echo `rm -rf /`",
            "echo $(rm -rf /)",
        ]

        for cmd in injection_attempts:
            # Should handle gracefully without executing dangerous parts
            result = shell_executor.run(cmd)
            assert isinstance(result, ExecutionResult)

    def test_path_traversal_blocked(self, shell_executor: ShellExecutor) -> None:
        """Test that path traversal attempts are handled."""
        result = shell_executor.run("cat ../../../etc/passwd")

        # "cat" is not in the allowlist, so it should be blocked
        assert result.exit_code != 0
