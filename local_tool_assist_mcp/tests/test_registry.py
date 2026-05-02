"""Tests for local_tool_assist_mcp.tool_registry."""

import pathlib
import tempfile
import unittest

from local_tool_assist_mcp.tool_registry import (
    ALLOWED_SCRIPT_NAMES,
    REGISTRY,
    ToolEntry,
    _doctor_flags,
    _linter_flags,
    _scan_flags,
    _slicer_flags,
    get_entry,
)

_TOOLCHAIN_ROOT = (
    pathlib.Path(__file__).parent.parent.parent / "aletheia_toolchain"
).resolve()

_V1_ACTIONS = {"scan_directory", "validate_manifest", "lint_tool_command", "run_semantic_slice"}


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------

class TestRegistryStructure(unittest.TestCase):

    def test_registry_has_exactly_four_actions(self):
        self.assertEqual(set(REGISTRY.keys()), _V1_ACTIONS)

    def test_all_entries_are_tool_entry_instances(self):
        for name, entry in REGISTRY.items():
            self.assertIsInstance(entry, ToolEntry, f"{name!r} is not a ToolEntry")

    def test_all_entries_have_positive_timeout(self):
        for name, entry in REGISTRY.items():
            self.assertGreater(
                entry.timeout_seconds, 0,
                f"{name!r} timeout must be positive",
            )

    def test_all_entries_have_callable_build_flags(self):
        for name, entry in REGISTRY.items():
            self.assertTrue(callable(entry.build_flags), f"{name!r}.build_flags is not callable")

    def test_all_entries_have_callable_collect_artifacts(self):
        for name, entry in REGISTRY.items():
            self.assertTrue(
                callable(entry.collect_artifacts),
                f"{name!r}.collect_artifacts is not callable",
            )

    def test_all_entries_have_non_empty_script_name(self):
        for name, entry in REGISTRY.items():
            self.assertTrue(entry.script_name, f"{name!r}.script_name is empty")

    def test_all_script_names_end_with_py(self):
        for name, entry in REGISTRY.items():
            self.assertTrue(
                entry.script_name.endswith(".py"),
                f"{name!r}.script_name does not end with .py",
            )


# ---------------------------------------------------------------------------
# Review gate flags
# ---------------------------------------------------------------------------

class TestReviewGateFlags(unittest.TestCase):

    def test_run_semantic_slice_requires_review(self):
        self.assertTrue(REGISTRY["run_semantic_slice"].requires_review_approval)

    def test_scan_directory_does_not_require_review(self):
        self.assertFalse(REGISTRY["scan_directory"].requires_review_approval)

    def test_validate_manifest_does_not_require_review(self):
        self.assertFalse(REGISTRY["validate_manifest"].requires_review_approval)

    def test_lint_tool_command_does_not_require_review(self):
        self.assertFalse(REGISTRY["lint_tool_command"].requires_review_approval)


# ---------------------------------------------------------------------------
# get_entry
# ---------------------------------------------------------------------------

class TestGetEntry(unittest.TestCase):

    def test_returns_correct_entry_for_known_action(self):
        for action in _V1_ACTIONS:
            entry = get_entry(action)
            self.assertEqual(entry.action, action)

    def test_raises_value_error_for_unknown_action(self):
        with self.assertRaises(ValueError):
            get_entry("nonexistent_action")

    def test_raises_value_error_for_shell_command(self):
        with self.assertRaises(ValueError):
            get_entry("rm -rf /")

    def test_raises_value_error_for_empty_string(self):
        with self.assertRaises(ValueError):
            get_entry("")


# ---------------------------------------------------------------------------
# ALLOWED_SCRIPT_NAMES
# ---------------------------------------------------------------------------

class TestAllowedScriptNames(unittest.TestCase):

    def test_allowed_names_is_frozenset(self):
        self.assertIsInstance(ALLOWED_SCRIPT_NAMES, frozenset)

    def test_allowed_names_contains_all_script_names(self):
        for name, entry in REGISTRY.items():
            self.assertIn(entry.script_name, ALLOWED_SCRIPT_NAMES)

    def test_known_scripts_are_present(self):
        expected = {
            "create_file_map_v3.py",
            "manifest_doctor.py",
            "tool_command_linter.py",
            "semantic_slicer_v7.0.py",
        }
        self.assertEqual(ALLOWED_SCRIPT_NAMES, expected)


# ---------------------------------------------------------------------------
# build_flags — scan_directory
# ---------------------------------------------------------------------------

class TestScanFlags(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.session_dir = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_contains_roots_flag(self):
        flags = _scan_flags({"target_repo": "/some/repo"}, self.session_dir)
        self.assertIn("--roots", flags)
        idx = flags.index("--roots")
        self.assertEqual(flags[idx + 1], "/some/repo")

    def test_contains_out_flag(self):
        flags = _scan_flags({"target_repo": "/repo"}, self.session_dir)
        self.assertIn("--out", flags)

    def test_out_path_inside_session_dir(self):
        flags = _scan_flags({"target_repo": "/repo"}, self.session_dir)
        idx = flags.index("--out")
        out_path = pathlib.Path(flags[idx + 1])
        self.assertTrue(str(out_path).startswith(str(self.session_dir)))

    def test_out_path_outside_toolchain(self):
        flags = _scan_flags({"target_repo": "/repo"}, self.session_dir)
        idx = flags.index("--out")
        out_path = pathlib.Path(flags[idx + 1]).resolve()
        self.assertFalse(str(out_path).startswith(str(_TOOLCHAIN_ROOT)))

    def test_contains_health_report_flag(self):
        flags = _scan_flags({"target_repo": "/repo"}, self.session_dir)
        self.assertIn("--health-report", flags)

    def test_contains_hash_flag(self):
        flags = _scan_flags({"target_repo": "/repo"}, self.session_dir)
        self.assertIn("--hash", flags)

    def test_contains_profile_flag(self):
        flags = _scan_flags({"target_repo": "/repo"}, self.session_dir)
        self.assertIn("--profile", flags)

    def test_default_profile_is_safe(self):
        flags = _scan_flags({"target_repo": "/repo"}, self.session_dir)
        idx = flags.index("--profile")
        self.assertEqual(flags[idx + 1], "safe")

    def test_custom_profile_used(self):
        flags = _scan_flags({"target_repo": "/repo", "profile": "python"}, self.session_dir)
        idx = flags.index("--profile")
        self.assertEqual(flags[idx + 1], "python")

    def test_raises_without_target_repo(self):
        with self.assertRaises(ValueError):
            _scan_flags({}, self.session_dir)


# ---------------------------------------------------------------------------
# build_flags — validate_manifest
# ---------------------------------------------------------------------------

class TestDoctorFlags(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.session_dir = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_contains_manifest_flag(self):
        flags = _doctor_flags({"manifest_csv": "/some/file_map.csv"}, self.session_dir)
        self.assertIn("--manifest", flags)
        idx = flags.index("--manifest")
        self.assertEqual(flags[idx + 1], "/some/file_map.csv")

    def test_contains_out_flag(self):
        flags = _doctor_flags({"manifest_csv": "/some/file_map.csv"}, self.session_dir)
        self.assertIn("--out", flags)

    def test_out_path_inside_session_dir(self):
        flags = _doctor_flags({"manifest_csv": "/some/file_map.csv"}, self.session_dir)
        idx = flags.index("--out")
        self.assertTrue(flags[idx + 1].startswith(str(self.session_dir)))

    def test_contains_markdown_out_flag(self):
        flags = _doctor_flags({"manifest_csv": "/some/file_map.csv"}, self.session_dir)
        self.assertIn("--markdown-out", flags)

    def test_raises_without_manifest_csv(self):
        with self.assertRaises(ValueError):
            _doctor_flags({}, self.session_dir)


# ---------------------------------------------------------------------------
# build_flags — lint_tool_command
# ---------------------------------------------------------------------------

class TestLinterFlags(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.session_dir = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_contains_command_flag(self):
        flags = _linter_flags({"command": "python foo.py --out bar.csv"}, self.session_dir)
        self.assertIn("--command", flags)
        idx = flags.index("--command")
        self.assertEqual(flags[idx + 1], "python foo.py --out bar.csv")

    def test_contains_out_flag(self):
        flags = _linter_flags({"command": "python foo.py"}, self.session_dir)
        self.assertIn("--out", flags)

    def test_out_path_inside_session_dir(self):
        flags = _linter_flags({"command": "python foo.py"}, self.session_dir)
        idx = flags.index("--out")
        self.assertTrue(flags[idx + 1].startswith(str(self.session_dir)))

    def test_raises_without_command(self):
        with self.assertRaises(ValueError):
            _linter_flags({}, self.session_dir)


# ---------------------------------------------------------------------------
# build_flags — run_semantic_slice
# ---------------------------------------------------------------------------

class TestSlicerFlags(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.session_dir = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _params(self):
        return {"manifest_csv": "/some/file_map.csv", "target_repo": "/some/repo"}

    def test_contains_manifest_flag(self):
        flags = _slicer_flags(self._params(), self.session_dir)
        self.assertIn("--manifest", flags)

    def test_contains_o_flag_not_out(self):
        flags = _slicer_flags(self._params(), self.session_dir)
        self.assertIn("-o", flags)
        self.assertNotIn("--out", flags)

    def test_o_path_inside_session_dir(self):
        flags = _slicer_flags(self._params(), self.session_dir)
        idx = flags.index("-o")
        self.assertTrue(flags[idx + 1].startswith(str(self.session_dir)))

    def test_o_path_outside_toolchain(self):
        flags = _slicer_flags(self._params(), self.session_dir)
        idx = flags.index("-o")
        out_path = pathlib.Path(flags[idx + 1]).resolve()
        self.assertFalse(str(out_path).startswith(str(_TOOLCHAIN_ROOT)))

    def test_contains_base_dir_flag(self):
        flags = _slicer_flags(self._params(), self.session_dir)
        self.assertIn("--base-dir", flags)
        idx = flags.index("--base-dir")
        self.assertEqual(flags[idx + 1], "/some/repo")

    def test_contains_format_json(self):
        flags = _slicer_flags(self._params(), self.session_dir)
        self.assertIn("--format", flags)
        idx = flags.index("--format")
        self.assertEqual(flags[idx + 1], "json")

    def test_contains_deterministic_flag(self):
        flags = _slicer_flags(self._params(), self.session_dir)
        self.assertIn("--deterministic", flags)

    def test_raises_without_manifest_csv(self):
        with self.assertRaises(ValueError):
            _slicer_flags({"target_repo": "/repo"}, self.session_dir)

    def test_raises_without_target_repo(self):
        with self.assertRaises(ValueError):
            _slicer_flags({"manifest_csv": "/some/file_map.csv"}, self.session_dir)


if __name__ == "__main__":
    unittest.main()
