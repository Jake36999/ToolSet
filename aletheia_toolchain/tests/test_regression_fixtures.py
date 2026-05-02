"""tests/test_regression_fixtures.py — Phase 12.

Regression tests that verify transcript-derived command patterns still
produce the expected lint/doctor outcomes.  These tests are the
machine-readable equivalent of the human-reviewed transcript log.

Each test loads a fixture from
  tests/fixtures/transcript_regressions/
and asserts that the relevant tool produces the documented outcome.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_TOOLCHAIN = pathlib.Path(__file__).parent.parent
_FIXTURES = _TOOLCHAIN / "tests" / "fixtures" / "transcript_regressions"
_COMMANDS_DIR = _FIXTURES / "commands"
_EDGE_CASES_DIR = _FIXTURES / "edge_cases"

_LINTER = str(_TOOLCHAIN / "tool_command_linter.py")
_DOCTOR = str(_TOOLCHAIN / "manifest_doctor.py")


def _run_linter(command: str, out_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _LINTER, "--command", command, "--out", out_path],
        cwd=str(_TOOLCHAIN),
        capture_output=True,
        text=True,
    )


def _run_doctor(manifest_path: str, out_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _DOCTOR, "--manifest", manifest_path, "--out", out_path],
        cwd=str(_TOOLCHAIN),
        capture_output=True,
        text=True,
    )


def _load_fixture_command(name: str) -> dict:
    path = _COMMANDS_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixture existence
# ---------------------------------------------------------------------------

class TestFixtureExistence(unittest.TestCase):

    def test_command_fixtures_exist(self):
        expected = [
            "cmd_v2_o_flag.json",
            "cmd_v3_o_flag.json",
            "cmd_broad_scan.json",
            "cmd_manifest_plus_dot.json",
        ]
        for name in expected:
            self.assertTrue(
                (_COMMANDS_DIR / name).exists(),
                f"Missing fixture: commands/{name}",
            )

    def test_edge_case_fixtures_exist(self):
        expected = [
            "empty_directory.csv",
            "malformed_manifest.csv",
            "oversized_files.csv",
            "polluted_manifest.csv",
        ]
        for name in expected:
            self.assertTrue(
                (_EDGE_CASES_DIR / name).exists(),
                f"Missing fixture: edge_cases/{name}",
            )

    def test_sample_fixtures_exist(self):
        expected = [
            "sample_manifest.csv",
            "sample_bundle.json",
            "sample_command.ps1",
        ]
        for name in expected:
            self.assertTrue(
                (_FIXTURES / name).exists(),
                f"Missing fixture: {name}",
            )


# ---------------------------------------------------------------------------
# Command regression: R001 — create_file_map_v2 -o → BLOCK
# ---------------------------------------------------------------------------

class TestRegressionR001(unittest.TestCase):

    def test_v2_o_flag_is_block(self):
        fixture = _load_fixture_command("cmd_v2_o_flag.json")
        self.assertEqual(fixture["expected_rule"], "R001")
        self.assertEqual(fixture["expected_status"], "BLOCK")
        with tempfile.TemporaryDirectory() as td:
            out = str(pathlib.Path(td) / "lint.json")
            result = _run_linter(fixture["command"], out)
            report = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 2, "Expected exit 2 for BLOCK")
        self.assertEqual(report["status"], "BLOCK")
        rule_ids = [f["rule_id"] for f in report.get("errors", [])]
        self.assertIn("R001", rule_ids)


# ---------------------------------------------------------------------------
# Command regression: R002 — create_file_map_v3 -o → WARN
# ---------------------------------------------------------------------------

class TestRegressionR002(unittest.TestCase):

    def test_v3_o_flag_is_warn(self):
        fixture = _load_fixture_command("cmd_v3_o_flag.json")
        self.assertEqual(fixture["expected_rule"], "R002")
        self.assertEqual(fixture["expected_status"], "WARN")
        with tempfile.TemporaryDirectory() as td:
            out = str(pathlib.Path(td) / "lint.json")
            result = _run_linter(fixture["command"], out)
            report = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))
        self.assertIn(result.returncode, (0, 2), "Exit should be 0 or 2")
        self.assertEqual(report["status"], "WARN")
        rule_ids = [f["rule_id"] for f in report.get("warnings", [])]
        self.assertIn("R002", rule_ids)


# ---------------------------------------------------------------------------
# Command regression: R003 — slicer broad scan → WARN
# ---------------------------------------------------------------------------

class TestRegressionR003(unittest.TestCase):

    def test_broad_scan_is_warn(self):
        fixture = _load_fixture_command("cmd_broad_scan.json")
        self.assertEqual(fixture["expected_rule"], "R003")
        self.assertEqual(fixture["expected_status"], "WARN")
        with tempfile.TemporaryDirectory() as td:
            out = str(pathlib.Path(td) / "lint.json")
            result = _run_linter(fixture["command"], out)
            report = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))
        self.assertIn(result.returncode, (0, 2), "Exit should be 0 or 2")
        self.assertEqual(report["status"], "WARN")
        rule_ids = [f["rule_id"] for f in report.get("warnings", [])]
        self.assertIn("R003", rule_ids)


# ---------------------------------------------------------------------------
# Command regression: R004 — slicer --manifest + positional . → BLOCK
# ---------------------------------------------------------------------------

class TestRegressionR004(unittest.TestCase):

    def test_manifest_plus_dot_is_block(self):
        fixture = _load_fixture_command("cmd_manifest_plus_dot.json")
        self.assertEqual(fixture["expected_rule"], "R004")
        self.assertEqual(fixture["expected_status"], "BLOCK")
        with tempfile.TemporaryDirectory() as td:
            out = str(pathlib.Path(td) / "lint.json")
            result = _run_linter(fixture["command"], out)
            report = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 2, "Expected exit 2 for BLOCK")
        self.assertEqual(report["status"], "BLOCK")
        rule_ids = [f["rule_id"] for f in report.get("errors", [])]
        self.assertIn("R004", rule_ids)


# ---------------------------------------------------------------------------
# Polluted manifest — manifest_doctor WARN or BLOCK
# ---------------------------------------------------------------------------

class TestRegressionPollutedManifest(unittest.TestCase):

    def test_polluted_manifest_triggers_doctor_issues(self):
        manifest_path = str(_EDGE_CASES_DIR / "polluted_manifest.csv")
        with tempfile.TemporaryDirectory() as td:
            out = str(pathlib.Path(td) / "doctor.json")
            _run_doctor(manifest_path, out)
            report = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))
        status = report.get("status", "")
        self.assertIn(
            status, ("WARN", "BLOCK"),
            f"Expected polluted manifest to produce WARN or BLOCK, got {status!r}",
        )
        findings = report.get("findings", {})
        has_bundle_artifact = bool(findings.get("bundle_artifacts"))
        has_suspicious = bool(findings.get("suspicious_paths"))
        self.assertTrue(
            has_bundle_artifact or has_suspicious,
            "Expected findings.bundle_artifacts or findings.suspicious_paths to be non-empty",
        )

    def test_malformed_manifest_loads_without_crash(self):
        manifest_path = str(_EDGE_CASES_DIR / "malformed_manifest.csv")
        with tempfile.TemporaryDirectory() as td:
            out = str(pathlib.Path(td) / "doctor.json")
            result = _run_doctor(manifest_path, out)
        self.assertIn(result.returncode, (0, 2), "doctor should not exit 1 on malformed CSV")


if __name__ == "__main__":
    unittest.main()
