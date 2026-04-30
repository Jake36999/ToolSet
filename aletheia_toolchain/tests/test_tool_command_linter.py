"""Tests for tool_command_linter.py."""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_LINTER = str(pathlib.Path(__file__).resolve().parents[1] / "tool_command_linter.py")
_CWD = str(pathlib.Path(__file__).resolve().parents[1])


def _run(args: list, cwd: str = _CWD) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _LINTER] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _lint_cmd(command: str, extra_args: list = None, tmp: pathlib.Path = None) -> dict:
    """Lint an inline command and return the parsed JSON report."""
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "report.json"
        base_args = ["--command", command, "--out", str(out)]
        if extra_args:
            base_args += extra_args
        result = _run(base_args)
        if out.exists():
            return json.loads(out.read_text(encoding="utf-8"))
        raise AssertionError(f"Linter did not write report.\nstdout: {result.stdout}\nstderr: {result.stderr}")


class TestFilemapV2Rules(unittest.TestCase):
    """R001: create_file_map_v2.py -o must be BLOCK."""

    def test_v2_dash_o_is_blocked(self):
        data = _lint_cmd("python create_file_map_v2.py -o output.csv")
        self.assertEqual(data["status"], "BLOCK")
        self.assertFalse(data["safe_to_run"])
        rule_ids = [e["rule_id"] for e in data["errors"]]
        self.assertIn("R001", rule_ids)

    def test_v2_out_flag_passes(self):
        data = _lint_cmd("python create_file_map_v2.py --out output.csv --roots .")
        self.assertEqual(data["status"], "PASS")
        self.assertTrue(data["safe_to_run"])

    def test_v2_path_with_dash_o_is_blocked(self):
        """Full path to script still triggers R001."""
        data = _lint_cmd(
            r'python "D:\Aletheia_project\DEV_TOOLS\create_file_map_v2.py" -o output.csv'
        )
        self.assertEqual(data["status"], "BLOCK")
        rule_ids = [e["rule_id"] for e in data["errors"]]
        self.assertIn("R001", rule_ids)


class TestFilemapV3Rules(unittest.TestCase):
    """R002: create_file_map_v3.py -o is WARN, not BLOCK; safe_to_run stays True."""

    def test_v3_dash_o_warns_but_safe(self):
        data = _lint_cmd("python create_file_map_v3.py -o output.csv")
        self.assertEqual(data["status"], "WARN")
        self.assertTrue(data["safe_to_run"], "WARN should still be safe_to_run=True")
        rule_ids = [w["rule_id"] for w in data["warnings"]]
        self.assertIn("R002", rule_ids)

    def test_v3_out_flag_passes(self):
        data = _lint_cmd("python create_file_map_v3.py --out output.csv --roots .")
        self.assertEqual(data["status"], "PASS")
        self.assertTrue(data["safe_to_run"])

    def test_v3_dash_o_has_autofix(self):
        data = _lint_cmd("python create_file_map_v3.py -o output.csv")
        self.assertTrue(
            any("--out" in (s or "") for s in data["autofix_suggestions"]),
            "R002 should suggest replacing -o with --out"
        )


class TestSlicerBroadPositional(unittest.TestCase):
    """R003: slicer positional '.' without --manifest → WARN."""

    def test_broad_dot_warns(self):
        data = _lint_cmd("python semantic_slicer_v6.0.py .")
        self.assertIn(data["status"], ("WARN", "BLOCK"))
        all_rule_ids = (
            [e["rule_id"] for e in data["errors"]] +
            [w["rule_id"] for w in data["warnings"]]
        )
        self.assertIn("R003", all_rule_ids)

    def test_manifest_only_passes(self):
        """Using --manifest without positional '.' is the safe pattern."""
        data = _lint_cmd("python semantic_slicer_v6.0.py --manifest file_map.csv --deterministic")
        self.assertEqual(data["status"], "PASS")
        self.assertTrue(data["safe_to_run"])


class TestSlicerManifestPlusDot(unittest.TestCase):
    """R004: slicer --manifest + positional '.' → BLOCK."""

    def test_manifest_plus_dot_is_blocked(self):
        data = _lint_cmd("python semantic_slicer_v6.0.py . --manifest file_map.csv")
        self.assertEqual(data["status"], "BLOCK")
        self.assertFalse(data["safe_to_run"])
        rule_ids = [e["rule_id"] for e in data["errors"]]
        self.assertIn("R004", rule_ids)

    def test_manifest_plus_dot_has_autofix(self):
        data = _lint_cmd("python semantic_slicer_v6.0.py . --manifest file_map.csv")
        self.assertTrue(
            any("." in (s or "") for s in data["autofix_suggestions"]),
            "R004 should suggest removing the positional '.'"
        )


class TestSlicerDeterministic(unittest.TestCase):
    """R005: automated slicer extraction missing --deterministic → WARN."""

    def test_agent_task_without_deterministic_warns(self):
        data = _lint_cmd(
            "python semantic_slicer_v6.0.py --manifest map.csv --agent-task analyze"
        )
        self.assertIn(data["status"], ("WARN", "BLOCK"))
        rule_ids = (
            [e["rule_id"] for e in data["errors"]] +
            [w["rule_id"] for w in data["warnings"]]
        )
        self.assertIn("R005", rule_ids)

    def test_agent_role_without_deterministic_warns(self):
        data = _lint_cmd(
            "python semantic_slicer_v6.0.py --manifest map.csv --agent-role reviewer"
        )
        rule_ids = (
            [e["rule_id"] for e in data["errors"]] +
            [w["rule_id"] for w in data["warnings"]]
        )
        self.assertIn("R005", rule_ids)

    def test_agent_task_with_deterministic_no_r005(self):
        data = _lint_cmd(
            "python semantic_slicer_v6.0.py --manifest map.csv --agent-task analyze --deterministic"
        )
        all_ids = (
            [e["rule_id"] for e in data["errors"]] +
            [w["rule_id"] for w in data["warnings"]]
        )
        self.assertNotIn("R005", all_ids)


class TestReIngestionOutput(unittest.TestCase):
    """R006: output path matching re-ingestion patterns → WARN."""

    def test_bundle_output_name_warns(self):
        data = _lint_cmd(
            "python create_file_map_v3.py --roots . --out project_bundle_20260429.csv"
        )
        rule_ids = (
            [e["rule_id"] for e in data["errors"]] +
            [w["rule_id"] for w in data["warnings"]]
        )
        self.assertIn("R006", rule_ids)

    def test_extraction_output_name_warns(self):
        data = _lint_cmd(
            r"python semantic_slicer_v6.0.py --manifest map.csv -o phase3_Extraction.py"
        )
        rule_ids = (
            [e["rule_id"] for e in data["errors"]] +
            [w["rule_id"] for w in data["warnings"]]
        )
        self.assertIn("R006", rule_ids)

    def test_clean_output_name_no_r006(self):
        data = _lint_cmd(
            "python create_file_map_v3.py --roots . --out filtered_map.csv"
        )
        all_ids = (
            [e["rule_id"] for e in data["errors"]] +
            [w["rule_id"] for w in data["warnings"]]
        )
        self.assertNotIn("R006", all_ids)


class TestCommandFileInput(unittest.TestCase):
    """--command-file reads a file; R007 fires for file-level missing doctor step."""

    def test_command_file_with_clean_commands(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            cmd_file = tmp / "cmds.ps1"
            cmd_file.write_text(
                "python create_file_map_v3.py --roots . --out map.csv\n"
                "python manifest_doctor.py --manifest map.csv --out report.json\n"
                "python semantic_slicer_v6.0.py --manifest map.csv --deterministic\n",
                encoding="utf-8",
            )
            out = tmp / "report.json"
            result = _run(["--command-file", str(cmd_file), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["command_source"], "cmds.ps1")
            self.assertEqual(data["status"], "PASS")

    def test_command_file_ps1_with_backtick_continuation(self):
        """PS1 backtick-continued lines are joined correctly."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            cmd_file = tmp / "cmds.ps1"
            cmd_file.write_text(
                "python semantic_slicer_v6.0.py `\n"
                "    --manifest map.csv `\n"
                "    --deterministic\n",
                encoding="utf-8",
            )
            out = tmp / "report.json"
            _run(["--command-file", str(cmd_file), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            # No R004 (no positional '.'), no R003, should be PASS
            all_ids = (
                [e["rule_id"] for e in data["errors"]] +
                [w["rule_id"] for w in data["warnings"]]
            )
            self.assertNotIn("R004", all_ids)
            self.assertNotIn("R003", all_ids)

    def test_command_file_broad_slicer_without_doctor_warns_r007(self):
        """R007 fires when a command file has a broad slicer but no manifest_doctor step."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            cmd_file = tmp / "no_doctor.ps1"
            cmd_file.write_text(
                "python create_file_map_v3.py --roots . --out map.csv\n"
                "python semantic_slicer_v6.0.py . --deterministic\n",
                encoding="utf-8",
            )
            out = tmp / "report.json"
            _run(["--command-file", str(cmd_file), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            all_ids = (
                [e["rule_id"] for e in data["errors"]] +
                [w["rule_id"] for w in data["warnings"]]
            )
            self.assertIn("R007", all_ids)


class TestRewriteOut(unittest.TestCase):
    """--rewrite-out writes suggestions without mutating the original."""

    def test_rewrite_out_written_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            cmd_file = tmp / "commands.ps1"
            original_content = "python create_file_map_v2.py -o output.csv\n"
            cmd_file.write_text(original_content, encoding="utf-8")

            out = tmp / "report.json"
            rewrite = tmp / "rewrites.txt"

            _run([
                "--command-file", str(cmd_file),
                "--out", str(out),
                "--rewrite-out", str(rewrite),
            ])

            # Original must not be mutated
            self.assertEqual(cmd_file.read_text(encoding="utf-8"), original_content)

            # Rewrite file must exist and contain suggestions
            self.assertTrue(rewrite.exists(), "rewrite-out file was not created")
            content = rewrite.read_text(encoding="utf-8")
            self.assertIn("R001", content)
            self.assertIn("--out", content)
            self.assertIn("no original command was modified", content.lower())

    def test_rewrite_out_with_no_issues_notes_no_suggestions(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            out = tmp / "report.json"
            rewrite = tmp / "rewrites.txt"

            _run([
                "--command",
                "python create_file_map_v3.py --out map.csv --roots .",
                "--out", str(out),
                "--rewrite-out", str(rewrite),
            ])

            self.assertTrue(rewrite.exists())
            content = rewrite.read_text(encoding="utf-8")
            self.assertIn("No autofix", content)


class TestLinterDoesNotExecute(unittest.TestCase):
    """Linter must not execute the command being linted."""

    def test_dangerous_command_is_not_executed(self):
        """A rm/del command embedded with a Python tool must not run."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            canary = tmp / "canary.txt"
            canary.write_text("safe", encoding="utf-8")

            # Embed a command that would delete the canary if executed
            cmd = f"python create_file_map_v3.py --out out.csv; del {canary}"
            out = tmp / "report.json"
            _run(["--command", cmd, "--out", str(out)])

            # Canary must still exist
            self.assertTrue(
                canary.exists(),
                "Linter must not execute the command under lint"
            )


class TestHelpAndBasicCLI(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("--command", result.stdout)
        self.assertIn("--command-file", result.stdout)
        self.assertIn("--rewrite-out", result.stdout)

    def test_missing_required_arg_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run(["--out", str(out)])
            self.assertNotEqual(result.returncode, 0)

    def test_block_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run(["--command", "python create_file_map_v2.py -o out.csv",
                           "--out", str(out)])
            self.assertEqual(result.returncode, 2)

    def test_warn_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run(["--command", "python create_file_map_v3.py -o out.csv",
                           "--out", str(out)])
            self.assertEqual(result.returncode, 0)

    def test_pass_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run([
                "--command",
                "python create_file_map_v3.py --out map.csv --roots .",
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
