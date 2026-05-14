import os
import tempfile
import unittest

from database import Database


class CaseTypeSchemaTest(unittest.TestCase):
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

    def test_default_case_type_ui_and_api_filter(self):
        pid = self.db.create_project("p1", "d")
        ui_id = self.db.create_test_case_v2(pid, "UI1", case_type="ui")
        api_id = self.db.create_test_case_v2(pid, "API1", case_type="api")
        cases_ui = self.db.get_project_cases(pid, case_type="ui")
        cases_api = self.db.get_project_cases(pid, case_type="api")
        self.assertEqual({c["id"] for c in cases_ui}, {ui_id})
        self.assertEqual({c["id"] for c in cases_api}, {api_id})

    def test_step_validation_via_create(self):
        pid = self.db.create_project("p2", "")
        api_id = self.db.create_test_case_v2(pid, "A", case_type="api")
        self.db.create_test_step(api_id, "api_request", api_spec='{"method":"GET","url":"http://example.com","expected_status":200}')
        steps = self.db.get_case_steps(api_id)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["action"], "api_request")

    def test_migrate_api_steps(self):
        pid = self.db.create_project("p3", "")
        ui_id = self.db.create_test_case_v2(pid, "Mix", case_type="ui")
        self.db.create_test_step(ui_id, "click", selector_type="css", selector_value="#x")
        self.db.create_test_step(
            ui_id,
            "api_request",
            api_spec='{"method":"GET","url":"http://example.com","expected_status":200}',
        )
        r = self.db.migrate_api_steps_from_ui_case(ui_id)
        self.assertTrue(r.get("success"))
        api_case_id = r["target_api_case_id"]
        ui_steps = self.db.get_case_steps(ui_id)
        api_steps = self.db.get_case_steps(api_case_id)
        self.assertEqual(len(ui_steps), 1)
        self.assertEqual(ui_steps[0]["action"], "click")
        self.assertEqual(len(api_steps), 1)
        self.assertEqual(api_steps[0]["action"], "api_request")


if __name__ == "__main__":
    unittest.main()
