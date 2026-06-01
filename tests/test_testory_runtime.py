# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from packaging.testory_runtime import (
    bundled_python_candidates,
    resolve_bundled_python,
    verify_bundled_python,
)


class TestTestoryRuntime(unittest.TestCase):
    def test_resolve_prefers_pythonw_in_flat_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flat = root / ".venv"
            flat.mkdir()
            (flat / "python.exe").write_text("", encoding="utf-8")
            (flat / "pythonw.exe").write_text("", encoding="utf-8")
            resolved = resolve_bundled_python(root)
            self.assertEqual(resolved, flat / "pythonw.exe")

    def test_resolve_legacy_scripts_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / "pythonw.exe").write_text("", encoding="utf-8")
            resolved = resolve_bundled_python(root)
            self.assertEqual(resolved, scripts / "pythonw.exe")

    def test_verify_reports_missing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            interpreter, err = verify_bundled_python(Path(tmp))
            self.assertIsNone(interpreter)
            self.assertIn("未找到内置 Python", err or "")

    def test_verify_reports_broken_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            py = scripts / "python.exe"
            py.write_text("", encoding="utf-8")

            def fake_run(*args, **kwargs):
                return type(
                    "R",
                    (),
                    {
                        "returncode": 103,
                        "stdout": "",
                        "stderr": "did not find executable at 'Z:\\Missing\\python.exe'",
                    },
                )()

            with patch("packaging.testory_runtime.subprocess.run", side_effect=fake_run):
                interpreter, err = verify_bundled_python(root)
            self.assertIsNotNone(interpreter)
            self.assertIn("incomplete", (err or "").lower())


if __name__ == "__main__":
    unittest.main()
