"""Tests for local_tool_assist_mcp.schemas."""

import copy
import json
import pathlib
import tempfile
import unittest

from local_tool_assist_mcp import schemas, session as sess


# ---------------------------------------------------------------------------
# Schema file
# ---------------------------------------------------------------------------

class TestSchemaFile(unittest.TestCase):

    def test_schema_file_exists(self):
        self.assertTrue(schemas.get_schema_path().is_file())

    def test_schema_is_valid_json(self):
        path = schemas.get_schema_path()
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, dict)

    def test_schema_has_correct_id(self):
        with open(schemas.get_schema_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data.get("$id"), "LocalToolAssistSession/v1.0")

    def test_schema_requires_review_state(self):
        with open(schemas.get_schema_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("review_state", data["required"])

    def test_schema_review_state_requires_slice_approved(self):
        with open(schemas.get_schema_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        review_required = data["properties"]["review_state"]["required"]
        self.assertIn("slice_approved", review_required)

    def test_schema_execution_mode_const_false(self):
        with open(schemas.get_schema_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        allow = (
            data["properties"]["execution_mode"]
            ["properties"]["allow_command_execution"]
        )
        self.assertEqual(allow.get("const"), False)


# ---------------------------------------------------------------------------
# validate_session — valid inputs
# ---------------------------------------------------------------------------

class TestValidateSessionValid(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make(self):
        sd, _ = sess.create_session(
            "validate test", "/some/repo", output_root=self.output_root
        )
        return sd

    def test_fresh_session_passes_validation(self):
        sd = self._make()
        valid, errors = schemas.validate_session(sd)
        self.assertTrue(valid, f"Unexpected errors: {errors}")
        self.assertEqual(errors, [])

    def test_session_with_updated_review_state_passes(self):
        sd = self._make()
        sess.update_review_state(sd, scan_reviewed=True, approved_by="orchestrator")
        valid, errors = schemas.validate_session(sd)
        self.assertTrue(valid, f"Unexpected errors: {errors}")

    def test_session_with_all_review_approved_passes(self):
        sd = self._make()
        sess.update_review_state(
            sd,
            scan_reviewed=True,
            manifest_reviewed=True,
            slice_approved=True,
            approved_by="orchestrator",
            approved_at="2026-05-01T12:00:00Z",
            approval_notes="LGTM",
        )
        valid, errors = schemas.validate_session(sd)
        self.assertTrue(valid, f"Unexpected errors: {errors}")


# ---------------------------------------------------------------------------
# validate_session — invalid inputs
# ---------------------------------------------------------------------------

class TestValidateSessionInvalid(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make(self):
        sd, _ = sess.create_session(
            "validate test", "/some/repo", output_root=self.output_root
        )
        return sd

    def test_fails_for_missing_review_state(self):
        sd = self._make()
        del sd["review_state"]
        valid, errors = schemas.validate_session(sd)
        self.assertFalse(valid)
        self.assertTrue(len(errors) > 0)

    def test_fails_for_wrong_schema_version(self):
        sd = self._make()
        sd["schema_version"] = "WrongVersion/v9"
        valid, errors = schemas.validate_session(sd)
        self.assertFalse(valid)
        self.assertTrue(len(errors) > 0)

    def test_fails_for_missing_session_id(self):
        sd = self._make()
        del sd["session_id"]
        valid, errors = schemas.validate_session(sd)
        self.assertFalse(valid)

    def test_fails_for_allow_command_execution_true(self):
        sd = self._make()
        sd["execution_mode"]["allow_command_execution"] = True
        valid, errors = schemas.validate_session(sd)
        self.assertFalse(valid)
        self.assertTrue(len(errors) > 0)

    def test_fails_for_missing_slice_approved(self):
        sd = self._make()
        del sd["review_state"]["slice_approved"]
        valid, errors = schemas.validate_session(sd)
        self.assertFalse(valid)

    def test_returns_list_of_strings(self):
        sd = self._make()
        del sd["review_state"]
        del sd["session_id"]
        valid, errors = schemas.validate_session(sd)
        self.assertFalse(valid)
        for e in errors:
            self.assertIsInstance(e, str)

    def test_empty_dict_fails(self):
        valid, errors = schemas.validate_session({})
        self.assertFalse(valid)
        self.assertTrue(len(errors) > 0)


if __name__ == "__main__":
    unittest.main()
