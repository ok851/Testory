# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from database import Database


class TestMixedCaseValidation(unittest.TestCase):
    def setUp(self):
        self._fd, self._path = tempfile.mkstemp(suffix=".db")
        os.close(self._fd)
        Database._schema_initialized = False
        self.db = Database(self._path)

    def tearDown(self):
        Database._schema_initialized = False
        try:
            os.remove(self._path)
        except OSError:
            pass

    def test_create_step_with_automation_layer(self):
        pid = self.db.create_project("P", "")
        cid = self.db.create_test_case_v2(pid, "Mix", case_type="ui")
        sid = self.db.create_test_step(
            cid,
            "launch_app",
            input_value="C:\\app.exe",
            automation_layer="desktop",
            desktop_spec='{"backend":"uia"}',
        )
        step = self.db.get_test_step(sid)
        self.assertEqual(step["automation_layer"], "desktop")
        self.assertIn("backend", step["desktop_spec"])

    def test_case_has_desktop_steps(self):
        pid = self.db.create_project("P2", "")
        cid = self.db.create_test_case_v2(pid, "Mix2", case_type="ui")
        self.db.create_test_step(cid, "navigate", input_value="https://example.com")
        self.assertFalse(self.db.case_has_desktop_steps(cid))
        self.db.create_test_step(cid, "click", automation_layer="desktop")
        self.assertTrue(self.db.case_has_desktop_steps(cid))


if __name__ == "__main__":
    unittest.main()
