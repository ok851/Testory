# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution_lock import LocalExecutionLock, lock_file_path


class TestExecutionLock(unittest.TestCase):
    def test_acquire_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("execution_lock.lock_file_path") as mock_path:
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
            with patch("execution_lock.lock_file_path") as mock_path:
                p = Path(tmp) / ".uat_execution.lock"
                mock_path.return_value = p
                a = LocalExecutionLock()
                b = LocalExecutionLock()
                self.assertTrue(a.acquire(owner="a", timeout_sec=5))
                self.assertFalse(b.acquire(blocking=False))
                a.release()
                self.assertTrue(b.acquire(owner="b", timeout_sec=5))
                b.release()


if __name__ == "__main__":
    unittest.main()
