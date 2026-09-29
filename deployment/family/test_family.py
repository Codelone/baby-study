import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from family import Supervisor, TrialError


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


if __name__ == "__main__":
    unittest.main()
