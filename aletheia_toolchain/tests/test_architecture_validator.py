"""Tests for architecture_validator.py — Phase 7."""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_VALIDATOR = str(pathlib.Path(__file__).resolve().parents[1] / "architecture_validator.py")
_CWD = str(pathlib.Path(__file__).resolve().parents[1])
_SAMPLE_BUNDLE = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures" / "transcript_regressions" / "sample_bundle.json"
)
_EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples" / "configs"


def _run(args: list, cwd: str = _CWD) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _VALIDATOR] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _write_bundle(directory: pathlib.Path, data: dict, name: str = "bundle.json") -> pathlib.Path:
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_config(directory: pathlib.Path, data: dict, name: str = "config.json") -> pathlib.Path:
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_bundle(**overrides) -> dict:
    """Return a minimal valid synthetic bundle for testing."""
    base = {
        "meta": {
            "bundle_schema_version": "test-bundle-v1",
            "deterministic": True,
            "stats": {"bundled": 1, "skipped": 0, "errors": 0},
        },
        "layer_1_8_entry_points": ["main.py"],
        "layer_2_intelligence": [{"path": "main.py", "import_graph": ["json", "pathlib"]}],
        "layer_x_uncertainties": {"dynamic_behaviors": [], "syntax_errors": []},
        "system_architecture_context": {
            "system_contracts": ["Assertion constraint: result is not None"],
            "external_dependencies": {"stdlib": ["json", "pathlib"], "third_party": []},
        },
    }
    base.update(overrides)
    return base


def _minimal_config(**overrides) -> dict:
    base = {"schema_version": "1.0", "architecture_expectations": {}, "risk_rules": {}}
    base.update(overrides)
    return base


# ===========================================================================
# Basic CLI
# ===========================================================================

class TestHelpAndBasicCLI(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("--bundle", result.stdout)
        self.assertIn("--config", result.stdout)
        self.assertIn("--out", result.stdout)
        self.assertIn("--markdown-out", result.stdout)

    def test_missing_bundle_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run(["--out", str(out)])
            self.assertNotEqual(result.returncode, 0)

    def test_nonexistent_bundle_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run(["--bundle", "no_such_file.json", "--out", str(out)])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR", result.stdout + result.stderr)

    def test_malformed_json_bundle_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bad = tmp / "bad.json"
            bad.write_text("{ not valid json }", encoding="utf-8")
            out = tmp / "report.json"
            result = _run(["--bundle", str(bad), "--out", str(out)])
            self.assertNotEqual(result.returncode, 0)


# ===========================================================================
# Passing synthetic bundle
# ===========================================================================

class TestPassingSyntheticBundle(unittest.TestCase):
    def test_empty_config_passes(self):
        """No rules fired when config has no expectations."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, _minimal_config())
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "PASS")
            self.assertEqual(data["findings_count"], 0)

    def test_no_config_passes(self):
        """Running without --config must still produce a valid PASS report."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "PASS")

    def test_report_contains_expected_keys(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            for key in ("status", "bundle", "findings_count", "findings", "severity_counts"):
                self.assertIn(key, data, f"Missing key: {key}")

    def test_sample_fixture_with_python_project_config(self):
        """The sample_bundle.json fixture must produce a valid report with no crash."""
        self.assertTrue(_SAMPLE_BUNDLE.exists(), f"Sample bundle missing: {_SAMPLE_BUNDLE}")
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run([
                "--bundle", str(_SAMPLE_BUNDLE),
                "--config", str(_EXAMPLES / "python_project.json"),
                "--profile", "default",
                "--out", str(out),
            ])
            # We don't assert PASS/FAIL — the sample fixture may trigger WARN/FAIL.
            # We only assert it completes cleanly (exit 0 or 2), not 1 (invocation error).
            self.assertIn(result.returncode, (0, 2), result.stderr)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn(data["status"], ("PASS", "WARN", "FAIL"))


# ===========================================================================
# Required paths / exts
# ===========================================================================

class TestRequiredPathsAndExts(unittest.TestCase):
    def test_missing_required_path_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_paths": ["pyproject.toml"]}
            ))
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertEqual(result.returncode, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "WARN")
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV001", ids)

    def test_present_required_path_passes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_2_intelligence=[{"path": "pyproject.toml", "import_graph": []}]
            ))
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_paths": ["pyproject.toml"]}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertNotIn("R-AV001", ids)

    def test_missing_required_ext_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            # Bundle has .py file but config requires .ts
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_exts": [".ts"]}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV002", ids)


# ===========================================================================
# Required entry point
# ===========================================================================

class TestRequiredEntryPoint(unittest.TestCase):
    def test_missing_entry_point_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(layer_1_8_entry_points=[]))
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_entry_points": ["app.py"]}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV003", ids)

    def test_present_entry_point_no_r_av003(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(layer_1_8_entry_points=["app.py"]))
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_entry_points": ["app.py"]}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertNotIn("R-AV003", ids)


# ===========================================================================
# Forbidden import
# ===========================================================================

class TestForbiddenImports(unittest.TestCase):
    def test_forbidden_import_causes_fail(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_2_intelligence=[{"path": "legacy.py", "import_graph": ["pickle", "pathlib"]}]
            ))
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"forbidden_imports": ["pickle"]}
            ))
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertEqual(result.returncode, 2)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "FAIL")
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV005", ids)

    def test_clean_imports_no_r_av005(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"forbidden_imports": ["pickle"]}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertNotIn("R-AV005", ids)


# ===========================================================================
# Forbidden runtime behaviour
# ===========================================================================

class TestForbiddenRuntimeBehaviours(unittest.TestCase):
    def test_eval_in_layer_x_causes_fail(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_x_uncertainties={
                    "dynamic_behaviors": [
                        {"path": "risky.py", "flags": [{"type": "dynamic_behavior", "detail": "eval/exec"}]}
                    ],
                    "syntax_errors": [],
                }
            ))
            cfg = _write_config(tmp, _minimal_config(
                risk_rules={"forbidden_behaviors": ["eval"]}
            ))
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertEqual(result.returncode, 2)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "FAIL")
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV006", ids)

    def test_no_dynamic_behaviors_no_r_av006(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, _minimal_config(
                risk_rules={"forbidden_behaviors": ["eval", "exec"]}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertNotIn("R-AV006", ids)


# ===========================================================================
# Layer X uncertainty policy
# ===========================================================================

class TestLayerXUncertaintyPolicy(unittest.TestCase):
    def test_syntax_errors_always_warn(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_x_uncertainties={
                    "dynamic_behaviors": [],
                    "syntax_errors": ["broken.py (Line 5): invalid syntax"],
                }
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV008a", ids)

    def test_high_uncertainty_count_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            dyn = [{"path": f"f{i}.py", "flags": ["eval"]} for i in range(5)]
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_x_uncertainties={"dynamic_behaviors": dyn, "syntax_errors": []}
            ))
            cfg = _write_config(tmp, _minimal_config(risk_rules={"max_uncertainties": 3}))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV008b", ids)

    def test_uncertainty_within_threshold_no_r_av008b(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, _minimal_config(risk_rules={"max_uncertainties": 10}))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertNotIn("R-AV008b", ids)


# ===========================================================================
# Memory / performance pattern
# ===========================================================================

class TestMemoryPerformancePatterns(unittest.TestCase):
    def test_too_many_high_risk_slices_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            high_risk = {f"slice_{i}": {"risk_level": "HIGH", "risk_score": 65} for i in range(5)}
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_2_7_patch_collision_risk=high_risk
            ))
            cfg = _write_config(tmp, _minimal_config(risk_rules={"max_high_risk_slices": 3}))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV009", ids)

    def test_within_high_risk_threshold_no_r_av009(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_2_7_patch_collision_risk={
                    "s1": {"risk_level": "HIGH", "risk_score": 65}
                }
            ))
            cfg = _write_config(tmp, _minimal_config(risk_rules={"max_high_risk_slices": 10}))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertNotIn("R-AV009", ids)


# ===========================================================================
# Required output style
# ===========================================================================

class TestRequiredOutputStyle(unittest.TestCase):
    def test_non_deterministic_bundle_warns_when_required(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle_data = _minimal_bundle()
            bundle_data["meta"]["deterministic"] = False
            bundle = _write_bundle(tmp, bundle_data)
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_output_style": {"deterministic": True}}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("R-AV007", ids)

    def test_deterministic_bundle_no_r_av007(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())  # deterministic=True
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_output_style": {"deterministic": True}}
            ))
            out = tmp / "report.json"
            _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertNotIn("R-AV007", ids)


# ===========================================================================
# Plugin failure isolation
# ===========================================================================

class TestPluginFailureIsolation(unittest.TestCase):
    def _write_plugin(self, directory: pathlib.Path, code: str, name: str = "plugin.py") -> pathlib.Path:
        path = directory / name
        path.write_text(code, encoding="utf-8")
        return path

    def test_crashing_plugin_becomes_warn_not_crash(self):
        """A plugin that raises on import or execution must become a WARN finding, not a hard crash."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            plugin = self._write_plugin(tmp, "def run_checks(bundle, config):\n    raise RuntimeError('plugin exploded')\n")
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, {
                "schema_version": "1.0",
                "plugins": [str(plugin)],
                "architecture_expectations": {},
                "risk_rules": {},
            })
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            # Must NOT exit 1 (invocation error); plugin failure is not a crash
            self.assertIn(result.returncode, (0, 2), result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("PLUGIN-ERROR", ids)
            # Severity must be WARN, not FAIL
            severities = {f["id"]: f["severity"] for f in data["findings"]}
            self.assertEqual(severities.get("PLUGIN-ERROR"), "WARN")

    def test_working_plugin_findings_included(self):
        """A well-behaved plugin that returns findings must have them in the report."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            plugin = self._write_plugin(tmp,
                "def run_checks(bundle, config):\n"
                "    return [{'id': 'P001', 'severity': 'INFO', 'message': 'plugin ran'}]\n"
            )
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, {
                "schema_version": "1.0",
                "plugins": [str(plugin)],
                "architecture_expectations": {},
                "risk_rules": {},
            })
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("P001", ids)

    def test_nonexistent_plugin_path_becomes_warn(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, {
                "schema_version": "1.0",
                "plugins": ["nonexistent_plugin_xyz.py"],
                "architecture_expectations": {},
                "risk_rules": {},
            })
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertIn(result.returncode, (0, 2))
            data = json.loads(out.read_text(encoding="utf-8"))
            ids = [f["id"] for f in data["findings"]]
            self.assertIn("PLUGIN-ERROR", ids)


# ===========================================================================
# Markdown report
# ===========================================================================

class TestMarkdownReport(unittest.TestCase):
    def test_markdown_report_created(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            out = tmp / "report.json"
            md = tmp / "report.md"
            result = _run([
                "--bundle", str(bundle),
                "--out", str(out),
                "--markdown-out", str(md),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(md.exists(), "Markdown report file was not created")
            content = md.read_text(encoding="utf-8")
            self.assertIn("# Architecture Validator Report", content)
            self.assertIn("PASS", content)

    def test_markdown_report_contains_findings_section(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_x_uncertainties={"dynamic_behaviors": [], "syntax_errors": ["bad.py: error"]}
            ))
            out = tmp / "report.json"
            md = tmp / "report.md"
            _run(["--bundle", str(bundle), "--out", str(out), "--markdown-out", str(md)])
            content = md.read_text(encoding="utf-8")
            self.assertIn("Findings", content)
            self.assertIn("R-AV008a", content)


# ===========================================================================
# Exit codes
# ===========================================================================

class TestExitCodes(unittest.TestCase):
    def test_pass_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--out", str(out)])
            self.assertEqual(result.returncode, 0)

    def test_warn_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle())
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"required_paths": ["missing.toml"]}
            ))
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertEqual(result.returncode, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "WARN")

    def test_fail_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            bundle = _write_bundle(tmp, _minimal_bundle(
                layer_2_intelligence=[{"path": "x.py", "import_graph": ["pickle"]}]
            ))
            cfg = _write_config(tmp, _minimal_config(
                architecture_expectations={"forbidden_imports": ["pickle"]}
            ))
            out = tmp / "report.json"
            result = _run(["--bundle", str(bundle), "--config", str(cfg), "--out", str(out)])
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
