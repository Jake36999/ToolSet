"""tests/test_pipeline_gatekeeper.py — Phase 10.

Tests for pipeline_gatekeeper.py.
Covers: PASS/WARN/BLOCK verdicts, missing required reports, custom policy,
overrideable gates, exit codes, and Markdown output.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_CWD = str(pathlib.Path(__file__).parent.parent)
_TOOL = str(pathlib.Path(__file__).parent.parent / "pipeline_gatekeeper.py")
_ENCODING = "utf-8"


def _run(args: list, cwd: str = _CWD) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _TOOL] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _write_json(directory: pathlib.Path, data: dict, name: str) -> pathlib.Path:
    p = directory / name
    p.write_text(json.dumps(data, indent=2), encoding=_ENCODING)
    return p


def _manifest_report(status: str = "PASS") -> dict:
    return {
        "tool_version": "v3.0",
        "status": status,
        "summary": {"row_count": 10, "missing_files": 0},
        "findings": {},
        "recommended_action": "No action required.",
    }


def _validator_report(status: str = "PASS", findings_count: int = 0) -> dict:
    return {
        "validator_version": "1.0",
        "status": status,
        "findings_count": findings_count,
        "severity_counts": {"PASS": 5, "WARN": 0, "FAIL": 0},
        "findings": [],
    }


def _runtime_report(overall_memory_risk: str = "NONE") -> dict:
    return {
        "tool_version": "v9.0",
        "run_name": "test_run",
        "overall_memory_risk": overall_memory_risk,
        "findings": [],
        "timed_out": False,
        "suggested_commands": [],
        "uncertainty_notes": "",
        "evidence_sources_used": [],
    }


def _policy(**overrides) -> dict:
    base: dict = {
        "policy_version": "1.0",
        "required_reports": [],
        "gates": {
            "manifest": {
                "label": "Manifest health",
                "status_field": "status",
                "status_map": {"PASS": "PASS", "WARN": "WARN", "BLOCK": "BLOCK"},
                "overrideable": False,
            },
            "validator": {
                "label": "Architecture validation",
                "status_field": "status",
                "status_map": {"PASS": "PASS", "WARN": "WARN", "FAIL": "BLOCK"},
                "overrideable": True,
            },
            "runtime": {
                "label": "Runtime memory risk",
                "status_field": "overall_memory_risk",
                "status_map": {
                    "NONE": "PASS", "LOW": "WARN", "MEDIUM": "WARN", "HIGH": "BLOCK",
                },
                "overrideable": True,
            },
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# CLI basics
# ---------------------------------------------------------------------------

class TestGatekeeperCLI(unittest.TestCase):

    def test_help_exits_zero(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("pipeline_gatekeeper", result.stdout)

    def test_out_required(self):
        result = _run([])
        self.assertNotEqual(result.returncode, 0)

    def test_no_reports_all_optional_returns_pass(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "gk.json"
            result = _run(["--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "PASS")
        self.assertEqual(data["failed_gates"], [])


# ---------------------------------------------------------------------------
# PASS gate
# ---------------------------------------------------------------------------

class TestGatekeeperPass(unittest.TestCase):

    def test_all_pass_returns_pass_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            mr = _write_json(td_path, _manifest_report("PASS"), "manifest.json")
            vr = _write_json(td_path, _validator_report("PASS"), "validator.json")
            rr = _write_json(td_path, _runtime_report("NONE"), "runtime.json")
            out = td_path / "gk.json"
            result = _run([
                "--manifest-report", str(mr),
                "--validator-report", str(vr),
                "--runtime-report", str(rr),
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "PASS")
        self.assertEqual(data["failed_gates"], [])
        self.assertEqual(data["warning_gates"], [])

    def test_input_report_summary_populated(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            mr = _write_json(td_path, _manifest_report("PASS"), "manifest.json")
            vr = _write_json(td_path, _validator_report("PASS"), "validator.json")
            out = td_path / "gk.json"
            _run([
                "--manifest-report", str(mr),
                "--validator-report", str(vr),
                "--out", str(out),
            ])
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("manifest", data["input_report_summary"])
        self.assertIn("validator", data["input_report_summary"])
        self.assertEqual(data["input_report_summary"]["manifest"]["status"], "PASS")


# ---------------------------------------------------------------------------
# WARN gate
# ---------------------------------------------------------------------------

class TestGatekeeperWarn(unittest.TestCase):

    def test_manifest_warn_returns_warn_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            mr = _write_json(td_path, _manifest_report("WARN"), "manifest.json")
            out = td_path / "gk.json"
            result = _run(["--manifest-report", str(mr), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "WARN")
        self.assertEqual(len(data["warning_gates"]), 1)
        self.assertEqual(data["warning_gates"][0]["gate_id"], "manifest")

    def test_runtime_low_risk_returns_warn(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            rr = _write_json(td_path, _runtime_report("LOW"), "runtime.json")
            out = td_path / "gk.json"
            result = _run(["--runtime-report", str(rr), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "WARN")

    def test_warning_gates_list_populated(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            vr = _write_json(td_path, _validator_report("WARN"), "validator.json")
            out = td_path / "gk.json"
            _run(["--validator-report", str(vr), "--out", str(out)])
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(len(data["warning_gates"]), 1)
        self.assertEqual(data["warning_gates"][0]["gate_id"], "validator")
        self.assertEqual(data["warning_gates"][0]["outcome"], "WARN")


# ---------------------------------------------------------------------------
# BLOCK gate
# ---------------------------------------------------------------------------

class TestGatekeeperBlock(unittest.TestCase):

    def test_manifest_block_returns_block_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            mr = _write_json(td_path, _manifest_report("BLOCK"), "manifest.json")
            out = td_path / "gk.json"
            result = _run(["--manifest-report", str(mr), "--out", str(out)])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "BLOCK")
        self.assertEqual(len(data["failed_gates"]), 1)
        self.assertEqual(data["failed_gates"][0]["gate_id"], "manifest")

    def test_validator_fail_maps_to_block(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            vr = _write_json(td_path, _validator_report("FAIL"), "validator.json")
            out = td_path / "gk.json"
            result = _run(["--validator-report", str(vr), "--out", str(out)])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "BLOCK")
        self.assertEqual(data["failed_gates"][0]["report_status"], "FAIL")

    def test_runtime_high_risk_maps_to_block(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            rr = _write_json(td_path, _runtime_report("HIGH"), "runtime.json")
            out = td_path / "gk.json"
            result = _run(["--runtime-report", str(rr), "--out", str(out)])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "BLOCK")

    def test_block_takes_precedence_over_warn(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            mr = _write_json(td_path, _manifest_report("WARN"), "manifest.json")
            vr = _write_json(td_path, _validator_report("FAIL"), "validator.json")
            out = td_path / "gk.json"
            result = _run([
                "--manifest-report", str(mr),
                "--validator-report", str(vr),
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "BLOCK")
        self.assertEqual(len(data["failed_gates"]), 1)
        self.assertEqual(len(data["warning_gates"]), 1)

    def test_failed_gates_populated_with_details(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            vr = _write_json(td_path, _validator_report("FAIL"), "validator.json")
            out = td_path / "gk.json"
            _run(["--validator-report", str(vr), "--out", str(out)])
            data = json.loads(out.read_text(encoding=_ENCODING))
        gate = data["failed_gates"][0]
        self.assertIn("gate_id", gate)
        self.assertIn("label", gate)
        self.assertIn("outcome", gate)
        self.assertIn("report_path", gate)
        self.assertIn("overrideable", gate)


# ---------------------------------------------------------------------------
# Missing reports
# ---------------------------------------------------------------------------

class TestGatekeeperMissingReports(unittest.TestCase):

    def test_missing_optional_report_skipped_no_block(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            mr = _write_json(td_path, _manifest_report("PASS"), "manifest.json")
            out = td_path / "gk.json"
            result = _run(["--manifest-report", str(mr), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "PASS")
        self.assertNotIn("validator", data["missing_reports"])

    def test_missing_required_report_causes_block(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            pol = _write_json(
                td_path,
                _policy(required_reports=["manifest"]),
                "policy.json",
            )
            out = td_path / "gk.json"
            result = _run(["--policy", str(pol), "--out", str(out)])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "BLOCK")
        self.assertIn("manifest", data["missing_reports"])

    def test_report_file_not_found_causes_block(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            out = td_path / "gk.json"
            result = _run([
                "--manifest-report", str(td_path / "nonexistent.json"),
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "BLOCK")
        self.assertIn("manifest", data["missing_reports"])


# ---------------------------------------------------------------------------
# Overrideable gates
# ---------------------------------------------------------------------------

class TestGatekeeperOverrideable(unittest.TestCase):

    def test_overrideable_gates_listed_for_block(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            vr = _write_json(td_path, _validator_report("FAIL"), "validator.json")
            out = td_path / "gk.json"
            _run(["--validator-report", str(vr), "--out", str(out)])
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("validator", data["overrideable_gates"])

    def test_non_overrideable_gate_not_in_overrideable_list(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            mr = _write_json(td_path, _manifest_report("BLOCK"), "manifest.json")
            out = td_path / "gk.json"
            _run(["--manifest-report", str(mr), "--out", str(out)])
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertNotIn("manifest", data["overrideable_gates"])

    def test_custom_policy_overrides_default(self):
        """A custom policy with all gates mapped to PASS should always pass."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            custom = {
                "policy_version": "1.0",
                "required_reports": [],
                "gates": {
                    "validator": {
                        "label": "Validator (override)",
                        "status_field": "status",
                        "status_map": {
                            "PASS": "PASS",
                            "WARN": "PASS",
                            "FAIL": "WARN",
                        },
                        "overrideable": True,
                    },
                },
            }
            pol = _write_json(td_path, custom, "policy.json")
            vr = _write_json(td_path, _validator_report("FAIL"), "validator.json")
            out = td_path / "gk.json"
            result = _run([
                "--validator-report", str(vr),
                "--policy", str(pol),
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(data["status"], "WARN")


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

class TestGatekeeperMarkdown(unittest.TestCase):

    def test_markdown_out_generated(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            vr = _write_json(td_path, _validator_report("FAIL"), "validator.json")
            out = td_path / "gk.json"
            md = td_path / "gk.md"
            result = _run([
                "--validator-report", str(vr),
                "--out", str(out),
                "--markdown-out", str(md),
            ])
            self.assertEqual(result.returncode, 2, result.stderr)
            md_text = md.read_text(encoding=_ENCODING)
        self.assertIn("Pipeline Gatekeeper Report", md_text)
        self.assertIn("BLOCK", md_text)


if __name__ == "__main__":
    unittest.main()
