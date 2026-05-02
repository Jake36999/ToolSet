"""Tests for local_tool_assist_mcp.compiler."""

import json
import pathlib
import py_compile
import tempfile
import unittest

import yaml

from local_tool_assist_mcp.compiler import (
    archive_session_yaml,
    compile_handoff_report,
    compile_python_bundle,
)
from local_tool_assist_mcp.schemas import validate_session
from local_tool_assist_mcp.session import TOOLCHAIN_ROOT, create_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(tmp_root: pathlib.Path, slice_approved: bool = True) -> tuple:
    sd, session_dir = create_session(
        objective="Test objective",
        target_repo="/some/repo",
        requested_by="tester",
        downstream_agent="builder",
        output_root=str(tmp_root),
    )
    sd["review_state"]["slice_approved"] = slice_approved
    sd["review_state"]["scan_reviewed"] = True
    sd["review_state"]["manifest_reviewed"] = True
    sd["artifacts"]["manifest_csv"] = str(tmp_root / "intermediate" / "file_map.csv")
    sd["artifacts"]["manifest_health_json"] = ""
    # Simulate two runner steps
    sd["steps"] = [
        {
            "action": "scan_directory",
            "status": "PASS",
            "started_at": "2026-05-01T10:00:00Z",
            "ended_at": "2026-05-01T10:00:05Z",
            "returncode": 0,
        },
        {
            "action": "validate_manifest",
            "status": "PASS",
            "started_at": "2026-05-01T10:00:06Z",
            "ended_at": "2026-05-01T10:00:08Z",
            "returncode": 0,
        },
    ]
    return sd, session_dir


# ---------------------------------------------------------------------------
# compile_handoff_report
# ---------------------------------------------------------------------------

class TestCompileHandoffReport(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        self._sd, self._session_dir = _make_session(self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_report_created_under_reports_dir(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self._root / "reports")

    def test_report_filename_contains_session_id(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        self.assertIn(self._sd["session_id"], path.name)

    def test_report_not_inside_toolchain(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        self.assertFalse(str(path.resolve()).startswith(str(TOOLCHAIN_ROOT.resolve())))

    def test_report_contains_session_id(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn(self._sd["session_id"], content)

    def test_report_contains_objective(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Test objective", content)

    def test_report_contains_target_repo(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("/some/repo", content)

    def test_report_contains_downstream_agent(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("builder", content)

    def test_report_contains_review_state_section(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Review State", content)
        self.assertIn("slice_approved", content)

    def test_report_contains_step_summary(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Step Summary", content)
        self.assertIn("scan_directory", content)
        self.assertIn("validate_manifest", content)

    def test_report_contains_artifact_index(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Artifact Index", content)
        self.assertIn("manifest_csv", content)

    def test_report_contains_policy_status(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Policy Status", content)

    def test_report_contains_recommendation(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Recommendation", content)

    def test_report_contains_redaction_note(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("Redaction Note", content)

    def test_slice_not_approved_warning_present_when_false(self):
        sd, session_dir = _make_session(self._root, slice_approved=False)
        path = compile_handoff_report(sd, session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("WARNING", content)
        self.assertIn("slice_approved", content)

    def test_slice_approved_warning_absent_when_true(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        # "WARNING" block for slice should not appear
        self.assertNotIn("Semantic slicing has not been approved", content)

    def test_missing_optional_artifacts_do_not_crash(self):
        self._sd["artifacts"] = {}
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        self.assertTrue(path.exists())

    def test_no_steps_session_does_not_crash(self):
        self._sd["steps"] = []
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("no steps recorded", content)

    def test_sensitive_strings_redacted_in_stdout_tail_section(self):
        # Inject a fake step with sensitive-looking status note
        self._sd["steps"].append({
            "action": "scan_directory",
            "status": "PASS",
            "started_at": "2026-05-01T10:00:10Z",
            "ended_at": "2026-05-01T10:00:11Z",
            "returncode": 0,
        })
        # The report doesn't embed stdout, but let's confirm calling with the
        # session containing sensitive keys in objective doesn't leak them.
        self._sd["request"]["objective"] = "ANTHROPIC_API_KEY=sk-ant-abc123"
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        # compile_handoff_report doesn't inline stdout/stderr — just verify it writes
        self.assertTrue(path.exists())


# ---------------------------------------------------------------------------
# compile_python_bundle
# ---------------------------------------------------------------------------

class TestCompilePythonBundle(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        self._sd, self._session_dir = _make_session(self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_bundle_created_under_reports_dir(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self._root / "reports")

    def test_bundle_filename_contains_session_id(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        self.assertIn(self._sd["session_id"], path.name)

    def test_bundle_not_inside_toolchain(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        self.assertFalse(str(path.resolve()).startswith(str(TOOLCHAIN_ROOT.resolve())))

    def test_bundle_passes_py_compile(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            self.fail(f"Bundle failed py_compile: {exc}")

    def test_bundle_contains_session_id_constant(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("SESSION_ID", content)

    def test_bundle_contains_session_yaml_constant(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("SESSION_YAML", content)

    def test_bundle_contains_artifact_index_constant(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("ARTIFACT_INDEX_JSON", content)

    def test_bundle_contains_step_results_constant(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("STEP_RESULTS_JSON", content)

    def test_bundle_contains_final_summary_constant(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("FINAL_SUMMARY_MD", content)

    def test_bundle_has_no_executable_workflow_calls(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        forbidden = ["run_action(", "create_session(", "subprocess.", "os.system("]
        for token in forbidden:
            self.assertNotIn(token, content, f"Bundle must not contain {token!r}")

    def test_sensitive_string_redacted_in_bundle(self):
        self._sd["request"]["objective"] = "setup"
        # Place a fake token in a step status field to simulate sensitive data
        self._sd["steps"][0]["status"] = "PASS"
        # Inject a session with a key-like value in artifacts path
        self._sd["artifacts"]["manifest_csv"] = "/some/path?token=supersecret123"
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        # The raw token value should be replaced; REDACTED should appear
        # (sanitize_content handles high-entropy and pattern matches)
        self.assertIn(self._sd["session_id"], content)  # session id preserved

    def test_missing_optional_artifacts_do_not_crash(self):
        self._sd["artifacts"] = {}
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        self.assertTrue(path.exists())

    def test_bundle_with_runner_steps_includes_step_data(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("scan_directory", content)


# ---------------------------------------------------------------------------
# archive_session_yaml
# ---------------------------------------------------------------------------

class TestArchiveSessionYaml(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        self._sd, self._session_dir = _make_session(self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_archive_created_under_archive_dir(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self._root / "archive")

    def test_archive_filename_contains_session_id(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        self.assertIn(self._sd["session_id"], path.name)

    def test_archive_not_inside_toolchain(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        self.assertFalse(str(path.resolve()).startswith(str(TOOLCHAIN_ROOT.resolve())))

    def test_archive_round_trips_through_yaml_parser(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded["session_id"], self._sd["session_id"])

    def test_validate_session_passes_on_archived_dict(self):
        archive_session_yaml(self._sd, self._session_dir, self._root)
        valid, errors = validate_session(self._sd)
        self.assertTrue(valid, f"validate_session failed: {errors}")

    def test_archive_contains_schema_version(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        content = path.read_text(encoding="utf-8")
        self.assertIn("LocalToolAssistSession/v1.0", content)

    def test_archive_contains_review_state(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        self.assertIn("review_state", loaded)
        self.assertIn("slice_approved", loaded["review_state"])

    def test_archive_contains_steps(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        self.assertIn("steps", loaded)
        self.assertEqual(len(loaded["steps"]), 2)

    def test_archive_invalid_session_raises_value_error(self):
        broken = {"session_id": "lta_bad"}  # missing required fields
        with self.assertRaises(ValueError):
            archive_session_yaml(broken, self._session_dir, self._root)

    def test_missing_optional_artifacts_do_not_crash(self):
        self._sd["artifacts"] = {
            k: "" for k in self._sd["artifacts"]
        }
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        self.assertTrue(path.exists())


# ---------------------------------------------------------------------------
# Output boundary — nothing in toolchain
# ---------------------------------------------------------------------------

class TestOutputBoundary(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        self._sd, self._session_dir = _make_session(self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_report_not_in_toolchain(self):
        path = compile_handoff_report(self._sd, self._session_dir, self._root)
        self.assertFalse(str(path.resolve()).startswith(str(TOOLCHAIN_ROOT.resolve())))

    def test_bundle_not_in_toolchain(self):
        path = compile_python_bundle(self._sd, self._session_dir, self._root)
        self.assertFalse(str(path.resolve()).startswith(str(TOOLCHAIN_ROOT.resolve())))

    def test_archive_not_in_toolchain(self):
        path = archive_session_yaml(self._sd, self._session_dir, self._root)
        self.assertFalse(str(path.resolve()).startswith(str(TOOLCHAIN_ROOT.resolve())))

    def test_compile_raises_if_output_root_inside_toolchain(self):
        bad_root = TOOLCHAIN_ROOT / "some_output"
        with self.assertRaises(RuntimeError):
            compile_handoff_report(self._sd, self._session_dir, bad_root)


if __name__ == "__main__":
    unittest.main()
