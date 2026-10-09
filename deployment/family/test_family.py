import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from family import Supervisor, TrialError, ensure_loopback_ports_free


class ProcessOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="family-trial-")
        self.runtime = Path(self.temp.name) / "runtime"
        self.patches = [
            patch("family.RUNTIME", self.runtime),
            patch("family.CONTROL", self.runtime / "control.sock"),
        ]
        for patched in self.patches:
            patched.start()
        self.supervisor = Supervisor(dict(os.environ))
        self.supervisor.acquire()

    def tearDown(self):
        self.supervisor.close()
        for patched in reversed(self.patches):
            patched.stop()
        self.temp.cleanup()

    def test_close_stops_owned_process_and_preserves_unrelated_process(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            owned = self.supervisor.spawn(
                "owned", [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            self.supervisor.close()
            self.assertIsNotNone(owned.poll())
            self.assertIsNone(other.poll())
            self.assertFalse((self.runtime / "control.sock").exists())
        finally:
            other.terminate()
            other.wait(timeout=5)

    def test_second_start_cannot_remove_first_control_socket(self):
        second = Supervisor(dict(os.environ))
        with self.assertRaises(TrialError):
            second.acquire()
        second.close()
        self.assertTrue((self.runtime / "control.sock").exists())
        self.assertEqual((self.runtime / "control.sock").stat().st_mode & 0o777, 0o600)

    def test_stop_request_and_child_failure_are_detected(self):
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(self.runtime / "control.sock"))
            client.sendall(b"stop\n")
            with self.assertRaises(TrialError):
                self.supervisor.tick()
            self.assertEqual(client.recv(32), b"stopping\n")
        self.supervisor.stopping = False
        child = self.supervisor.spawn(
            "failed", [sys.executable, "-c", "raise SystemExit(7)"]
        )
        child.wait(timeout=5)
        with self.assertRaisesRegex(TrialError, "failed 意外退出"):
            self.supervisor.tick()

    def test_status_uses_live_control_and_component_state(self):
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(self.runtime / "control.sock"))
            client.sendall(b"status\n")
            self.supervisor.tick()
            body = json.loads(client.recv(65536))
        self.assertEqual(body["phase"], "preparing")
        self.assertEqual(body["components_alive"], {})

    def test_missing_tunnel_is_fatal_and_retains_reason(self):
        self.supervisor.tunnel_missing_since = time.monotonic() - 31
        with self.assertRaisesRegex(TrialError, "Tunnel not found"):
            self.supervisor.tick()
        self.supervisor.failure = "Tunnel not found"
        self.supervisor.close()
        body = json.loads((self.runtime / "state.json").read_text())
        self.assertEqual(body["phase"], "failed")
        self.assertEqual(body["error"], "Tunnel not found")

    def test_logs_rotate_without_blocking_child_and_remain_readable(self):
        child = self.supervisor.spawn(
            "volume", [sys.executable, "-c", "for i in range(11000): print('x'*1024)"]
        )
        child.wait(timeout=10)
        for thread in self.supervisor.collectors:
            thread.join(timeout=5)
        self.assertTrue((self.supervisor.logs / "volume.log.1").exists())
        self.assertGreater((self.supervisor.logs / "volume.log").stat().st_size, 0)

    def test_cleanup_keeps_recent_logs_and_backups(self):
        self.supervisor.close()
        old = self.runtime / "logs" / "old"
        old.mkdir()
        (old / "web.log").write_text("old")
        os.utime(old, (time.time() - 31 * 86400,) * 2)
        backup = self.runtime / "backups"
        backup.mkdir()
        (backup / "keep.dump").write_text("backup")
        next_run = Supervisor(dict(os.environ))
        try:
            next_run.acquire()
            self.assertFalse(old.exists())
            self.assertTrue((backup / "keep.dump").exists())
            self.assertTrue(self.supervisor.logs.exists())
        finally:
            next_run.close()

    def test_live_unrelated_listener_is_not_treated_as_available(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaises(TrialError):
                ensure_loopback_ports_free([listener.getsockname()[1]])

    def test_recently_closed_connection_does_not_block_restart(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen()
            with socket.create_connection(("127.0.0.1", port)) as client:
                connection, _ = listener.accept()
                connection.close()
                self.assertEqual(client.recv(1), b"")
        ensure_loopback_ports_free([port])


if __name__ == "__main__":
    unittest.main()
