"""Tests for Phase 9 runtime forensics tools.

Covers:
  oom_forensics_reporter.py
  runtime_slice_correlator.py
  runtime_packager.py

Strategy: all tests drive the tools via subprocess using synthetic Phase 8
artefact directories written to tempfile.TemporaryDirectory.  No live
processes are launched by these tests — artefacts are written directly.

IMPORTANT: All file reads must occur INSIDE the TemporaryDirectory context
manager.  The directory is deleted when the `with` block exits, so file
contents are loaded into local variables before assertions are made.
"""

import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_OOM = str(pathlib.Path(__file__).resolve().parents[1] / "oom_forensics_reporter.py")
_CORR = str(pathlib.Path(__file__).resolve().parents[1] / "runtime_slice_correlator.py")
_PACK = str(pathlib.Path(__file__).resolve().parents[1] / "runtime_packager.py")
_CWD = str(pathlib.Path(__file__).resolve().parents[1])

_ENCODING = "utf-8"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _run(script: str, args: list, cwd: str = _CWD) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, script] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _write_metrics(
    runtime_dir: pathlib.Path,
    exit_code: int = 0,
    timed_out: bool = False,
    start_failed: bool = False,
    duration_s: float = 1.0,
    run_name: str = "test_run",
    sample_count: int = 0,
) -> None:
    data = {
        "watcher_version": "v8.0",
        "run_name": run_name,
        "cmd": ["python", "-c", "pass"],
        "exit_code": exit_code,
        "timed_out": timed_out,
        "start_failed": start_failed,
        "start_error": "",
        "start_iso": "2026-04-30T10:00:00",
        "end_iso": "2026-04-30T10:00:01",
        "duration_s": duration_s,
        "metrics_mode": "basic",
        "psutil_available": False,
        "sample_count": sample_count,
    }
    (runtime_dir / "runtime_metrics.json").write_text(
        json.dumps(data), encoding=_ENCODING
    )


def _write_timeline(runtime_dir: pathlib.Path, rows: list = None) -> None:
    fieldnames = [
        "timestamp", "cpu_percent", "rss_bytes", "vms_bytes",
        "num_threads", "metrics_error", "psutil_available",
    ]
    with (runtime_dir / "timeline.csv").open("w", newline="", encoding=_ENCODING) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in (rows or []):
            writer.writerow({f: row.get(f, "") for f in fieldnames})


def _make_runtime_dir(
    tmp: pathlib.Path,
    *,
    exit_code: int = 0,
    timed_out: bool = False,
    start_failed: bool = False,
    stdout_content: str = "",
    stderr_content: str = "",
    timeline_rows: list = None,
    run_name: str = "test_run",
) -> pathlib.Path:
    """Write a synthetic Phase 8 output directory."""
    rd = tmp / "runtime_out"
    rd.mkdir(exist_ok=True)
    _write_metrics(
        rd,
        exit_code=exit_code,
        timed_out=timed_out,
        start_failed=start_failed,
        run_name=run_name,
    )
    (rd / "stdout_tail.txt").write_text(stdout_content, encoding=_ENCODING)
    (rd / "stderr_tail.txt").write_text(stderr_content, encoding=_ENCODING)
    (rd / "stdout.log").write_text(stdout_content, encoding=_ENCODING)
    (rd / "stderr.log").write_text(stderr_content, encoding=_ENCODING)
    _write_timeline(rd, timeline_rows)
    (rd / "runtime_summary.md").write_text("# Runtime End Watcher Report\n", encoding=_ENCODING)
    return rd


def _make_bundle(
    directory: pathlib.Path,
    slices: list = None,
    name: str = "bundle.json",
) -> pathlib.Path:
    """Write a minimal synthetic slicer bundle with optional slices."""
    data: dict = {
        "meta": {
            "bundle_schema_version": "test-v9",
            "deterministic": True,
            "stats": {"bundled": 1, "skipped": 0, "errors": 0},
        },
    }
    if slices is not None:
        data["layer_2_code_intelligence"] = {"slices": slices}
    path = directory / name
    path.write_text(json.dumps(data), encoding=_ENCODING)
    return path


# ===========================================================================
# OOM Forensics Reporter
# ===========================================================================

class TestOOMForensicsReporterCLI(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run(_OOM, ["--help"])
        self.assertEqual(result.returncode, 0)

    def test_help_shows_required_flags(self):
        result = _run(_OOM, ["--help"])
        for flag in ["--runtime-report", "--out", "--manifest", "--bundle", "--config",
                     "--markdown-out"]:
            self.assertIn(flag, result.stdout, f"Missing flag: {flag}")

    def test_missing_runtime_report_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "report.json"
            result = _run(_OOM, [
                "--runtime-report", str(pathlib.Path(td) / "nonexistent"),
                "--out", str(out),
            ])
        self.assertNotEqual(result.returncode, 0)


class TestOOMForensicsReporterAnalysis(unittest.TestCase):
    """Synthetic runtime reports → expected analysis output."""

    def test_successful_exit_no_high_risk(self):
        """exit_code=0, no log patterns → overall_memory_risk should not be HIGH."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), exit_code=0)
            out = pathlib.Path(td) / "report.json"
            result = _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("overall_memory_risk", report)
        self.assertNotEqual(report["overall_memory_risk"], "HIGH")

    def test_nonzero_exit_produces_parseable_output(self):
        """Nonzero exit code → output JSON present and parseable."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), exit_code=1)
            out = pathlib.Path(td) / "report.json"
            result = _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists(), "Output JSON must be created")
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("findings", report)

    def test_timeout_finding_present(self):
        """timed_out=True → a finding about timeout appears."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), timed_out=True)
            out = pathlib.Path(td) / "report.json"
            _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            report = json.loads(out.read_text(encoding=_ENCODING))
        ids = [f["id"] for f in report["findings"]]
        self.assertTrue(
            any("OOM-004" in fid or "timeout" in fid.lower() for fid in ids),
            f"No timeout finding in: {ids}",
        )

    def test_sigkill_exit_code_high_confidence(self):
        """exit_code=137 (SIGKILL) → HIGH confidence finding and overall HIGH risk."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), exit_code=137)
            out = pathlib.Path(td) / "report.json"
            _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(report["overall_memory_risk"], "HIGH")
        high_findings = [f for f in report["findings"] if f.get("confidence") == "HIGH"]
        self.assertTrue(len(high_findings) >= 1, "Expected at least one HIGH-confidence finding")

    def test_memory_error_in_stderr_high_confidence(self):
        """'MemoryError' in stderr → HIGH confidence finding."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(
                pathlib.Path(td),
                exit_code=1,
                stderr_content="Traceback (most recent call last):\n  ...\nMemoryError\n",
            )
            out = pathlib.Path(td) / "report.json"
            _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            report = json.loads(out.read_text(encoding=_ENCODING))
        high = [f for f in report["findings"] if f.get("confidence") == "HIGH"]
        self.assertTrue(len(high) >= 1)
        self.assertEqual(report["overall_memory_risk"], "HIGH")

    def test_missing_optional_bundle_degrades_gracefully(self):
        """Passing a nonexistent --bundle should not crash the tool (exit 0)."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            out = pathlib.Path(td) / "report.json"
            result = _run(_OOM, [
                "--runtime-report", str(rd),
                "--bundle", str(pathlib.Path(td) / "no_such_bundle.json"),
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists(), "Output JSON must be written even without bundle")

    def test_confidence_field_present_in_all_findings(self):
        """Every finding must have a 'confidence' key."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), exit_code=137,
                                    stderr_content="MemoryError")
            out = pathlib.Path(td) / "report.json"
            _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            report = json.loads(out.read_text(encoding=_ENCODING))
        for f in report["findings"]:
            self.assertIn("confidence", f, f"Finding missing 'confidence': {f}")

    def test_uncertainty_notes_present_in_output(self):
        """Top-level 'uncertainty_notes' key must be present."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            out = pathlib.Path(td) / "report.json"
            _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("uncertainty_notes", report)
        self.assertIsInstance(report["uncertainty_notes"], str)
        self.assertGreater(len(report["uncertainty_notes"]), 10)

    def test_suggested_commands_present(self):
        """'suggested_commands' list must be present and non-empty."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), exit_code=137)
            out = pathlib.Path(td) / "report.json"
            _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("suggested_commands", report)
        self.assertGreater(len(report["suggested_commands"]), 0)

    def test_markdown_out_generated(self):
        """--markdown-out produces a non-empty .md file."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            out = pathlib.Path(td) / "report.json"
            md = pathlib.Path(td) / "report.md"
            result = _run(_OOM, [
                "--runtime-report", str(rd),
                "--out", str(out),
                "--markdown-out", str(md),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(md.exists(), "Markdown report must be created")
            md_size = md.stat().st_size
        self.assertGreater(md_size, 0)

    def test_rss_trend_finding_from_timeline(self):
        """Monotonically increasing RSS in timeline.csv → LOW-confidence finding."""
        rss = [10_000_000 + i * 1_000_000 for i in range(5)]
        rows = [{"timestamp": f"2026-04-30T10:00:0{i}", "rss_bytes": str(v)}
                for i, v in enumerate(rss)]
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), timeline_rows=rows)
            out = pathlib.Path(td) / "report.json"
            _run(_OOM, ["--runtime-report", str(rd), "--out", str(out)])
            report = json.loads(out.read_text(encoding=_ENCODING))
        ids = [f["id"] for f in report["findings"]]
        self.assertIn("OOM-030", ids, "Expected OOM-030 (RSS trend) finding")


# ===========================================================================
# Runtime Slice Correlator
# ===========================================================================

class TestRuntimeSliceCorrelatorCLI(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run(_CORR, ["--help"])
        self.assertEqual(result.returncode, 0)

    def test_missing_bundle_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            out = pathlib.Path(td) / "report.json"
            result = _run(_CORR, [
                "--runtime-report", str(rd),
                "--bundle-json", str(pathlib.Path(td) / "no_bundle.json"),
                "--out", str(out),
            ])
        self.assertNotEqual(result.returncode, 0)


class TestRuntimeSliceCorrelatorAnalysis(unittest.TestCase):
    """Correlation analysis with synthetic bundles and runtime evidence."""

    def test_degraded_mode_when_no_slices(self):
        """Bundle with no slices → degraded_mode=True in output."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            bundle = _make_bundle(pathlib.Path(td), slices=[])
            out = pathlib.Path(td) / "report.json"
            result = _run(_CORR, [
                "--runtime-report", str(rd),
                "--bundle-json", str(bundle),
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertTrue(report["degraded_mode"])
        self.assertEqual(report["correlations"], [])

    def test_slices_produce_correlations_list(self):
        """Bundle with slices → correlations list is present and has entries."""
        slices = [
            {"id": "slice_001", "name": "main", "file": "main.py",
             "line_range": [1, 10], "dependencies": ["os", "sys"], "complexity": 2},
            {"id": "slice_002", "name": "loader", "file": "loader.py",
             "line_range": [1, 20], "dependencies": ["json"], "complexity": 5},
        ]
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(
                pathlib.Path(td),
                stderr_content='File "main.py", line 5, in main\nNameError: name x undefined',
            )
            bundle = _make_bundle(pathlib.Path(td), slices=slices)
            out = pathlib.Path(td) / "report.json"
            result = _run(_CORR, [
                "--runtime-report", str(rd),
                "--bundle-json", str(bundle),
                "--out", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertFalse(report["degraded_mode"])
        self.assertGreater(len(report["correlations"]), 0)

    def test_file_in_traceback_maps_to_slice(self):
        """Slice whose file appears in a traceback must rank higher than others."""
        slices = [
            {"id": "hot", "name": "hot_func", "file": "hot_module.py",
             "dependencies": [], "complexity": 3},
            {"id": "cold", "name": "cold_func", "file": "cold_module.py",
             "dependencies": [], "complexity": 3},
        ]
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(
                pathlib.Path(td),
                stderr_content='File "hot_module.py", line 42, in hot_func\nRuntimeError: crash',
            )
            bundle = _make_bundle(pathlib.Path(td), slices=slices)
            out = pathlib.Path(td) / "report.json"
            _run(_CORR, [
                "--runtime-report", str(rd),
                "--bundle-json", str(bundle),
                "--out", str(out),
            ])
            report = json.loads(out.read_text(encoding=_ENCODING))
        top = report["correlations"][0]
        self.assertEqual(top["slice_id"], "hot", f"Expected 'hot' slice at rank 1, got {top}")

    def test_suggested_explain_cmd_present(self):
        """Non-degraded output must have suggested_explain_cmd in each correlation."""
        slices = [
            {"id": "s1", "name": "fn", "file": "app.py",
             "dependencies": ["os"], "complexity": 1},
        ]
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            bundle = _make_bundle(pathlib.Path(td), slices=slices)
            out = pathlib.Path(td) / "report.json"
            _run(_CORR, [
                "--runtime-report", str(rd),
                "--bundle-json", str(bundle),
                "--out", str(out),
            ])
            report = json.loads(out.read_text(encoding=_ENCODING))
        for c in report["correlations"]:
            self.assertIn("suggested_explain_cmd", c, f"Missing key in correlation: {c}")

    def test_uncertainty_notes_present(self):
        """Top-level uncertainty_notes must be present in all outputs."""
        slices = [{"id": "s1", "name": "fn", "file": "a.py",
                   "dependencies": [], "complexity": 1}]
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            bundle = _make_bundle(pathlib.Path(td), slices=slices)
            out = pathlib.Path(td) / "report.json"
            _run(_CORR, [
                "--runtime-report", str(rd),
                "--bundle-json", str(bundle),
                "--out", str(out),
            ])
            report = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("uncertainty_notes", report)


# ===========================================================================
# Runtime Packager
# ===========================================================================

class TestRuntimePackagerCLI(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run(_PACK, ["--help"])
        self.assertEqual(result.returncode, 0)

    def test_missing_runtime_dir_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "pkg.json"
            result = _run(_PACK, [
                "--runtime-dir", str(pathlib.Path(td) / "no_such_dir"),
                "--out", str(out),
            ])
        self.assertNotEqual(result.returncode, 0)


class TestRuntimePackagerAnalysis(unittest.TestCase):
    """Packager output correctness tests."""

    def test_full_runtime_dir_produces_bundle_with_required_keys(self):
        """All expected top-level keys must be present in the output bundle."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            out = pathlib.Path(td) / "pkg.json"
            result = _run(_PACK, ["--runtime-dir", str(rd), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            bundle = json.loads(out.read_text(encoding=_ENCODING))
        for key in [
            "tool_version", "packaged_at", "runtime_dir",
            "metrics_summary", "timeline_summary",
            "stdout_tail_redacted", "stderr_tail_redacted",
            "file_inventory", "redaction_applied", "missing_artefacts",
        ]:
            self.assertIn(key, bundle, f"Missing key: {key}")

    def test_redaction_applied_to_secret_in_stderr_tail(self):
        """A secret matching a SENSITIVE_PATTERN must not appear verbatim in the package."""
        secret = "api_key='abcdefghijklmnopqrstuvwxyz0123456789abc'"
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), stderr_content=secret)
            out = pathlib.Path(td) / "pkg.json"
            _run(_PACK, ["--runtime-dir", str(rd), "--out", str(out)])
            bundle = json.loads(out.read_text(encoding=_ENCODING))
        tail = "\n".join(bundle["stderr_tail_redacted"])
        self.assertNotIn(
            "abcdefghijklmnopqrstuvwxyz0123456789abc",
            tail,
            "Secret value should be redacted in packaged stderr tail",
        )

    def test_missing_artefacts_recorded_gracefully(self):
        """A runtime dir with only runtime_metrics.json must not crash the packager."""
        with tempfile.TemporaryDirectory() as td:
            rd = pathlib.Path(td) / "sparse_run"
            rd.mkdir()
            _write_metrics(rd, exit_code=0)
            out = pathlib.Path(td) / "pkg.json"
            result = _run(_PACK, ["--runtime-dir", str(rd), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            bundle = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIsInstance(bundle["missing_artefacts"], list)
        self.assertGreater(len(bundle["missing_artefacts"]), 0)

    def test_file_inventory_present_and_non_empty(self):
        """file_inventory must be a non-empty list."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            out = pathlib.Path(td) / "pkg.json"
            _run(_PACK, ["--runtime-dir", str(rd), "--out", str(out)])
            bundle = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIsInstance(bundle["file_inventory"], list)
        self.assertGreater(len(bundle["file_inventory"]), 0)

    def test_metrics_summary_preserves_key_fields(self):
        """metrics_summary must carry run_name, exit_code, duration_s."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), exit_code=42, run_name="my_run")
            out = pathlib.Path(td) / "pkg.json"
            _run(_PACK, ["--runtime-dir", str(rd), "--out", str(out)])
            bundle = json.loads(out.read_text(encoding=_ENCODING))
        ms = bundle["metrics_summary"]
        self.assertEqual(ms["run_name"], "my_run")
        self.assertEqual(ms["exit_code"], 42)
        self.assertIn("duration_s", ms)

    def test_markdown_out_generated(self):
        """--markdown-out produces a non-empty .md file."""
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td))
            out = pathlib.Path(td) / "pkg.json"
            md = pathlib.Path(td) / "pkg.md"
            result = _run(_PACK, [
                "--runtime-dir", str(rd),
                "--out", str(out),
                "--markdown-out", str(md),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(md.exists(), "Markdown report must be created")
            md_size = md.stat().st_size
        self.assertGreater(md_size, 0)

    def test_timeline_summary_reflects_row_count(self):
        """timeline_summary.row_count must match the number of data rows written."""
        rows = [{"timestamp": f"2026-04-30T10:00:0{i}", "rss_bytes": "100000"}
                for i in range(4)]
        with tempfile.TemporaryDirectory() as td:
            rd = _make_runtime_dir(pathlib.Path(td), timeline_rows=rows)
            out = pathlib.Path(td) / "pkg.json"
            _run(_PACK, ["--runtime-dir", str(rd), "--out", str(out)])
            bundle = json.loads(out.read_text(encoding=_ENCODING))
        self.assertEqual(bundle["timeline_summary"]["row_count"], 4)


if __name__ == "__main__":
    unittest.main()
