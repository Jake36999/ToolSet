"""Tests for runtime_end_watcher.py — Phase 8.

Strategy: every test drives the watcher via subprocess so the test process's
environment (imports, cwd, etc.) is never polluted.  Subprocess commands
executed *by the watcher* use sys.executable + -c "..." for portability
across platforms.

NOTE: --cmd must always be the last argument in _run() calls because the
      watcher uses nargs=REMAINDER, which consumes every token after --cmd.
"""

import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_WATCHER = str(pathlib.Path(__file__).resolve().parents[1] / "runtime_end_watcher.py")
_CWD = str(pathlib.Path(__file__).resolve().parents[1])

# Expected artefact names (the five report files + two raw logs).
_REPORT_FILES = [
    "runtime_metrics.json",
    "timeline.csv",
    "stdout_tail.txt",
    "stderr_tail.txt",
    "runtime_summary.md",
]
_ALL_FILES = _REPORT_FILES + ["stdout.log", "stderr.log"]


def _run(watcher_args: list, cwd: str = _CWD) -> subprocess.CompletedProcess:
    """Invoke the watcher and return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, _WATCHER] + watcher_args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _load_metrics(out_dir: pathlib.Path) -> dict:
    """Parse runtime_metrics.json from *out_dir*."""
    path = out_dir / "runtime_metrics.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# Basic CLI
# ===========================================================================

class TestCLI(unittest.TestCase):
    """--help and missing-arg guard."""

    def test_help_exits_zero(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)

    def test_help_shows_all_required_flags(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)
        for flag in [
            "--name",
            "--cmd",
            "--sample-seconds",
            "--timeout",
            "--out-dir",
            "--metrics-mode",
            "--python-faultevidence",
            "--python-tracemalloc",
        ]:
            self.assertIn(flag, result.stdout, f"Flag missing from --help: {flag}")

    def test_missing_cmd_exits_nonzero(self):
        result = _run(["--out-dir", "ignored"])
        self.assertNotEqual(result.returncode, 0)


# ===========================================================================
# Successful command
# ===========================================================================

class TestSuccessfulCommand(unittest.TestCase):
    """Watcher exits 0 and produces all artefacts when command succeeds."""

    def _run_success(self, tmp: pathlib.Path) -> subprocess.CompletedProcess:
        out_dir = tmp / "out"
        return _run([
            "--out-dir", str(out_dir),
            "--metrics-mode", "none",
            "--cmd", sys.executable, "-c", "print('hello watcher')",
        ]), out_dir

    def test_exit_code_mirrors_zero(self):
        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run_success(pathlib.Path(td))
        self.assertEqual(result.returncode, 0)

    def test_all_report_files_present(self):
        with tempfile.TemporaryDirectory() as td:
            _, out_dir = self._run_success(pathlib.Path(td))
            for fname in _ALL_FILES:
                self.assertTrue(
                    (out_dir / fname).exists(),
                    f"Missing artefact: {fname}",
                )

    def test_metrics_json_records_exit_zero_and_no_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            _, out_dir = self._run_success(pathlib.Path(td))
            metrics = _load_metrics(out_dir)
        self.assertEqual(metrics["exit_code"], 0)
        self.assertFalse(metrics["timed_out"])
        self.assertFalse(metrics["start_failed"])

    def test_python_faultevidence_flag_accepted(self):
        """--python-faultevidence must not cause an error."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            result = _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--python-faultevidence",
                "--cmd", sys.executable, "-c", "import os; print(os.environ.get('PYTHONFAULTHANDLER','0'))",
            ])
        self.assertEqual(result.returncode, 0)

    def test_python_tracemalloc_flag_accepted(self):
        """--python-tracemalloc must not cause an error."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            result = _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--python-tracemalloc",
                "--cmd", sys.executable, "-c", "print('tracemalloc ok')",
            ])
        self.assertEqual(result.returncode, 0)


# ===========================================================================
# Failing command
# ===========================================================================

class TestFailingCommand(unittest.TestCase):
    """Watcher mirrors nonzero exit code and still produces a report."""

    def _run_failure(self, tmp: pathlib.Path, code: int = 42):
        out_dir = tmp / "out"
        result = _run([
            "--out-dir", str(out_dir),
            "--metrics-mode", "none",
            "--cmd", sys.executable, "-c", f"raise SystemExit({code})",
        ])
        return result, out_dir

    def test_exit_code_mirrors_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run_failure(pathlib.Path(td), code=42)
        self.assertEqual(result.returncode, 42)

    def test_report_files_present_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            _, out_dir = self._run_failure(pathlib.Path(td))
            for fname in _REPORT_FILES:
                self.assertTrue(
                    (out_dir / fname).exists(),
                    f"Missing artefact on failure: {fname}",
                )

    def test_metrics_json_records_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            _, out_dir = self._run_failure(pathlib.Path(td), code=7)
            metrics = _load_metrics(out_dir)
        self.assertEqual(metrics["exit_code"], 7)
        self.assertFalse(metrics["timed_out"])


# ===========================================================================
# Timeout command
# ===========================================================================

class TestTimeoutCommand(unittest.TestCase):
    """Watcher kills the process on --timeout and exits 1."""

    _SLEEP_CMD = [sys.executable, "-c", "import time; time.sleep(60)"]

    def _run_timeout(self, tmp: pathlib.Path) -> tuple:
        out_dir = tmp / "out"
        result = _run([
            "--timeout", "2",
            "--out-dir", str(out_dir),
            "--metrics-mode", "none",
            "--cmd", *self._SLEEP_CMD,
        ])
        return result, out_dir

    def test_timeout_watcher_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run_timeout(pathlib.Path(td))
        self.assertEqual(result.returncode, 1)

    def test_timeout_metrics_json_timed_out_true(self):
        with tempfile.TemporaryDirectory() as td:
            _, out_dir = self._run_timeout(pathlib.Path(td))
            metrics = _load_metrics(out_dir)
        self.assertTrue(metrics["timed_out"])

    def test_timeout_summary_contains_timeout_status(self):
        with tempfile.TemporaryDirectory() as td:
            _, out_dir = self._run_timeout(pathlib.Path(td))
            summary = (out_dir / "runtime_summary.md").read_text(encoding="utf-8")
        self.assertIn("TIMEOUT", summary)


# ===========================================================================
# Large output
# ===========================================================================

class TestLargeOutput(unittest.TestCase):
    """Large stdout must be streamed without OOM or deadlock."""

    def test_large_output_completes_exit_zero(self):
        # 10 000 lines of 100 chars each ≈ 1 MB.  -u disables subprocess
        # Python stdout buffering so the drain thread sees output promptly.
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            result = _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--cmd",
                sys.executable, "-u", "-c",
                "for _ in range(10000): print('x' * 100)",
            ])
        self.assertEqual(result.returncode, 0)

    def test_large_output_stdout_log_has_content(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--cmd",
                sys.executable, "-u", "-c",
                "for _ in range(10000): print('x' * 100)",
            ])
            self.assertGreater((out_dir / "stdout.log").stat().st_size, 0)


# ===========================================================================
# Stdout / stderr log streaming
# ===========================================================================

class TestLogStreaming(unittest.TestCase):
    """stdout.log and stderr.log capture the respective streams."""

    def test_stdout_log_captures_stdout(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--cmd",
                sys.executable, "-c", "print('stdout_marker_abc')",
            ])
            content = (out_dir / "stdout.log").read_text(encoding="utf-8")
        self.assertIn("stdout_marker_abc", content)

    def test_stderr_log_captures_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--cmd",
                sys.executable, "-c",
                "import sys; sys.stderr.write('stderr_marker_xyz\\n')",
            ])
            content = (out_dir / "stderr.log").read_text(encoding="utf-8")
        self.assertIn("stderr_marker_xyz", content)

    def test_stdout_tail_contains_recent_lines(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--cmd",
                sys.executable, "-c", "print('tail_check_line')",
            ])
            tail = (out_dir / "stdout_tail.txt").read_text(encoding="utf-8")
        self.assertIn("tail_check_line", tail)


# ===========================================================================
# Metrics sampling
# ===========================================================================

class TestMetricsSampling(unittest.TestCase):
    """timeline.csv is always present; sampling threads produce rows."""

    def test_timeline_csv_header_always_present(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--cmd", sys.executable, "-c", "pass",
            ])
            text = (out_dir / "timeline.csv").read_text(encoding="utf-8")
        # At minimum the header row must be present.
        self.assertIn("timestamp", text)

    def test_basic_mode_produces_timeline_with_rows(self):
        """basic mode with fast sampling and a short sleep should yield >=1 row."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "basic",
                "--sample-seconds", "0.1",
                "--cmd", sys.executable, "-c", "import time; time.sleep(0.5)",
            ])
            with (out_dir / "timeline.csv").open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        self.assertGreaterEqual(len(rows), 1)

    def test_metrics_json_sample_count_matches_csv_rows(self):
        """sample_count in runtime_metrics.json must equal data rows in timeline.csv."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "out"
            _run([
                "--out-dir", str(out_dir),
                "--metrics-mode", "basic",
                "--sample-seconds", "0.1",
                "--cmd", sys.executable, "-c", "import time; time.sleep(0.5)",
            ])
            with (out_dir / "timeline.csv").open(encoding="utf-8") as fh:
                csv_rows = list(csv.DictReader(fh))
            metrics = _load_metrics(out_dir)
        self.assertEqual(metrics["sample_count"], len(csv_rows))


# ===========================================================================
# Start failure
# ===========================================================================

class TestStartFailure(unittest.TestCase):
    """Nonexistent or unlaunchable commands are handled without a traceback."""

    _BAD_CMD = "this_command_does_not_exist_xyzzy_phase8"

    def _run_bad(self, tmp: pathlib.Path):
        out_dir = tmp / "out"
        result = _run([
            "--out-dir", str(out_dir),
            "--metrics-mode", "none",
            "--cmd", self._BAD_CMD,
        ])
        return result, out_dir

    def test_invalid_command_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run_bad(pathlib.Path(td))
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_command_no_unhandled_traceback(self):
        """Watcher stderr must contain a clean error message, not a Python traceback."""
        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run_bad(pathlib.Path(td))
        self.assertNotIn("Traceback (most recent call last)", result.stderr)
        # A user-facing ERROR message must appear instead.
        self.assertIn("ERROR", result.stderr)

    def test_invalid_command_writes_report_with_start_failed_true(self):
        with tempfile.TemporaryDirectory() as td:
            _, out_dir = self._run_bad(pathlib.Path(td))
            self.assertTrue(
                (out_dir / "runtime_metrics.json").exists(),
                "runtime_metrics.json must be written even on start failure",
            )
            metrics = _load_metrics(out_dir)
        self.assertTrue(metrics["start_failed"])
        self.assertIsNotNone(metrics["start_error"])
        self.assertGreater(len(metrics["start_error"]), 0)


if __name__ == "__main__":
    unittest.main()
