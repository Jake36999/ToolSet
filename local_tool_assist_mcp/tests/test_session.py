"""Tests for local_tool_assist_mcp.session."""

import os
import pathlib
import re
import tempfile
import unittest

from local_tool_assist_mcp import session as sess

_SESSION_ID_RE = re.compile(r"^lta_[0-9]{8}T[0-9]{6}Z_[a-z0-9]{6}$")
_TOOLCHAIN_ROOT = (pathlib.Path(__file__).parent.parent.parent / "aletheia_toolchain").resolve()


# ---------------------------------------------------------------------------
# generate_session_id
# ---------------------------------------------------------------------------

class TestGenerateSessionId(unittest.TestCase):

    def test_session_id_matches_pattern(self):
        sid = sess.generate_session_id()
        self.assertRegex(sid, _SESSION_ID_RE)

    def test_session_ids_are_unique(self):
        ids = {sess.generate_session_id() for _ in range(50)}
        self.assertGreater(len(ids), 1)


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------

class TestCreateSession(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make(self, **kwargs):
        kwargs.setdefault("output_root", self.output_root)
        return sess.create_session("test objective", "/some/repo", **kwargs)

    # Return types
    def test_returns_dict_and_path(self):
        sd, session_dir = self._make()
        self.assertIsInstance(sd, dict)
        self.assertIsInstance(session_dir, pathlib.Path)

    def test_session_dir_is_created(self):
        _, session_dir = self._make()
        self.assertTrue(session_dir.is_dir())

    # Output subdirectories
    def test_all_output_subdirs_created(self):
        _, _ = self._make()
        for subdir in sess._OUTPUT_SUBDIRS:
            self.assertTrue(
                (self.output_root / subdir).is_dir(),
                f"Missing output subdir: {subdir}",
            )

    # Session dict structure
    def test_session_id_format(self):
        sd, _ = self._make()
        self.assertRegex(sd["session_id"], _SESSION_ID_RE)

    def test_required_top_level_keys(self):
        sd, _ = self._make()
        required = (
            "schema_version", "session_id", "created_at", "updated_at",
            "request", "execution_mode", "review_state", "policy",
            "artifacts", "steps", "redaction",
        )
        for key in required:
            self.assertIn(key, sd, f"Missing key: {key!r}")

    def test_schema_version(self):
        sd, _ = self._make()
        self.assertEqual(sd["schema_version"], "LocalToolAssistSession/v1.0")

    def test_review_state_fields_present(self):
        sd, _ = self._make()
        rs = sd["review_state"]
        for field in ("scan_reviewed", "manifest_reviewed", "slice_approved",
                      "approved_by", "approved_at", "approval_notes"):
            self.assertIn(field, rs, f"Missing review_state field: {field!r}")

    def test_review_state_defaults_false_or_empty(self):
        sd, _ = self._make()
        rs = sd["review_state"]
        self.assertIs(rs["scan_reviewed"], False)
        self.assertIs(rs["manifest_reviewed"], False)
        self.assertIs(rs["slice_approved"], False)
        self.assertEqual(rs["approved_by"], "")
        self.assertEqual(rs["approved_at"], "")
        self.assertEqual(rs["approval_notes"], "")

    def test_allow_command_execution_is_false(self):
        sd, _ = self._make()
        self.assertIs(sd["execution_mode"]["allow_command_execution"], False)

    def test_steps_is_empty_list(self):
        sd, _ = self._make()
        self.assertEqual(sd["steps"], [])

    def test_all_artifact_fields_present(self):
        sd, _ = self._make()
        for field in (
            "manifest_csv", "manifest_health_json", "manifest_doctor_json",
            "manifest_doctor_md", "command_lint_json", "slicer_json",
            "slicer_md", "final_markdown", "final_python_bundle", "archive_yaml",
        ):
            self.assertIn(field, sd["artifacts"], f"Missing artifact field: {field!r}")

    # Policy
    def test_policy_forbid_shell(self):
        sd, _ = self._make()
        self.assertTrue(sd["policy"]["forbid_shell"])

    def test_policy_outputs_outside_toolchain(self):
        sd, _ = self._make()
        self.assertTrue(sd["policy"]["outputs_must_be_outside_toolchain"])

    def test_policy_approved_tools_non_empty(self):
        sd, _ = self._make()
        self.assertGreater(len(sd["policy"]["approved_tools"]), 0)

    # Boundary checks
    def test_session_dir_outside_aletheia_toolchain(self):
        _, session_dir = self._make()
        resolved = session_dir.resolve()
        self.assertFalse(
            str(resolved).startswith(str(_TOOLCHAIN_ROOT)),
            f"session_dir {resolved} is inside aletheia_toolchain",
        )

    def test_default_output_root_outside_aletheia_toolchain(self):
        resolved = sess.DEFAULT_OUTPUT_ROOT.resolve()
        self.assertFalse(
            str(resolved).startswith(str(_TOOLCHAIN_ROOT)),
            f"DEFAULT_OUTPUT_ROOT {resolved} is inside aletheia_toolchain",
        )

    # Environment variable override
    def test_lta_output_root_env_override(self):
        with tempfile.TemporaryDirectory() as override_dir:
            prev = os.environ.get("LTA_OUTPUT_ROOT")
            try:
                os.environ["LTA_OUTPUT_ROOT"] = override_dir
                _, session_dir = sess.create_session("obj", "/repo")
                self.assertTrue(
                    str(session_dir.resolve()).startswith(
                        str(pathlib.Path(override_dir).resolve())
                    )
                )
            finally:
                if prev is None:
                    os.environ.pop("LTA_OUTPUT_ROOT", None)
                else:
                    os.environ["LTA_OUTPUT_ROOT"] = prev

    # Explicit output_root kwarg overrides env
    def test_explicit_output_root_overrides_env(self):
        with tempfile.TemporaryDirectory() as env_dir:
            prev = os.environ.get("LTA_OUTPUT_ROOT")
            try:
                os.environ["LTA_OUTPUT_ROOT"] = env_dir
                _, session_dir = self._make()  # passes output_root=self.output_root
                self.assertTrue(
                    str(session_dir.resolve()).startswith(
                        str(self.output_root.resolve())
                    ),
                    "Explicit output_root was ignored in favour of LTA_OUTPUT_ROOT",
                )
            finally:
                if prev is None:
                    os.environ.pop("LTA_OUTPUT_ROOT", None)
                else:
                    os.environ["LTA_OUTPUT_ROOT"] = prev

    def test_rejects_output_root_inside_toolchain(self):
        with self.assertRaises(ValueError):
            sess.create_session(
                "obj",
                "/repo",
                output_root=sess.TOOLCHAIN_ROOT / "local_tool_assist_outputs",
            )


class TestSessionPaths(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.temp_root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_toolchain_name_must_match(self):
        with self.assertRaises(ValueError):
            sess.SessionPaths(
                toolset_root=self.temp_root,
                toolchain_root=self.temp_root / "wrong_toolchain",
                output_root=self.temp_root / "local_tool_assist_outputs",
                sessions_root=self.temp_root / "local_tool_assist_outputs" / "sessions",
                reports_root=self.temp_root / "local_tool_assist_outputs" / "reports",
                bundles_root=self.temp_root / "local_tool_assist_outputs" / "bundles",
                archive_root=self.temp_root / "local_tool_assist_outputs" / "archive",
            )

    def test_rejects_generated_paths_inside_toolchain(self):
        toolchain_root = self.temp_root / "aletheia_toolchain"
        with self.assertRaises(ValueError):
            sess.SessionPaths(
                toolset_root=self.temp_root,
                toolchain_root=toolchain_root,
                output_root=self.temp_root / "safe_outputs",
                sessions_root=toolchain_root / "local_tool_assist_outputs" / "sessions",
                reports_root=self.temp_root / "safe_outputs" / "reports",
                bundles_root=self.temp_root / "safe_outputs" / "bundles",
                archive_root=self.temp_root / "safe_outputs" / "archive",
            )


# ---------------------------------------------------------------------------
# save_session / load_session round-trip
# ---------------------------------------------------------------------------

class TestSessionYamlRoundtrip(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @unittest.skipUnless(sess._HAS_YAML, "PyYAML not installed")
    def test_yaml_roundtrip_schema_version(self):
        sd, session_dir = sess.create_session(
            "round-trip", "/repo", output_root=self.output_root
        )
        path = session_dir / "session.yaml"
        sess.save_session(sd, path)
        loaded = sess.load_session(path)
        self.assertEqual(loaded["schema_version"], sd["schema_version"])

    @unittest.skipUnless(sess._HAS_YAML, "PyYAML not installed")
    def test_yaml_roundtrip_session_id(self):
        sd, session_dir = sess.create_session(
            "round-trip", "/repo", output_root=self.output_root
        )
        path = session_dir / "session.yaml"
        sess.save_session(sd, path)
        loaded = sess.load_session(path)
        self.assertEqual(loaded["session_id"], sd["session_id"])

    @unittest.skipUnless(sess._HAS_YAML, "PyYAML not installed")
    def test_yaml_roundtrip_review_state(self):
        sd, session_dir = sess.create_session(
            "round-trip", "/repo", output_root=self.output_root
        )
        path = session_dir / "session.yaml"
        sess.save_session(sd, path)
        loaded = sess.load_session(path)
        self.assertEqual(loaded["review_state"], sd["review_state"])

    @unittest.skipUnless(sess._HAS_YAML, "PyYAML not installed")
    def test_yaml_roundtrip_execution_mode(self):
        sd, session_dir = sess.create_session(
            "round-trip", "/repo", output_root=self.output_root
        )
        path = session_dir / "session.yaml"
        sess.save_session(sd, path)
        loaded = sess.load_session(path)
        self.assertEqual(loaded["execution_mode"], sd["execution_mode"])
        self.assertIs(loaded["execution_mode"]["allow_command_execution"], False)

    @unittest.skipUnless(sess._HAS_YAML, "PyYAML not installed")
    def test_yaml_roundtrip_policy(self):
        sd, session_dir = sess.create_session(
            "round-trip", "/repo", output_root=self.output_root
        )
        path = session_dir / "session.yaml"
        sess.save_session(sd, path)
        loaded = sess.load_session(path)
        self.assertEqual(loaded["policy"], sd["policy"])

    @unittest.skipUnless(sess._HAS_YAML, "PyYAML not installed")
    def test_yaml_roundtrip_review_state_after_update(self):
        sd, session_dir = sess.create_session(
            "round-trip", "/repo", output_root=self.output_root
        )
        sess.update_review_state(sd, scan_reviewed=True, approved_by="orchestrator")
        path = session_dir / "session.yaml"
        sess.save_session(sd, path)
        loaded = sess.load_session(path)
        self.assertTrue(loaded["review_state"]["scan_reviewed"])
        self.assertEqual(loaded["review_state"]["approved_by"], "orchestrator")


# ---------------------------------------------------------------------------
# update_review_state
# ---------------------------------------------------------------------------

class TestUpdateReviewState(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_session(self) -> dict:
        sd, _ = sess.create_session("test", "/repo", output_root=self.output_root)
        return sd

    def test_updates_scan_reviewed(self):
        sd = self._make_session()
        result = sess.update_review_state(sd, scan_reviewed=True)
        self.assertTrue(result["review_state"]["scan_reviewed"])

    def test_returns_same_dict(self):
        sd = self._make_session()
        result = sess.update_review_state(sd, scan_reviewed=True)
        self.assertIs(result, sd)

    def test_updates_slice_approved(self):
        sd = self._make_session()
        sess.update_review_state(sd, slice_approved=True, approved_by="orchestrator")
        self.assertTrue(sd["review_state"]["slice_approved"])
        self.assertEqual(sd["review_state"]["approved_by"], "orchestrator")

    def test_updates_multiple_fields(self):
        sd = self._make_session()
        sess.update_review_state(
            sd,
            scan_reviewed=True,
            manifest_reviewed=True,
            approval_notes="LGTM",
        )
        self.assertTrue(sd["review_state"]["scan_reviewed"])
        self.assertTrue(sd["review_state"]["manifest_reviewed"])
        self.assertEqual(sd["review_state"]["approval_notes"], "LGTM")

    def test_rejects_unknown_field(self):
        sd = self._make_session()
        with self.assertRaises(ValueError):
            sess.update_review_state(sd, nonexistent_field=True)

    def test_rejects_unknown_field_mixed_with_valid(self):
        sd = self._make_session()
        with self.assertRaises(ValueError):
            sess.update_review_state(sd, scan_reviewed=True, bad_field="x")

    def test_updated_at_is_set(self):
        sd = self._make_session()
        self.assertIn("updated_at", sd)
        sess.update_review_state(sd, scan_reviewed=True)
        self.assertIn("updated_at", sd)


if __name__ == "__main__":
    unittest.main()
