"""Tests for LTA-5 LM Studio registration artifacts.

Validates mcp.json structure, lmstudio_setup.md content, and the --list-tools
dry-run CLI mode. No live LM Studio instance or MCP client is required.
"""

import json
import pathlib
import subprocess
import sys
import unittest

_HERE = pathlib.Path(__file__).parent
_PACKAGE_ROOT = _HERE.parent
_TOOLSET_ROOT = _PACKAGE_ROOT.parent
_EXAMPLES_DIR = _PACKAGE_ROOT / "examples"
_DOCS_DIR = _PACKAGE_ROOT / "docs"
_MCP_JSON = _EXAMPLES_DIR / "mcp.json"
_SETUP_DOC = _DOCS_DIR / "lmstudio_setup.md"

from local_tool_assist_mcp.session import TOOLCHAIN_ROOT


# ---------------------------------------------------------------------------
# mcp.json structure
# ---------------------------------------------------------------------------

class TestMcpJson(unittest.TestCase):

    def setUp(self):
        with open(_MCP_JSON, encoding="utf-8") as fh:
            self._cfg = json.load(fh)

    def test_mcp_json_is_valid_json(self):
        self.assertIsInstance(self._cfg, dict)

    def test_mcp_json_has_mcp_servers_key(self):
        self.assertIn("mcpServers", self._cfg)

    def test_config_contains_exactly_one_server_entry(self):
        servers = self._cfg["mcpServers"]
        self.assertEqual(len(servers), 1)

    def test_server_entry_name_is_aletheia(self):
        names = list(self._cfg["mcpServers"].keys())
        self.assertIn("aletheia-local-tool-assist", names)

    def _server(self):
        return self._cfg["mcpServers"]["aletheia-local-tool-assist"]

    def test_command_is_python(self):
        cmd = self._server()["command"]
        self.assertIn(cmd.lower(), {"python", "py", "python3"})

    def test_args_use_module_flag(self):
        args = self._server()["args"]
        self.assertIn("-m", args)

    def test_args_point_to_mcp_server_module(self):
        args = self._server()["args"]
        self.assertIn("local_tool_assist_mcp.mcp_server", args)

    def test_cwd_is_toolset_root(self):
        cwd = self._server()["cwd"]
        # Normalise separators for comparison
        cwd_path = pathlib.Path(cwd)
        self.assertEqual(cwd_path, _TOOLSET_ROOT)

    def test_cwd_is_not_aletheia_toolchain(self):
        cwd = pathlib.Path(self._server()["cwd"])
        self.assertNotEqual(cwd.resolve(), TOOLCHAIN_ROOT.resolve())

    def test_env_contains_toolset_root(self):
        env = self._server().get("env", {})
        self.assertIn("TOOLSET_ROOT", env)

    def test_env_contains_lta_output_root(self):
        env = self._server().get("env", {})
        self.assertIn("LTA_OUTPUT_ROOT", env)

    def test_output_root_is_outside_aletheia_toolchain(self):
        env = self._server().get("env", {})
        output_root = pathlib.Path(env.get("LTA_OUTPUT_ROOT", ""))
        try:
            output_root.resolve().relative_to(TOOLCHAIN_ROOT.resolve())
            self.fail("LTA_OUTPUT_ROOT must not be inside aletheia_toolchain")
        except ValueError:
            pass  # not a sub-path — correct

    def test_toolset_root_env_matches_cwd(self):
        env = self._server().get("env", {})
        cwd = self._server()["cwd"]
        self.assertEqual(
            pathlib.Path(env["TOOLSET_ROOT"]),
            pathlib.Path(cwd),
        )


# ---------------------------------------------------------------------------
# lmstudio_setup.md content
# ---------------------------------------------------------------------------

class TestLmstudioSetupDoc(unittest.TestCase):

    def setUp(self):
        self._doc = _SETUP_DOC.read_text(encoding="utf-8")

    def test_doc_exists(self):
        self.assertTrue(_SETUP_DOC.exists())

    def test_doc_mentions_backend_only(self):
        self.assertIn("backend", self._doc.lower())

    def test_doc_mentions_no_custom_ui(self):
        lower = self._doc.lower()
        self.assertTrue(
            "no custom" in lower or "not a" in lower or "no web ui" in lower,
            "Doc should clarify there is no custom UI/app",
        )

    def test_doc_mentions_review_gate_before_slicing(self):
        self.assertIn("slice_approved", self._doc)

    def test_doc_mentions_run_semantic_slice(self):
        self.assertIn("run_semantic_slice", self._doc)

    def test_doc_mentions_policy_block(self):
        self.assertIn("POLICY_BLOCK", self._doc)

    def test_doc_mentions_final_python_bundle(self):
        self.assertIn("final_python_bundle", self._doc)

    def test_doc_mentions_compile_handoff_report(self):
        self.assertIn("compile_handoff_report", self._doc)

    def test_doc_mentions_create_session(self):
        self.assertIn("create_session", self._doc)

    def test_doc_mentions_lta_wrapper_test_command(self):
        self.assertIn("local_tool_assist_mcp/tests", self._doc)

    def test_doc_mentions_aletheia_test_command(self):
        self.assertIn("aletheia_toolchain", self._doc)
        self.assertIn("unittest discover", self._doc)

    def test_doc_mentions_output_root(self):
        self.assertIn("local_tool_assist_outputs", self._doc)

    def test_doc_mentions_toolchain_boundary(self):
        lower = self._doc.lower()
        self.assertIn("aletheia_toolchain", lower)

    def test_doc_mentions_api_key_stripping(self):
        lower = self._doc.lower()
        self.assertTrue(
            "api_key" in lower or "api key" in lower,
            "Doc should mention API key stripping",
        )

    def test_doc_mentions_no_shell_execution(self):
        lower = self._doc.lower()
        self.assertTrue(
            "shell" in lower or "arbitrary shell" in lower,
            "Doc should mention no arbitrary shell execution",
        )

    def test_doc_mentions_mcp_import_troubleshooting(self):
        self.assertIn("pip install mcp", self._doc)

    def test_doc_mentions_list_tools_verification_step(self):
        self.assertIn("--list-tools", self._doc)


# ---------------------------------------------------------------------------
# Module importability
# ---------------------------------------------------------------------------

class TestModuleImportability(unittest.TestCase):

    def test_mcp_server_importable(self):
        import local_tool_assist_mcp.mcp_server as srv
        self.assertTrue(hasattr(srv, "APPROVED_TOOLS"))
        self.assertTrue(hasattr(srv, "run_stdio"))

    def test_approved_tools_has_8_entries(self):
        from local_tool_assist_mcp.mcp_server import APPROVED_TOOLS
        self.assertEqual(len(APPROVED_TOOLS), 8)

    def test_list_tools_function_importable(self):
        from local_tool_assist_mcp.mcp_server import _list_tools_and_exit
        self.assertTrue(callable(_list_tools_and_exit))


# ---------------------------------------------------------------------------
# --list-tools dry-run (subprocess)
# ---------------------------------------------------------------------------

class TestListToolsCLI(unittest.TestCase):

    def test_list_tools_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "local_tool_assist_mcp.mcp_server", "--list-tools"],
            capture_output=True,
            text=True,
            cwd=str(_TOOLSET_ROOT),
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_list_tools_prints_all_8_tools(self):
        from local_tool_assist_mcp.mcp_server import APPROVED_TOOLS
        result = subprocess.run(
            [sys.executable, "-m", "local_tool_assist_mcp.mcp_server", "--list-tools"],
            capture_output=True,
            text=True,
            cwd=str(_TOOLSET_ROOT),
        )
        for tool_name in APPROVED_TOOLS:
            self.assertIn(tool_name, result.stdout,
                          f"Tool {tool_name!r} missing from --list-tools output")

    def test_list_tools_does_not_start_server(self):
        # Must complete quickly — a running server would block indefinitely
        result = subprocess.run(
            [sys.executable, "-m", "local_tool_assist_mcp.mcp_server", "--list-tools"],
            capture_output=True,
            text=True,
            cwd=str(_TOOLSET_ROOT),
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)

    def test_mcp_json_parses_from_command_line(self):
        result = subprocess.run(
            [sys.executable, "-m", "json.tool", str(_MCP_JSON)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"json.tool failed: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
