"""Tests for local_tool_assist_mcp.adapters.openai_responses_adapter.

All tests run without a live OpenAI API call or installed openai package
requirement (though openai is present in this environment).  Dispatch tests
use mocks for the underlying wrapper functions and temp dirs for integration
paths.
"""

import inspect
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from local_tool_assist_mcp.adapters.openai_responses_adapter import (
    OpenAIResponsesAdapter,
    _TOOL_SCHEMAS,
    dispatch_openai_tool_call,
    get_openai_tool_schemas,
    run_openai_tool_loop,
)
from local_tool_assist_mcp.mcp_server import APPROVED_TOOLS
from local_tool_assist_mcp.session import TOOLCHAIN_ROOT
from local_tool_assist_mcp.tool_registry import (
    ToolEntry,
    _doctor_artifacts, _doctor_flags,
    _linter_artifacts, _linter_flags,
    _scan_artifacts, _scan_flags,
    _slicer_artifacts, _slicer_flags,
)

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
_TOOLSET_ROOT = pathlib.Path(__file__).parent.parent.parent


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


def _create_real_session(tmp_root: pathlib.Path) -> str:
    """Create a session via dispatch and return session_id."""
    from local_tool_assist_mcp.mcp_server import _dispatch_create_session
    result = _dispatch_create_session(
        "test objective", "/repo", output_root=str(tmp_root)
    )
    return result["session_id"]


# ---------------------------------------------------------------------------
# TestModuleImportability
# ---------------------------------------------------------------------------

class TestModuleImportability(unittest.TestCase):

    def test_adapter_module_importable(self):
        import local_tool_assist_mcp.adapters.openai_responses_adapter as m
        self.assertTrue(hasattr(m, "get_openai_tool_schemas"))

    def test_base_module_importable(self):
        from local_tool_assist_mcp.adapters.base import ProviderAdapter
        self.assertTrue(inspect.isabstract(ProviderAdapter))

    def test_adapters_package_importable(self):
        import local_tool_assist_mcp.adapters
        self.assertTrue(hasattr(local_tool_assist_mcp.adapters, "__init__"))

    def test_openai_not_required_for_schema_functions(self):
        # get_openai_tool_schemas must work without calling openai
        schemas = get_openai_tool_schemas()
        self.assertIsInstance(schemas, list)

    def test_openai_not_required_for_dispatch(self):
        # dispatch_openai_tool_call must work for unknown-tool rejection
        # without requiring an openai import
        result = dispatch_openai_tool_call("nonexistent", {})
        self.assertEqual(result["status"], "ERROR")

    def test_run_openai_tool_loop_importable(self):
        self.assertTrue(callable(run_openai_tool_loop))

    def test_run_openai_tool_loop_has_expected_params(self):
        sig = inspect.signature(run_openai_tool_loop)
        params = sig.parameters
        self.assertIn("objective", params)
        self.assertIn("target_repo", params)
        self.assertIn("model", params)
        self.assertIn("output_root", params)
        self.assertIn("max_turns", params)

    def test_run_openai_tool_loop_raises_without_api_key(self):
        saved = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises((ImportError, RuntimeError)):
                run_openai_tool_loop("obj", "/repo")
        finally:
            if saved is not None:
                os.environ["OPENAI_API_KEY"] = saved

    def test_openai_responses_adapter_class_importable(self):
        self.assertTrue(issubclass(OpenAIResponsesAdapter, object))

    def test_openai_responses_adapter_is_provider_adapter(self):
        from local_tool_assist_mcp.adapters.base import ProviderAdapter
        self.assertTrue(issubclass(OpenAIResponsesAdapter, ProviderAdapter))


# ---------------------------------------------------------------------------
# TestToolSchemas
# ---------------------------------------------------------------------------

class TestToolSchemas(unittest.TestCase):

    def setUp(self):
        self._schemas = get_openai_tool_schemas()
        self._by_name = {s["name"]: s for s in self._schemas}

    def test_schema_list_contains_exactly_8(self):
        self.assertEqual(len(self._schemas), 8)

    def test_schema_names_match_approved_tools(self):
        self.assertEqual(set(self._by_name.keys()), APPROVED_TOOLS)

    def test_each_schema_is_json_serialisable(self):
        for schema in self._schemas:
            serialised = json.dumps(schema)
            self.assertIsInstance(serialised, str)

    def test_each_schema_has_type_function(self):
        for schema in self._schemas:
            self.assertEqual(schema.get("type"), "function",
                             f"{schema.get('name')!r} missing type=function")

    def test_each_schema_has_name(self):
        for schema in self._schemas:
            self.assertTrue(schema.get("name"), f"Schema missing name: {schema}")

    def test_each_schema_has_description(self):
        for schema in self._schemas:
            self.assertTrue(schema.get("description"),
                            f"{schema.get('name')!r} missing description")

    def test_each_schema_has_parameters_object(self):
        for schema in self._schemas:
            params = schema.get("parameters", {})
            self.assertEqual(params.get("type"), "object",
                             f"{schema.get('name')!r} parameters.type is not 'object'")

    def test_no_schema_exposes_forbidden_fields(self):
        forbidden = {"shell", "shell_command", "delete", "write_file",
                     "path_to_read_anywhere", "exec", "execute"}
        for schema in self._schemas:
            props = schema.get("parameters", {}).get("properties", {})
            overlap = forbidden & set(props)
            self.assertEqual(overlap, set(),
                             f"{schema.get('name')!r} exposes forbidden fields: {overlap}")

    def test_only_lint_tool_command_has_command_field(self):
        for schema in self._schemas:
            props = schema.get("parameters", {}).get("properties", {})
            if schema["name"] == "lint_tool_command":
                self.assertIn("command", props)
            else:
                self.assertNotIn("command", props,
                                 f"{schema['name']!r} must not have 'command' field")

    def test_create_session_requires_objective_and_target_repo(self):
        req = self._by_name["create_session"]["parameters"]["required"]
        self.assertIn("objective", req)
        self.assertIn("target_repo", req)

    def test_runner_tools_require_session_id(self):
        for name in ("scan_directory", "validate_manifest",
                     "lint_tool_command", "run_semantic_slice"):
            req = self._by_name[name]["parameters"]["required"]
            self.assertIn("session_id", req, f"{name!r} must require session_id")

    def test_get_openai_tool_schemas_returns_copy(self):
        a = get_openai_tool_schemas()
        b = get_openai_tool_schemas()
        self.assertIsNot(a, b)


# ---------------------------------------------------------------------------
# TestDispatchUnknownAndInvalidInput
# ---------------------------------------------------------------------------

class TestDispatchUnknownAndInvalidInput(unittest.TestCase):

    def test_rejects_unknown_tool_name(self):
        result = dispatch_openai_tool_call("nonexistent_tool", {})
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("error", result)

    def test_rejects_shell_looking_name(self):
        result = dispatch_openai_tool_call("rm -rf /", {})
        self.assertEqual(result["status"], "ERROR")

    def test_accepts_dict_arguments(self):
        result = dispatch_openai_tool_call("nonexistent_tool", {"key": "value"})
        self.assertEqual(result["status"], "ERROR")

    def test_accepts_json_string_arguments(self):
        result = dispatch_openai_tool_call(
            "nonexistent_tool", '{"key": "value"}'
        )
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("Unknown tool", result["error"])

    def test_invalid_json_string_returns_error(self):
        result = dispatch_openai_tool_call("create_session", "{invalid json}")
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("Invalid JSON", result["error"])

    def test_non_string_non_dict_arguments_returns_error(self):
        result = dispatch_openai_tool_call("create_session", 42)
        self.assertEqual(result["status"], "ERROR")


# ---------------------------------------------------------------------------
# TestDispatchCreateSession
# ---------------------------------------------------------------------------

class TestDispatchCreateSession(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_create_session_via_dict_args(self):
        result = dispatch_openai_tool_call(
            "create_session",
            {"objective": "test", "target_repo": "/repo",
             "output_root": str(self._root)},
        )
        self.assertIn("session_id", result)
        self.assertEqual(result["status"], "created")

    def test_create_session_via_json_string(self):
        result = dispatch_openai_tool_call(
            "create_session",
            json.dumps({"objective": "test", "target_repo": "/repo",
                        "output_root": str(self._root)}),
        )
        self.assertEqual(result["status"], "created")

    def test_create_session_output_root_kwarg(self):
        result = dispatch_openai_tool_call(
            "create_session",
            {"objective": "test", "target_repo": "/repo"},
            output_root=str(self._root),
        )
        self.assertEqual(result["status"], "created")

    def test_adapter_class_execute_tool_create_session(self):
        adapter = OpenAIResponsesAdapter(output_root=str(self._root))
        result = adapter.execute_tool(
            "create_session",
            {"objective": "test", "target_repo": "/repo"},
        )
        self.assertEqual(result["status"], "created")


# ---------------------------------------------------------------------------
# TestDispatchRunnerActions
# ---------------------------------------------------------------------------

class TestDispatchRunnerActions(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = pathlib.Path(self._tmpdir.name)
        self._session_id = _create_real_session(self._root)
        self._reg = _make_test_registry()
        self._fixtures = str(_FIXTURES_DIR)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_scan_directory_routes_through_wrapper_runner(self):
        result = dispatch_openai_tool_call(
            "scan_directory",
            {"session_id": self._session_id, "target_repo": "/fake/repo",
             "output_root": str(self._root)},
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertIn("action", result)
        self.assertEqual(result["action"], "scan_directory")

    def test_scan_directory_returns_pass(self):
        result = dispatch_openai_tool_call(
            "scan_directory",
            {"session_id": self._session_id, "target_repo": "/fake/repo",
             "output_root": str(self._root)},
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertEqual(result["status"], "PASS")

    def test_run_semantic_slice_policy_block_preserved(self):
        result = dispatch_openai_tool_call(
            "run_semantic_slice",
            {"session_id": self._session_id,
             "manifest_csv": "/fake/file_map.csv",
             "target_repo": "/fake/repo",
             "output_root": str(self._root)},
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertEqual(result["status"], "POLICY_BLOCK")
        self.assertTrue(result["policy"]["blocked"])

    def test_policy_block_has_reason(self):
        result = dispatch_openai_tool_call(
            "run_semantic_slice",
            {"session_id": self._session_id,
             "manifest_csv": "/fake/file_map.csv",
             "target_repo": "/fake/repo",
             "output_root": str(self._root)},
            _registry=self._reg,
            _toolchain_root=self._fixtures,
        )
        self.assertTrue(result["policy"]["reason"])

    def test_dispatch_does_not_call_aletheia_scripts_directly(self):
        # Verify dispatch goes through _dispatch_runner_action (not subprocess directly)
        with patch(
            "local_tool_assist_mcp.adapters.openai_responses_adapter._dispatch_runner_action"
        ) as mock_dispatch:
            mock_dispatch.return_value = {"status": "PASS", "action": "scan_directory"}
            dispatch_openai_tool_call(
                "scan_directory",
                {"session_id": self._session_id, "target_repo": "/repo",
                 "output_root": str(self._root)},
            )
            mock_dispatch.assert_called_once()
            call_args = mock_dispatch.call_args
            # First positional arg is the action name
            self.assertEqual(call_args[0][0], "scan_directory")


# ---------------------------------------------------------------------------
# TestDispatchMocked (no real sessions needed)
# ---------------------------------------------------------------------------

class TestDispatchMocked(unittest.TestCase):
    """Pure mock tests — verify routing without real session files."""

    def _patch_dispatch(self, target, return_value):
        return patch(
            f"local_tool_assist_mcp.adapters.openai_responses_adapter.{target}",
            return_value=return_value,
        )

    def test_create_session_calls_dispatch_create_session(self):
        with self._patch_dispatch(
            "_dispatch_create_session",
            {"session_id": "lta_test", "status": "created"},
        ) as mock_fn:
            result = dispatch_openai_tool_call(
                "create_session",
                {"objective": "x", "target_repo": "/repo"},
            )
            mock_fn.assert_called_once()
            self.assertEqual(result["status"], "created")

    def test_compile_handoff_report_calls_dispatch_compile(self):
        with self._patch_dispatch(
            "_dispatch_compile_handoff_report",
            {"status": "compiled", "final_markdown": "/x.md", "final_python_bundle": "/x.py"},
        ) as mock_fn:
            result = dispatch_openai_tool_call(
                "compile_handoff_report",
                {"session_id": "lta_test"},
            )
            mock_fn.assert_called_once()
            self.assertEqual(result["status"], "compiled")

    def test_archive_session_yaml_calls_dispatch_archive(self):
        with self._patch_dispatch(
            "_dispatch_archive_session_yaml",
            {"status": "archived", "archive_yaml": "/x.yaml"},
        ) as mock_fn:
            result = dispatch_openai_tool_call(
                "archive_session_yaml",
                {"session_id": "lta_test"},
            )
            mock_fn.assert_called_once()
            self.assertEqual(result["status"], "archived")

    def test_read_report_calls_dispatch_read_report(self):
        with self._patch_dispatch(
            "_dispatch_read_report",
            {"content": "hello", "truncated": False, "char_count": 5},
        ) as mock_fn:
            result = dispatch_openai_tool_call(
                "read_report",
                {"session_id": "lta_test", "artifact_key": "manifest_csv"},
            )
            mock_fn.assert_called_once()
            self.assertIn("content", result)

    def test_no_live_openai_api_calls_in_dispatch(self):
        # dispatch_openai_tool_call must never import or call OpenAI client
        with patch("openai.OpenAI") as mock_client:
            dispatch_openai_tool_call("nonexistent_tool", {})
            mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# TestAdapterClass
# ---------------------------------------------------------------------------

class TestAdapterClass(unittest.TestCase):

    def test_list_tools_returns_8(self):
        adapter = OpenAIResponsesAdapter()
        tools = adapter.list_tools()
        self.assertEqual(len(tools), 8)

    def test_list_tools_names_match_approved(self):
        adapter = OpenAIResponsesAdapter()
        names = {t["name"] for t in adapter.list_tools()}
        self.assertEqual(names, APPROVED_TOOLS)

    def test_execute_tool_unknown_returns_error(self):
        adapter = OpenAIResponsesAdapter()
        result = adapter.execute_tool("unknown_tool", {})
        self.assertEqual(result["status"], "ERROR")

    def test_adapter_output_root_passed_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = OpenAIResponsesAdapter(output_root=tmp)
            with patch(
                "local_tool_assist_mcp.adapters.openai_responses_adapter._dispatch_create_session",
                return_value={"status": "created", "session_id": "lta_test"},
            ) as mock_fn:
                adapter.execute_tool("create_session", {"objective": "x", "target_repo": "/r"})
                _, kwargs = mock_fn.call_args
                self.assertEqual(kwargs.get("output_root") or mock_fn.call_args[0][-1], tmp)


if __name__ == "__main__":
    unittest.main()
