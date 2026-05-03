"""Tests for local_tool_assist_mcp.runner."""

import os
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from local_tool_assist_mcp import runner as run_mod
from local_tool_assist_mcp.runner import (
    _assert_outputs_outside_toolchain,
    _build_safe_env,
    _make_result,
    _map_status,
    _tail,
    run_action,
)
from local_tool_assist_mcp.session import TOOLCHAIN_ROOT, create_session
from local_tool_assist_mcp.tool_registry import (
    REGISTRY,
    ToolEntry,
    _doctor_artifacts,
    _doctor_flags,
    _linter_artifacts,
    _linter_flags,
    _scan_artifacts,
    _scan_flags,
    _slicer_artifacts,
    _slicer_flags,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"

_REQUIRED_RESULT_KEYS = (
    "action", "status", "returncode",
    "started_at", "ended_at",
    "stdout_tail", "stderr_tail",
    "artifacts", "policy",
)


# ---------------------------------------------------------------------------
# Test registry (fake scripts, same flag-builders as production)
# ---------------------------------------------------------------------------

def _make_test_registry() -> dict:
    return {
        "scan_directory": ToolEntry(
            action="scan_directory",
            script_name="fake_scanner.py",
            timeout_seconds=15,
            requires_review_approval=False,
            build_flags=_scan_flags,
            collect_artifacts=_scan_artifacts,
            primary_json_report_key="manifest_health_json",
        ),
        "validate_manifest": ToolEntry(
            action="validate_manifest",
            script_name="fake_doctor.py",
            timeout_seconds=15,
            requires_review_approval=False,
            build_flags=_doctor_flags,
            collect_artifacts=_doctor_artifacts,
            primary_json_report_key="manifest_doctor_json",
        ),
        "lint_tool_command": ToolEntry(
            action="lint_tool_command",
            script_name="fake_linter.py",
            timeout_seconds=15,
            requires_review_approval=False,
            build_flags=_linter_flags,
            collect_artifacts=_linter_artifacts,
            primary_json_report_key="command_lint_json",
        ),
        "run_semantic_slice": ToolEntry(
            action="run_semantic_slice",
            script_name="fake_slicer.py",
            timeout_seconds=15,
            requires_review_approval=True,
            build_flags=_slicer_flags,
            collect_artifacts=_slicer_artifacts,
            primary_json_report_key="slicer_json",
        ),
    }


# ---------------------------------------------------------------------------
# _tail
# ---------------------------------------------------------------------------

class TestTail(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(_tail(""), "")

    def test_returns_last_n_lines(self):
        text = "\n".join(str(i) for i in range(100))
        result = _tail(text, n=10)
        lines = result.splitlines()
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[-1], "99")

    def test_short_text_returned_in_full(self):
        text = "line1\nline2\nline3"
        self.assertEqual(_tail(text, n=50), text)


# ---------------------------------------------------------------------------
# _build_safe_env
# ---------------------------------------------------------------------------

class TestBuildSafeEnv(unittest.TestCase):

    def _with_extra(self, extras: dict):
        prev = {k: os.environ.get(k) for k in extras}
        for k, v in extras.items():
            os.environ[k] = v
        try:
            env = _build_safe_env()
        finally:
            for k, old in prev.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old
        return env

    def test_strips_api_key(self):
        env = self._with_extra({"OPENAI_API_KEY": "sk-test-key"})
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_strips_token(self):
        env = self._with_extra({"LTA_MCP_AUTH_TOKEN": "tok-abc"})
        self.assertNotIn("LTA_MCP_AUTH_TOKEN", env)

    def test_strips_secret(self):
        env = self._with_extra({"DB_PASSWORD_SECRET": "s3cr3t"})
        self.assertNotIn("DB_PASSWORD_SECRET", env)

    def test_strips_anthropic_api_key(self):
        env = self._with_extra({"ANTHROPIC_API_KEY": "anth-key"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_preserves_other_vars(self):
        env = self._with_extra({"LTA_DEV_MODE": "1"})
        self.assertIn("LTA_DEV_MODE", env)

    def test_preserves_path(self):
        env = _build_safe_env()
        self.assertIn("PATH", env)

    def test_does_not_mutate_os_environ(self):
        before = set(os.environ.keys())
        _build_safe_env()
        after = set(os.environ.keys())
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# _map_status
# ---------------------------------------------------------------------------

class TestMapStatus(unittest.TestCase):

    def test_returncode_1_is_error(self):
        self.assertEqual(_map_status(1, None), "ERROR")

    def test_returncode_0_no_report_is_pass(self):
        self.assertEqual(_map_status(0, None), "PASS")

    def test_returncode_2_no_report_is_block(self):
        self.assertEqual(_map_status(2, None), "BLOCK")

    def test_reads_status_from_json_report(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            import json
            json.dump({"status": "WARN"}, fh)
            path = pathlib.Path(fh.name)
        try:
            self.assertEqual(_map_status(0, path), "WARN")
        finally:
            path.unlink(missing_ok=True)

    def test_reads_block_from_json_report(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            import json
            json.dump({"status": "BLOCK"}, fh)
            path = pathlib.Path(fh.name)
        try:
            self.assertEqual(_map_status(2, path), "BLOCK")
        finally:
            path.unlink(missing_ok=True)

    def test_report_error_key_overrides_returncode(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            import json
            json.dump({"status": "BLOCK", "error": "schema parse error"}, fh)
            path = pathlib.Path(fh.name)
        try:
            self.assertEqual(_map_status(2, path), "ERROR")
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# _assert_outputs_outside_toolchain
# ---------------------------------------------------------------------------

class TestAssertOutputsBoundary(unittest.TestCase):

    def test_raises_if_inside_toolchain(self):
        bad_dir = TOOLCHAIN_ROOT / "sessions" / "lta_test"
        with self.assertRaises(RuntimeError):
            _assert_outputs_outside_toolchain(bad_dir)

    def test_passes_for_temp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            _assert_outputs_outside_toolchain(pathlib.Path(d))  # should not raise


# ---------------------------------------------------------------------------
# result shape
# ---------------------------------------------------------------------------

class TestResultShape(unittest.TestCase):

    def test_make_result_has_all_required_keys(self):
        r = _make_result("scan_directory", "PASS", 0, "t0", "t1")
        for k in _REQUIRED_RESULT_KEYS:
            self.assertIn(k, r, f"Missing key: {k!r}")

    def test_make_result_policy_has_blocked_and_reason(self):
        r = _make_result("scan_directory", "PASS", 0, "t0", "t1")
        self.assertIn("blocked", r["policy"])
        self.assertIn("reason", r["policy"])


# ---------------------------------------------------------------------------
# run_action — subprocess behaviour (mocked)
# ---------------------------------------------------------------------------

class TestRunActionSubprocessBehaviour(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)
        self.session_dict, self.session_dir = create_session(
            "test", "/repo", output_root=self.output_root
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def _mock_proc(self, returncode=0):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = ""
        proc.stderr = ""
        return proc

    def test_uses_shell_false(self):
        with patch("local_tool_assist_mcp.runner.subprocess.run", return_value=self._mock_proc()) as m:
            run_action(
                "scan_directory",
                {"target_repo": "/repo"},
                self.session_dict,
                self.session_dir,
            )
        _, call_kwargs = m.call_args
        self.assertFalse(call_kwargs.get("shell", True))

    def test_uses_toolchain_cwd_by_default(self):
        with patch("local_tool_assist_mcp.runner.subprocess.run", return_value=self._mock_proc()) as m:
            run_action(
                "scan_directory",
                {"target_repo": "/repo"},
                self.session_dict,
                self.session_dir,
            )
        _, call_kwargs = m.call_args
        self.assertEqual(call_kwargs.get("cwd"), str(TOOLCHAIN_ROOT))

    def test_toolchain_root_override_used_as_cwd(self):
        with patch("local_tool_assist_mcp.runner.subprocess.run", return_value=self._mock_proc()) as m:
            run_action(
                "scan_directory",
                {"target_repo": "/repo"},
                self.session_dict,
                self.session_dir,
                toolchain_root=str(_FIXTURES),
            )
        _, call_kwargs = m.call_args
        self.assertEqual(call_kwargs.get("cwd"), str(_FIXTURES))

    def test_capture_output_is_true(self):
        with patch("local_tool_assist_mcp.runner.subprocess.run", return_value=self._mock_proc()) as m:
            run_action(
                "scan_directory",
                {"target_repo": "/repo"},
                self.session_dict,
                self.session_dir,
            )
        _, call_kwargs = m.call_args
        self.assertTrue(call_kwargs.get("capture_output", False))


# ---------------------------------------------------------------------------
# run_action — unknown action
# ---------------------------------------------------------------------------

class TestRunActionUnknownAction(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)
        self.session_dict, self.session_dir = create_session(
            "test", "/repo", output_root=self.output_root
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_error_status(self):
        result = run_action("rm_rf_slash", {}, self.session_dict, self.session_dir)
        self.assertEqual(result["status"], "ERROR")

    def test_returns_returncode_minus_one(self):
        result = run_action("rm_rf_slash", {}, self.session_dict, self.session_dir)
        self.assertEqual(result["returncode"], -1)

    def test_step_appended_for_unknown_action(self):
        before = len(self.session_dict["steps"])
        run_action("unknown", {}, self.session_dict, self.session_dir)
        self.assertEqual(len(self.session_dict["steps"]), before + 1)

    def test_result_has_all_required_keys(self):
        result = run_action("unknown", {}, self.session_dict, self.session_dir)
        for k in _REQUIRED_RESULT_KEYS:
            self.assertIn(k, result)


# ---------------------------------------------------------------------------
# run_action — review gate
# ---------------------------------------------------------------------------

class TestReviewGate(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)
        self.session_dict, self.session_dir = create_session(
            "slice test", "/repo", output_root=self.output_root
        )
        self._test_registry = _make_test_registry()
        self.session_dict["artifacts"]["manifest_doctor_json"] = str(self.session_dir / "intermediate" / "manifest_doctor.json")
        self.session_dict.setdefault("latest", {})["manifest_doctor_status"] = "PASS"

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("LTA_DEV_MODE", None)

    def _params(self):
        return {
            "manifest_csv": str(self.session_dir / "intermediate" / "file_map.csv"),
            "target_repo": "/fake/repo",
        }

    def test_blocks_slice_without_approval(self):
        result = run_action(
            "run_semantic_slice",
            self._params(),
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertEqual(result["status"], "POLICY_BLOCK")
        self.assertTrue(result["policy"]["blocked"])

    def test_block_result_has_reason(self):
        result = run_action(
            "run_semantic_slice",
            self._params(),
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertIn("REVIEW_APPROVAL_REQUIRED", result["policy"]["reason"])

    def test_allows_slice_when_approved(self):
        self.session_dict["review_state"]["slice_approved"] = True
        result = run_action(
            "run_semantic_slice",
            self._params(),
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertNotEqual(result["status"], "POLICY_BLOCK")

    def test_dev_mode_bypasses_gate(self):
        os.environ["LTA_DEV_MODE"] = "1"
        result = run_action(
            "run_semantic_slice",
            self._params(),
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertNotEqual(result["status"], "POLICY_BLOCK")

    def test_gate_check_is_in_runner_not_only_mcp(self):
        # Calling run_action directly (not through MCP server) must still enforce gate
        self.assertFalse(self.session_dict["review_state"]["slice_approved"])
        result = run_action(
            "run_semantic_slice",
            self._params(),
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertEqual(result["status"], "POLICY_BLOCK")


# ---------------------------------------------------------------------------
# run_action — integration with fake scripts
# ---------------------------------------------------------------------------

class TestRunActionIntegration(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)
        self.session_dict, self.session_dir = create_session(
            "integration test", "/fake/repo", output_root=self.output_root
        )
        self._test_registry = _make_test_registry()
        self.session_dict["artifacts"]["manifest_doctor_json"] = str(self.session_dir / "intermediate" / "manifest_doctor.json")
        self.session_dict.setdefault("latest", {})["manifest_doctor_status"] = "PASS"
        os.environ.pop("FAKE_SCANNER_EXIT", None)
        os.environ.pop("FAKE_DOCTOR_EXIT", None)
        os.environ.pop("FAKE_DOCTOR_ERROR", None)
        os.environ.pop("FAKE_LINTER_EXIT", None)
        os.environ.pop("LTA_DEV_MODE", None)

    def tearDown(self):
        self._tmpdir.cleanup()
        for k in ("FAKE_SCANNER_EXIT", "FAKE_DOCTOR_EXIT", "FAKE_DOCTOR_ERROR",
                  "FAKE_LINTER_EXIT", "LTA_DEV_MODE"):
            os.environ.pop(k, None)

    def test_scan_directory_returns_pass(self):
        result = run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertEqual(result["status"], "PASS")

    def test_scan_directory_produces_manifest_csv(self):
        result = run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        csv_path = result["artifacts"].get("manifest_csv", "")
        self.assertTrue(csv_path, "manifest_csv artifact is empty")
        self.assertTrue(pathlib.Path(csv_path).exists(), f"CSV not found at {csv_path}")

    def test_scan_directory_produces_health_report(self):
        result = run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        hp_path = result["artifacts"].get("manifest_health_json", "")
        self.assertTrue(hp_path)
        self.assertTrue(pathlib.Path(hp_path).exists())

    def test_validate_manifest_returns_pass(self):
        # Create a dummy CSV so the fake doctor can run
        csv_path = self.session_dir / "intermediate" / "file_map.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text("abs_path,rel_path\n/fake/main.py,main.py\n")

        result = run_action(
            "validate_manifest",
            {"manifest_csv": str(csv_path)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertEqual(result["status"], "PASS")

    def test_validate_manifest_produces_json_report(self):
        csv_path = self.session_dir / "intermediate" / "file_map.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text("abs_path,rel_path\n/fake/main.py,main.py\n")

        result = run_action(
            "validate_manifest",
            {"manifest_csv": str(csv_path)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        json_path = result["artifacts"].get("manifest_doctor_json", "")
        self.assertTrue(json_path)
        self.assertTrue(pathlib.Path(json_path).exists())

    def test_validate_manifest_block_status_on_exit_2(self):
        csv_path = self.session_dir / "intermediate" / "file_map.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text("abs_path,rel_path\n/fake/main.py,main.py\n")
        os.environ["FAKE_DOCTOR_EXIT"] = "2"

        result = run_action(
            "validate_manifest",
            {"manifest_csv": str(csv_path)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertEqual(result["status"], "BLOCK")

    def test_lint_tool_command_returns_pass(self):
        result = run_action(
            "lint_tool_command",
            {"command": "python create_file_map_v3.py . --out /tmp/out.csv"},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertEqual(result["status"], "PASS")

    def test_lint_tool_command_produces_json_report(self):
        result = run_action(
            "lint_tool_command",
            {"command": "python foo.py"},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        json_path = result["artifacts"].get("command_lint_json", "")
        self.assertTrue(json_path)
        self.assertTrue(pathlib.Path(json_path).exists())

    def test_slice_with_approval_produces_output(self):
        self.session_dict["review_state"]["slice_approved"] = True
        csv_path = self.session_dir / "intermediate" / "file_map.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text("abs_path,rel_path\n/fake/main.py,main.py\n")

        result = run_action(
            "run_semantic_slice",
            {"manifest_csv": str(csv_path), "target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertNotEqual(result["status"], "POLICY_BLOCK")
        slicer_path = result["artifacts"].get("slicer_json", "")
        self.assertTrue(slicer_path)
        self.assertTrue(pathlib.Path(slicer_path).exists())


# ---------------------------------------------------------------------------
# run_action — session mutation
# ---------------------------------------------------------------------------

class TestRunActionSessionMutation(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output_root = pathlib.Path(self._tmpdir.name)
        self.session_dict, self.session_dir = create_session(
            "mutation test", "/repo", output_root=self.output_root
        )
        self._test_registry = _make_test_registry()
        self.session_dict["artifacts"]["manifest_doctor_json"] = str(self.session_dir / "intermediate" / "manifest_doctor.json")
        self.session_dict.setdefault("latest", {})["manifest_doctor_status"] = "PASS"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_step_appended_after_successful_run(self):
        before = len(self.session_dict["steps"])
        run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertEqual(len(self.session_dict["steps"]), before + 1)

    def test_step_contains_action_name(self):
        run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        last_step = self.session_dict["steps"][-1]
        self.assertEqual(last_step["action"], "scan_directory")

    def test_step_contains_status(self):
        run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        last_step = self.session_dict["steps"][-1]
        self.assertIn("status", last_step)

    def test_session_artifact_updated_after_scan(self):
        run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        self.assertTrue(self.session_dict["artifacts"]["manifest_csv"])

    def test_artifacts_outside_toolchain(self):
        run_action(
            "scan_directory",
            {"target_repo": str(self.session_dir)},
            self.session_dict,
            self.session_dir,
            toolchain_root=str(_FIXTURES),
            registry=self._test_registry,
        )
        for key, val in self.session_dict["artifacts"].items():
            if val:
                p = pathlib.Path(val).resolve()
                self.assertFalse(
                    str(p).startswith(str(TOOLCHAIN_ROOT.resolve())),
                    f"Artifact {key!r} is inside TOOLCHAIN_ROOT: {val}",
                )


if __name__ == "__main__":
    unittest.main()
