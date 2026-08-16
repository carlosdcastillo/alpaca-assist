"""Tests for pack_bridge.py's daemon spawn argv — specifically that the

optional model argument reaches pack_daemon.py's --model flag only when
this invocation is the one that actually spawns a fresh daemon.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

pytest.importorskip("fcntl")

import pack_bridge


class TestLaunchOrAttachModelForwarding:
    def _run(self, tmp_path: Path, model: str | None) -> list[str]:
        """Drive _launch_or_attach with Popen mocked and _probe_connect

        faked to report "no existing daemon" then "spawn succeeded",
        so the function proceeds straight through the spawn branch
        without touching a real socket or process. Returns the argv
        Popen was called with.
        """
        fake_socket = MagicMock()
        with patch.object(pack_bridge, "subprocess") as mock_subprocess, patch.object(
            pack_bridge,
            "_probe_connect",
            side_effect=[None, fake_socket],
        ), patch.object(pack_bridge.Path, "home", return_value=tmp_path):
            mock_subprocess.Popen.return_value = MagicMock()
            result = pack_bridge._launch_or_attach("sess-1", model)

        assert result is fake_socket
        return cast(list[str], mock_subprocess.Popen.call_args[0][0])

    def test_model_appends_the_flag(self, tmp_path: Path) -> None:
        argv = self._run(tmp_path, "kimi-k3")

        assert argv[-2:] == ["--model", "kimi-k3"]

    def test_no_model_omits_the_flag(self, tmp_path: Path) -> None:
        argv = self._run(tmp_path, None)

        assert "--model" not in argv

    def test_argv_still_targets_pack_daemon_with_the_session_id(
        self,
        tmp_path: Path,
    ) -> None:
        argv = self._run(tmp_path, "kimi-k3")

        assert argv[1].endswith("pack_daemon.py")
        assert argv[2] == "sess-1"
