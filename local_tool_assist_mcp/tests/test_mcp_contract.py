"""Tests for local_tool_assist_mcp.mcp_server (LTA-4 contract layer).

Tests are structured so they do not require a live MCP client or LM Studio.
FastMCP schema tests use asyncio.run(mcp.list_tools()); dispatch tests call
_dispatch_* functions directly with fake-script registries for isolation.
"""

import asyncio
import pathlib
import tempfile
import unittest

from local_tool_assist_mcp.mcp_server import (
    APPROVED_TOOLS,
    _dispatch_archive_session_yaml,
    _dispatch_compile_handoff_report,
    _dispatch_create_session,
    _dispatch_read_report,
    _dispatch_runner_action,
    _is_subpath,
    _validate_read_path,
    mcp,
)
from local_tool_assist_mcp.session import TOOLCHAIN_ROOT
from local_tool_assist_mcp.tool_registry import (
    ToolEntry,
    _doctor_flags, _doctor_artifacts,
    _linter_flags, _linter_artifacts,
    _scan_flags, _scan_artifacts,
    _slicer_flags, _slicer_artifacts,
)

# ---------------------------------------------------------------------------
# Fake-script test registry (mirrors test_runner.py)
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

_RUNNER_ACTIONS = {"scan_directory", "validate_manifest", "lint_tool_command", "run_semantic_slice"}


def _make_test_registry() -> dict:
    return {
        "scan_directory": ToolEntry(
            action="scan_directory",
            script_name="fake_scanner.py",
            timeout_seconds=30,
            requires_review_approval=False,
            build_flags=_scan_flags,
            collect_artifacts=_scan_artifacts,
            primary_json_report_key="manifest_health_json",
        ),
        "validate_manifest": ToolEntry(
            action="validate_manifest",
            script_name="fake_doctor.py",
            timeout_seconds=30,
            requires_review_approval=False,
            build_flags=_doctor_flags,
            collect_artifacts=_doctor_artifacts,
            primary_json_report_key="manifest_doctor_json",
        ),
        "lint_tool_command": ToolEntry(
            action="lint_tool_command",
            script_name="fake_linter.py",
            timeout_seconds=30,
            requires_review_approval=False,
            build_flags=_linter_flags,
            collect_artifacts=_linter_artifacts,
            primary_json_report_key="command_lint_json",
        ),
        "run_semantic_slice": ToolEntry(
            action="run_semantic_slice",
            script_name="fake_slicer.py",
            timeout_seconds=30,
            requires_review_approval=True,
            build_flags=_slicer_flags,
            collect_artifacts=_slicer_artifacts,
            primary_json_report_key="slicer_json",
        ),
    }


# ---------------------------------------------------------------------------
# Helper: create a session dict + saved YAML in a temp dir
# ---------------------------------------------------------------------------

def _setup_session(tmp_root: pathlib.Path, slice_approved: bool = False) -> tuple:
    result = _dispatch_create_session(
        objective="MCP test",
        target_repo="/some/repo",
        requested_by="test",
        downstream_agent="builder",
        output_root=str(tmp_root),
    )
    session_id = result["session_id"]
    return session_id, result


# ---------------------------------------------------------------------------
# TestApprovedToolList
# ---------------------------------------------------------------------------

class TestApprovedToolList(unittest.TestCase):

    def test_approved_tools_is_frozenset(self):
        self.assertIsInstance(APPROVED_TOOLS, frozenset)

    def test_approved_tools_has_exactly_8(self):
        self.assertEqual(len(APPROVED_TOOLS), 8)

    def test_approved_tools_contains_all_expected(self):
        expected = {
            "create_session",
            "scan_directory",
            "validate_manifest",
            "lint_tool_command",
            "run_semantic_slice",
            "read_report",
            "compile_handoff_report",
            "archive_session_yaml",
        }
        self.assertEqual(APPROVED_TOOLS, expected)

    def test_mcp_server_registers_exactly_8_tools(self):
        tools = asyncio.run(mcp.list_tools())
        registered_names = {t.name for t in tools}
        self.assertEqual(registered_names, APPROVED_TOOLS)

    def test_no_raw_shell_tools(self):
        tools = asyncio.run(mcp.list_tools())
        forbidden = {"execute_command", "run_shell", "shell", "bash", "cmd",
                     "delete_file", "write_file", "move_file", "rm", "cp"}
        names = {t.name for t in tools}
        self.assertEqual(forbidden & names, set(), f"Forbidden tools exposed: {forbidden & names}")

    def test_no_arbitrary_file_write_tools(self):
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        for name in names:
            self.assertNotIn("write", name.lower().split("_"),
                             f"Tool {name!r} looks like a write tool")


# ---------------------------------------------------------------------------
# TestToolSchemas
# ---------------------------------------------------------------------------

class TestToolSchemas(unittest.TestCase):

    def setUp(self):
        self._tools = {t.name: t for t in asyncio.run(mcp.list_tools())}

    def test_each_tool_has_input_schema(self):
        for name, tool in self._tools.items():
            self.assertIsInstance(tool.inputSchema, dict,
                                  f"{name!r} missing inputSchema")

    def test_each_schema_is_object_type(self):
        for name, tool in self._tools.items():
            self.assertEqual(
                tool.inputSchema.get("type"), "object",
                f"{name!r} schema type is not 'object'",
            )

    def test_no_shell_command_field_in_any_tool(self):
        forbidden_fields = {"shell", "shell_command", "delete", "write_file",
                            "path_to_read_anywhere", "exec", "execute"}
        for name, tool in self._tools.items():
            props = tool.inputSchema.get("properties", {})
            overlap = forbidden_fields & set(props)
            self.assertEqual(overlap, set(),
                             f"Tool {name!r} exposes forbidden fields: {overlap}")

    def test_only_lint_tool_command_has_command_field(self):
        for name, tool in self._tools.items():
            props = tool.inputSchema.get("properties", {})
            if name == "lint_tool_command":
                self.assertIn("command", props,
                              "lint_tool_command must have 'command' field")
            else:
                self.assertNotIn("command", props,
                                 f"{name!r} must not expose 'command' field")

    def test_create_session_has_required_objective_and_target_repo(self):
        schema = self._tools["create_session"].inputSchema
        required = schema.get("required", [])
        self.assertIn("objective", required)
        self.assertIn("target_repo", required)

    def test_runner_tools_have_session_id_required(self):
        runner_tools = {"scan_directory", "validate_manifest",
                        "lint_tool_command", "run_semantic_slice"}
        for name in runner_tools:
            schema = self._tools[name].inputSchema
            required = schema.get("required", [])
            self.assertIn("session_id", required, f"{name!r} must require session_id")

    def test_read_report_has_session_id_required(self):
        schema = self._tools["read_report"].inputSchema
        self.assertIn("session_id", schema.get("required", []))

    def test_compile_and_archive_tools_have_session_id_required(self):
        for name in ("compile_handoff_report", "archive_session_yaml"):
            schema = self._tools[name].inputSchema
            self.assertIn("session_id", schema.get("required", []),
                          f"{name!r} must require session_id")


# ---------------------------------------------------------------------------
# TestDispatchCreateSession
# ---------------------------------------------------------------------------

class TestDispatchCreateSession(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_session_id(self):
        result = _dispatch_create_session("obj", "/repo", output_root=str(self._root))
        self.assertIn("session_id", result)
        self.assertTrue(result["session_id"].startswith("lta_"))

    def test_returns_status_created(self):
        result = _dispatch_create_session("obj", "/repo", output_root=str(self._root))
        self.assertEqual(result["status"], "created")

    def test_saves_yaml_to_disk(self):
        result = _dispatch_create_session("obj", "/repo", output_root=str(self._root))
        yp = pathlib.Path(result["yaml_path"])
        self.assertTrue(yp.exists())

    def test_yaml_is_valid_session(self):
        from local_tool_assist_mcp.session import load_session
        from local_tool_assist_mcp.schemas import validate_session
        result = _dispatch_create_session("obj", "/repo", output_root=str(self._root))
        sd = load_session(result["yaml_path"])
        valid, errors = validate_session(sd)
        self.assertTrue(valid, f"Saved session failed validation: {errors}")

    def test_session_dir_outside_toolchain(self):
        result = _dispatch_create_session("obj", "/repo", output_root=str(self._root))
        sd_path = pathlib.Path(result["session_dir"])
        self.assertFalse(_is_subpath(sd_path, TOOLCHAIN_ROOT))


# ---------------------------------------------------------------------------
# TestDispatchRunnerActions
# ---------------------------------------------------------------------------

class TestDispatchRunnerActions(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        create_result = _dispatch_create_session(
            "obj", "/repo", output_root=str(self._root)
        )
        self._session_id = create_result["session_id"]
        self._reg = _make_test_registry()
        self._fixtures = str(_FIXTURES_DIR)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_scan_directory_dispatches_through_runner(self):
        result = _dispatch_runner_action(
            "scan_directory",
            self._session_id,
            {"target_repo": "/fake/repo"},
            output_root=str(self._root),
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertIn("status", result)
        self.assertEqual(result["action"], "scan_directory")

    def test_scan_directory_returns_pass(self):
        result = _dispatch_runner_action(
            "scan_directory",
            self._session_id,
            {"target_repo": "/fake/repo"},
            output_root=str(self._root),
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertEqual(result["status"], "PASS")

    def test_run_semantic_slice_dispatches_through_runner(self):
        # Without approval → POLICY_BLOCK
        result = _dispatch_runner_action(
            "run_semantic_slice",
            self._session_id,
            {"manifest_csv": "/fake/file_map.csv", "target_repo": "/fake/repo"},
            output_root=str(self._root),
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertIn("status", result)
        self.assertEqual(result["action"], "run_semantic_slice")

    def test_policy_block_returned_unchanged_when_not_approved(self):
        result = _dispatch_runner_action(
            "run_semantic_slice",
            self._session_id,
            {"manifest_csv": "/fake/file_map.csv", "target_repo": "/fake/repo"},
            output_root=str(self._root),
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertEqual(result["status"], "POLICY_BLOCK")
        self.assertTrue(result["policy"]["blocked"])

    def test_policy_block_has_reason(self):
        result = _dispatch_runner_action(
            "run_semantic_slice",
            self._session_id,
            {"manifest_csv": "/fake/file_map.csv", "target_repo": "/fake/repo"},
            output_root=str(self._root),
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertTrue(result["policy"]["reason"])

    def test_slice_allowed_when_approved(self):
        from local_tool_assist_mcp.session import load_session, save_session
        yp = self._root / "sessions" / self._session_id / "session.yaml"
        sd = load_session(yp)
        sd["review_state"]["slice_approved"] = True
        sd.setdefault("artifacts", {})["manifest_csv"] = "/fake/file_map.csv"
        sd["artifacts"]["manifest_doctor_json"] = "/fake/manifest_doctor.json"
        sd.setdefault("latest", {})["manifest_doctor_status"] = "PASS"
        save_session(sd, yp)

        result = _dispatch_runner_action(
            "run_semantic_slice",
            self._session_id,
            {"manifest_csv": "/fake/file_map.csv", "target_repo": "/fake/repo"},
            output_root=str(self._root),
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertNotEqual(result["status"], "POLICY_BLOCK")

    def test_unknown_action_returns_error(self):
        result = _dispatch_runner_action(
            "nonexistent_action",
            self._session_id,
            {},
            output_root=str(self._root),
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertEqual(result["status"], "ERROR")

    def test_session_not_found_raises_value_error(self):
        with self.assertRaises(ValueError):
            _dispatch_runner_action(
                "scan_directory",
                "lta_bad_session_id",
                {"target_repo": "/repo"},
                output_root=str(self._root),
            )


# ---------------------------------------------------------------------------
# TestDispatchCompilerTools
# ---------------------------------------------------------------------------

class TestDispatchCompilerTools(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        create_result = _dispatch_create_session(
            "obj", "/repo", output_root=str(self._root)
        )
        self._session_id = create_result["session_id"]

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_compile_handoff_report_returns_status_compiled(self):
        result = _dispatch_compile_handoff_report(
            self._session_id, output_root=str(self._root)
        )
        self.assertEqual(result["status"], "compiled")

    def test_compile_handoff_report_produces_markdown_outside_toolchain(self):
        result = _dispatch_compile_handoff_report(
            self._session_id, output_root=str(self._root)
        )
        md_path = pathlib.Path(result["final_markdown"])
        self.assertTrue(md_path.exists())
        self.assertFalse(_is_subpath(md_path, TOOLCHAIN_ROOT))

    def test_compile_handoff_report_produces_bundle_outside_toolchain(self):
        result = _dispatch_compile_handoff_report(
            self._session_id, output_root=str(self._root)
        )
        bundle_path = pathlib.Path(result["final_python_bundle"])
        self.assertTrue(bundle_path.exists())
        self.assertFalse(_is_subpath(bundle_path, TOOLCHAIN_ROOT))

    def test_compile_handoff_report_updates_session_artifacts(self):
        from local_tool_assist_mcp.session import load_session
        _dispatch_compile_handoff_report(self._session_id, output_root=str(self._root))
        yp = self._root / "sessions" / self._session_id / "session.yaml"
        sd = load_session(yp)
        self.assertTrue(sd["artifacts"]["final_markdown"])
        self.assertTrue(sd["artifacts"]["final_python_bundle"])

    def test_archive_session_yaml_returns_status_archived(self):
        result = _dispatch_archive_session_yaml(
            self._session_id, output_root=str(self._root)
        )
        self.assertEqual(result["status"], "archived")

    def test_archive_session_yaml_creates_file_outside_toolchain(self):
        result = _dispatch_archive_session_yaml(
            self._session_id, output_root=str(self._root)
        )
        archive_path = pathlib.Path(result["archive_yaml"])
        self.assertTrue(archive_path.exists())
        self.assertFalse(_is_subpath(archive_path, TOOLCHAIN_ROOT))

    def test_archive_session_yaml_file_under_archive_dir(self):
        result = _dispatch_archive_session_yaml(
            self._session_id, output_root=str(self._root)
        )
        archive_path = pathlib.Path(result["archive_yaml"])
        self.assertEqual(archive_path.parent, self._root / "archive")


# ---------------------------------------------------------------------------
# TestReadReportSafety
# ---------------------------------------------------------------------------

class TestReadReportSafety(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        create_result = _dispatch_create_session(
            "obj", "/repo", output_root=str(self._root)
        )
        self._session_id = create_result["session_id"]
        # Write known files inside session-owned directory for valid-read tests
        reports_dir = self._root / "sessions" / self._session_id / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        self._test_file = reports_dir / "test_output.txt"
        self._test_file.write_text("hello world\nline2", encoding="utf-8")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_rejects_absolute_path_outside_output_root(self):
        from local_tool_assist_mcp.policy import PolicyError
        with self.assertRaises((ValueError, PolicyError)):
            _dispatch_read_report(
                self._session_id,
                relative_path="C:/Windows/System32/drivers/etc/hosts",
                output_root=str(self._root),
            )

    def test_rejects_dotdot_traversal(self):
        with self.assertRaises(ValueError):
            _dispatch_read_report(
                self._session_id,
                relative_path="../../../etc/passwd",
                output_root=str(self._root),
            )

    def test_rejects_path_inside_toolchain(self):
        from local_tool_assist_mcp.policy import PolicyError
        with self.assertRaises(PolicyError):
            _validate_read_path(TOOLCHAIN_ROOT / "some_file.py", self._root)

    def test_rejects_path_outside_output_root(self):
        outside = pathlib.Path(tempfile.gettempdir()) / "outside.txt"
        outside.write_text("x")
        try:
            from local_tool_assist_mcp.policy import PolicyError
            with self.assertRaises(PolicyError):
                _validate_read_path(outside, self._root)
        finally:
            outside.unlink(missing_ok=True)

    def test_reads_valid_relative_path(self):
        result = _dispatch_read_report(
            self._session_id,
            relative_path=f"sessions/{self._session_id}/reports/test_output.txt",
            output_root=str(self._root),
        )
        self.assertIn("content", result)
        self.assertIn("hello world", result["content"])

    def test_max_chars_truncates_content(self):
        result = _dispatch_read_report(
            self._session_id,
            relative_path=f"sessions/{self._session_id}/reports/test_output.txt",
            max_chars=5,
            output_root=str(self._root),
        )
        self.assertEqual(len(result["content"]), 5)
        self.assertTrue(result["truncated"])

    def test_redacts_secrets_in_content(self):
        sensitive_file = self._root / "sessions" / self._session_id / "reports" / "sensitive.txt"
        sensitive_file.write_text("MY_API_KEY=sk-secret123\nother line", encoding="utf-8")
        result = _dispatch_read_report(
            self._session_id,
            relative_path=f"sessions/{self._session_id}/reports/sensitive.txt",
            output_root=str(self._root),
        )
        # Should not contain raw secret
        self.assertNotIn("sk-secret123", result["content"])

    def test_empty_artifact_key_returns_error(self):
        result = _dispatch_read_report(
            self._session_id,
            artifact_key="slicer_json",  # not produced, empty in session
            output_root=str(self._root),
        )
        self.assertIn("error", result)

    def test_no_artifact_or_path_returns_error(self):
        result = _dispatch_read_report(
            self._session_id,
            output_root=str(self._root),
        )
        self.assertIn("error", result)

    def test_does_not_require_live_mcp_client(self):
        # This test is its own proof — it runs without any MCP transport
        result = _dispatch_read_report(
            self._session_id,
            relative_path=f"sessions/{self._session_id}/reports/test_output.txt",
            output_root=str(self._root),
        )
        self.assertIn("content", result)


# ---------------------------------------------------------------------------
# TestNoLiveMCPRequired
# ---------------------------------------------------------------------------

class TestNoLiveMCPRequired(unittest.TestCase):
    """Confirm all tests pass without a live MCP client or LM Studio."""

    def test_mcp_server_importable(self):
        import local_tool_assist_mcp.mcp_server as srv
        self.assertTrue(hasattr(srv, "APPROVED_TOOLS"))

    def test_dispatch_functions_callable_without_client(self):
        # Merely calling the dispatch function (with a temp session) proves no
        # live client is needed.
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            result = _dispatch_create_session("test", "/repo", output_root=str(root))
            self.assertEqual(result["status"], "created")

    def test_mcp_module_has_mcp_instance(self):
        import local_tool_assist_mcp.mcp_server as srv
        self.assertTrue(hasattr(srv, "mcp"))


if __name__ == "__main__":
    unittest.main()
