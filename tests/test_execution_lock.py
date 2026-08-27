# -*- coding: utf-8 -*-
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.execution.execution_lock import LocalExecutionLock, acquire, lock_file_path, release


class TestExecutionLock(unittest.TestCase):
    def test_acquire_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("modules.execution.execution_lock.lock_file_path") as mock_path:
                p = Path(tmp) / ".uat_execution.lock"
                mock_path.return_value = p
                lock = LocalExecutionLock()
                self.assertTrue(lock.acquire(owner="test", timeout_sec=5))
                self.assertTrue(lock.is_held())
                self.assertTrue(p.exists())
                lock.release()
                self.assertFalse(lock.is_held())

    def test_second_acquire_blocks_then_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("modules.execution.execution_lock.lock_file_path") as mock_path:
                p = Path(tmp) / ".uat_execution.lock"
                mock_path.return_value = p
                a = LocalExecutionLock()
                b = LocalExecutionLock()
                self.assertTrue(a.acquire(owner="a", timeout_sec=5))
                self.assertFalse(b.acquire(blocking=False))
                a.release()
                self.assertTrue(b.acquire(owner="b", timeout_sec=5))
                b.release()

    def test_singleton_second_thread_nonblocking_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("modules.execution.execution_lock.lock_file_path") as mock_path:
                p = Path(tmp) / ".uat_execution.lock"
                mock_path.return_value = p
                self.assertTrue(acquire(owner="main", timeout_sec=5))
                blocked = threading.Event()
                result = {}

                def worker():
                    result["ok"] = acquire(blocking=False, owner="other")
                    blocked.set()

                t = threading.Thread(target=worker)
                t.start()
                blocked.wait(timeout=5)
                self.assertFalse(result.get("ok"))
                release()
                self.assertTrue(acquire(owner="worker", timeout_sec=5))
                release()


if __name__ == "__main__":
    unittest.main()
