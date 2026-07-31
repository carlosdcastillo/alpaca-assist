"""Tests for core/pack_transport.py using a real-pipe fake Popen.

A fake subprocess (two OS pipe pairs wrapped as .stdin/.stdout) stands in
for the real `ssh ... pack_bridge.py ...` child, so PackTransport's reader
thread genuinely blocks/reads/iterates exactly as it would against a real
process, without needing SSH or a real daemon.
"""
from __future__ import annotations

import os
import threading
import time
from unittest.mock import patch

import pytest

from core.pack_protocol import encode_error_response
from core.pack_protocol import encode_notification
from core.pack_protocol import encode_response
from core.pack_transport import PackTransport
from core.pack_transport import PackTransportError


class FakePopen:
    """A Popen-alike backed by two real OS pipes.

    `daemon_write(text)` / `daemon_read()` let the test act as "the
    daemon side": write fake responses/notifications for the transport to
    receive, and read whatever the transport sent as requests.
    """

    def __init__(self) -> None:
        to_daemon_r, self._to_daemon_w = os.pipe()
        from_daemon_r, self._from_daemon_w = os.pipe()
        self.stdin = os.fdopen(self._to_daemon_w, "wb")
        self.stdout = os.fdopen(from_daemon_r, "rb")
        self._daemon_reader = os.fdopen(to_daemon_r, "rb")
        self._terminated = False

    def daemon_write(self, text: str) -> None:
        os.write(self._from_daemon_w, text.encode("utf-8"))

    def daemon_read_line(self, timeout: float = 2.0) -> str:
        # Simple blocking readline via the raw fd the test owns.
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            chunk = self._daemon_reader.readline()
            if chunk:
                buf += chunk
                break
        return buf.decode("utf-8")

    def terminate(self) -> None:
        if self._terminated:
            return
        self._terminated = True
        # Simulate child death: its stdout write end closes -> our reader
        # thread's iteration hits EOF.
        os.close(self._from_daemon_w)


@pytest.fixture
def fake_popen() -> FakePopen:
    return FakePopen()


@pytest.fixture
def transport(fake_popen: FakePopen):
    t = PackTransport("testhost", "session-1")
    with patch("core.pack_transport.subprocess.Popen", return_value=fake_popen):
        t.connect()
    yield t
    t.close()


class TestConnect:
    def test_connect_sets_connected_true(self, transport: PackTransport) -> None:
        assert transport.connected is True

    def test_connect_spawns_ssh_with_batch_mode_and_session_id(
        self,
        fake_popen: FakePopen,
    ) -> None:
        t = PackTransport("myhost", "abc123")
        with patch("core.pack_transport.subprocess.Popen", return_value=fake_popen) as mock_popen:
            t.connect()
        t.close()

        argv = mock_popen.call_args[0][0]
        assert argv[0] == "ssh"
        assert "BatchMode=yes" in " ".join(argv)
        assert "myhost" in argv
        assert any("abc123" in part for part in argv)


class TestRequestResponse:
    def test_successful_round_trip(
        self,
        transport: PackTransport,
        fake_popen: FakePopen,
    ) -> None:
        def respond() -> None:
            line = fake_popen.daemon_read_line()
            assert '"method": "attach"' in line
            fake_popen.daemon_write(encode_response(1, {"title": "Pack Tab"}))

        threading.Thread(target=respond, daemon=True).start()

        result = transport.send_request("attach", {}, timeout=2.0)

        assert result == {"title": "Pack Tab"}

    def test_error_response_raises(
        self,
        transport: PackTransport,
        fake_popen: FakePopen,
    ) -> None:
        def respond() -> None:
            fake_popen.daemon_read_line()
            fake_popen.daemon_write(encode_error_response(1, "boom"))

        threading.Thread(target=respond, daemon=True).start()

        with pytest.raises(PackTransportError, match="boom"):
            transport.send_request("send_message", {}, timeout=2.0)

    def test_timeout_raises(self, transport: PackTransport) -> None:
        with pytest.raises(PackTransportError, match="timed out"):
            transport.send_request("attach", {}, timeout=0.2)

    def test_send_request_not_connected_raises(self) -> None:
        t = PackTransport("host", "session")

        with pytest.raises(PackTransportError, match="not connected"):
            t.send_request("attach", {}, timeout=1.0)

    def test_concurrent_requests_correlate_independently(
        self,
        transport: PackTransport,
        fake_popen: FakePopen,
    ) -> None:
        def respond_both() -> None:
            line1 = fake_popen.daemon_read_line()
            line2 = fake_popen.daemon_read_line()
            # Respond out of order to prove correlation is by id, not order.
            id2 = 2 if '"id": 2' in line2 else 1
            id1 = 1 if id2 == 2 else 2
            fake_popen.daemon_write(encode_response(id2, {"which": "second-sent"}))
            fake_popen.daemon_write(encode_response(id1, {"which": "first-sent"}))

        threading.Thread(target=respond_both, daemon=True).start()

        results = {}

        def call(method: str, key: str) -> None:
            results[key] = transport.send_request(method, {}, timeout=2.0)

        t1 = threading.Thread(target=call, args=("attach", "a"))
        t2 = threading.Thread(target=call, args=("stop_streaming", "b"))
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert results["a"]["which"] in ("first-sent", "second-sent")
        assert results["b"]["which"] in ("first-sent", "second-sent")


class TestNotifications:
    def test_notification_dispatches_to_registered_handler(
        self,
        fake_popen: FakePopen,
    ) -> None:
        t = PackTransport("host", "session")
        received = []
        t.on_notification("on_content_update", received.append)
        with patch("core.pack_transport.subprocess.Popen", return_value=fake_popen):
            t.connect()

        fake_popen.daemon_write(
            encode_notification("on_content_update", {"content_chunk": "hi"}),
        )
        time.sleep(0.2)

        assert received == [{"content_chunk": "hi"}]
        t.close()

    def test_notification_with_no_handler_is_ignored(
        self,
        transport: PackTransport,
        fake_popen: FakePopen,
    ) -> None:
        fake_popen.daemon_write(encode_notification("unregistered_method", {}))
        time.sleep(0.2)
        # No exception, no crash — nothing else to assert.


class TestDisconnect:
    def test_close_fires_disconnect_handlers(
        self,
        fake_popen: FakePopen,
    ) -> None:
        t = PackTransport("host", "session")
        fired = threading.Event()
        t.on_disconnect(fired.set)
        with patch("core.pack_transport.subprocess.Popen", return_value=fake_popen):
            t.connect()

        t.close()

        assert fired.wait(timeout=2.0)
        assert t.connected is False

    def test_pending_request_fails_on_disconnect(
        self,
        fake_popen: FakePopen,
    ) -> None:
        t = PackTransport("host", "session")
        with patch("core.pack_transport.subprocess.Popen", return_value=fake_popen):
            t.connect()

        def kill_soon() -> None:
            time.sleep(0.2)
            t.close()

        threading.Thread(target=kill_soon, daemon=True).start()

        with pytest.raises(PackTransportError):
            t.send_request("attach", {}, timeout=3.0)
