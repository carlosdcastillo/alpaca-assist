"""
TRAMP (Transparent Remote Access, Multiple Protocol) filename handler.
Supports SSH TRAMP filenames like /ssh:user@host:/path/to/file
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import List
from typing import Optional
from typing import Tuple


@dataclass
class TrampPath:
    """Parsed TRAMP filename components."""

    method: str
    user: str
    host: str
    path: str
    original: str


class TrampHandler:
    """Handler for TRAMP filenames with SSH support."""

    # TRAMP filename pattern: /method:user@host:path
    TRAMP_PATTERN = re.compile(r"^/([^:]+):([^@]+)@([^:]+):(.*)$")

    def __init__(self):
        """Initialize TRAMP handler and check SSH availability."""
        self._ssh_command = self._detect_ssh_command()

    def _detect_ssh_command(self) -> list[str]:
        """Detect available SSH command on the system."""
        # Try standard OpenSSH first
        if shutil.which("ssh"):
            return ["ssh"]

        # Windows-specific fallbacks
        if sys.platform == "win32":
            # Try PuTTY's plink
            if shutil.which("plink"):
                return ["plink", "-batch"]

            # Check for Windows OpenSSH in system paths
            openssh_paths = [
                r"C:\Windows\System32\OpenSSH\ssh.exe",
                r"C:\Program Files\OpenSSH\ssh.exe",
            ]
            for path in openssh_paths:
                if os.path.exists(path):
                    return [path]

        # No SSH client found
        error_msg = "SSH client not found. "
        if sys.platform == "win32":
            error_msg += (
                "Please install OpenSSH or PuTTY.\n"
                "Windows 10+: Enable OpenSSH client in Optional Features\n"
                "Or download PuTTY from: https://www.putty.org/"
            )
        else:
            error_msg += "Please install openssh-client package."

        raise RuntimeError(error_msg)

    def is_tramp_path(self, filepath: str) -> bool:
        """Check if filepath is a TRAMP filename."""
        return bool(self.TRAMP_PATTERN.match(filepath))

    def parse_tramp_path(self, filepath: str) -> TrampPath:
        """Parse TRAMP filename into components."""
        match = self.TRAMP_PATTERN.match(filepath)
        if not match:
            raise ValueError(f"Invalid TRAMP filename: {filepath}")

        method, user, host, path = match.groups()

        # Validate supported methods
        if method.lower() not in ["ssh", "scp"]:
            raise ValueError(f"Unsupported TRAMP method: {method}")

        return TrampPath(
            method=method.lower(),
            user=user,
            host=host,
            path=path,
            original=filepath,
        )

    def _run_ssh_command(
        self,
        tramp_path: TrampPath,
        remote_command: str,
        input_data: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Run SSH command on remote host."""
        # Build SSH connection string
        ssh_target = f"{tramp_path.user}@{tramp_path.host}"

        # Build full command
        cmd = self._ssh_command + [ssh_target, remote_command]

        # Run command
        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
            )
            return result
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"SSH command timed out: {' '.join(cmd)}")
        except FileNotFoundError:
            raise RuntimeError("SSH client not found or not executable")

    def exists(self, filepath: str) -> bool:
        """Check if file or directory exists."""
        if not self.is_tramp_path(filepath):
            return os.path.exists(filepath)

        tramp_path = self.parse_tramp_path(filepath)

        # Use 'test -e' to check existence
        result = self._run_ssh_command(tramp_path, f"test -e '{tramp_path.path}'")
        return result.returncode == 0

    def is_file(self, filepath: str) -> bool:
        """Check if path is a file."""
        if not self.is_tramp_path(filepath):
            return os.path.isfile(filepath)

        tramp_path = self.parse_tramp_path(filepath)

        # Use 'test -f' to check if it's a file
        result = self._run_ssh_command(tramp_path, f"test -f '{tramp_path.path}'")
        return result.returncode == 0

    def is_dir(self, filepath: str) -> bool:
        """Check if path is a directory."""
        if not self.is_tramp_path(filepath):
            return os.path.isdir(filepath)

        tramp_path = self.parse_tramp_path(filepath)

        # Use 'test -d' to check if it's a directory
        result = self._run_ssh_command(tramp_path, f"test -d '{tramp_path.path}'")
        return result.returncode == 0

    def get_size(self, filepath: str) -> int:
        """Get file size in bytes."""
        if not self.is_tramp_path(filepath):
            return os.path.getsize(filepath)

        tramp_path = self.parse_tramp_path(filepath)

        # Use 'stat' to get file size
        result = self._run_ssh_command(
            tramp_path,
            f"stat -c %s '{tramp_path.path}' 2>/dev/null || stat -f %z '{tramp_path.path}'",
        )

        if result.returncode != 0:
            raise FileNotFoundError(
                f"Cannot get size of '{filepath}': {result.stderr.strip()}",
            )

        try:
            return int(result.stdout.strip())
        except ValueError:
            raise RuntimeError(f"Invalid size output: {result.stdout.strip()}")

    def read_file(self, filepath: str) -> str:
        """Read file contents."""
        if not self.is_tramp_path(filepath):
            with open(filepath, encoding="utf-8") as f:
                return f.read()

        tramp_path = self.parse_tramp_path(filepath)

        # Use 'cat' to read file
        result = self._run_ssh_command(tramp_path, f"cat '{tramp_path.path}'")

        if result.returncode != 0:
            if "No such file" in result.stderr or "not found" in result.stderr:
                raise FileNotFoundError(f"File not found: {filepath}")
            elif "Permission denied" in result.stderr:
                raise PermissionError(f"Permission denied: {filepath}")
            else:
                raise RuntimeError(
                    f"Error reading file '{filepath}': {result.stderr.strip()}",
                )

        return result.stdout

    def write_file(self, filepath: str, content: str) -> None:
        """Write content to file."""
        if not self.is_tramp_path(filepath):
            # Create directory if needed
            dir_path = os.path.dirname(os.path.abspath(filepath))
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return

        tramp_path = self.parse_tramp_path(filepath)

        # Create remote directory if needed
        remote_dir = os.path.dirname(tramp_path.path)
        if remote_dir and remote_dir != "/":
            mkdir_result = self._run_ssh_command(tramp_path, f"mkdir -p '{remote_dir}'")
            if mkdir_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to create remote directory '{remote_dir}': {mkdir_result.stderr.strip()}",
                )

        # Write file using 'cat >' with stdin
        result = self._run_ssh_command(
            tramp_path,
            f"cat > '{tramp_path.path}'",
            input_data=content,
        )

        if result.returncode != 0:
            if "Permission denied" in result.stderr:
                raise PermissionError(f"Permission denied: {filepath}")
            else:
                raise RuntimeError(
                    f"Error writing file '{filepath}': {result.stderr.strip()}",
                )

    def list_dir(self, filepath: str) -> list[tuple[str, bool, int]]:
        """List directory contents. Returns list of (name, is_dir, size) tuples."""
        if not self.is_tramp_path(filepath):
            items = []
            for item in os.listdir(filepath):
                item_path = os.path.join(filepath, item)
                is_dir = os.path.isdir(item_path)
                size = 0 if is_dir else os.path.getsize(item_path)
                items.append((item, is_dir, size))
            return items

        tramp_path = self.parse_tramp_path(filepath)

        # Use 'ls -la' to get detailed directory listing
        result = self._run_ssh_command(tramp_path, f"ls -la '{tramp_path.path}'")

        if result.returncode != 0:
            if "No such file" in result.stderr or "not found" in result.stderr:
                raise FileNotFoundError(f"Directory not found: {filepath}")
            elif "Permission denied" in result.stderr:
                raise PermissionError(f"Permission denied: {filepath}")
            else:
                raise RuntimeError(
                    f"Error listing directory '{filepath}': {result.stderr.strip()}",
                )

        items = []
        lines = result.stdout.strip().split("\n")

        for line in lines[1:]:  # Skip first line (total)
            if not line.strip():
                continue

            # Parse ls -la output
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue

            permissions = parts[0]
            size_str = parts[4]
            name = parts[8]

            # Skip . and .. entries
            if name in [".", ".."]:
                continue

            is_dir = permissions.startswith("d")
            try:
                size = int(size_str) if not is_dir else 0
            except ValueError:
                size = 0

            items.append((name, is_dir, size))

        return items


# Global instance
_tramp_handler = None


def get_tramp_handler() -> TrampHandler:
    """Get global TRAMP handler instance."""
    global _tramp_handler
    if _tramp_handler is None:
        _tramp_handler = TrampHandler()
    return _tramp_handler
