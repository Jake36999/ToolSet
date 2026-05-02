"""tests/test_e2e_pipeline.py — Phase 12.

End-to-end pipeline integration tests.  Each test wires two or more
tools together and asserts on the combined output, mirroring the
workflow a developer would run manually.

Tests here intentionally invoke real subprocesses (no mocking) so that
argument passing, exit codes, and file I/O are all exercised together.
"""

import csv
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

_TOOLCHAIN = pathlib.Path(__file__).parent.parent
_DOCTOR = str(_TOOLCHAIN / "manifest_doctor.py")
_GATEKEEPER = str(_TOOLCHAIN / "pipeline_gatekeeper.py")
_WATCHER = str(_TOOLCHAIN / "runtime_end_watcher.py")
_ARTIFACTS_DIR = _TOOLCHAIN / "test_artifacts"

_ENCODING = "utf-8"

_WATCHER_ARTIFACTS = [
    "stdout.log",
    "stderr.log",
    "runtime_metrics.json",
    "timeline.csv",
    "stdout_tail.txt",
    "stderr_tail.txt",
    "runtime_summary.md",
]


def _run(cmd: list, cwd: str = str(_TOOLCHAIN)) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _write_csv(path: pathlib.Path, rows: list) -> None:
    with path.open("w", newline="", encoding=_ENCODING) as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["root", "rel_path", "abs_path", "ext", "size", "mtime_iso", "sha1"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_file(parent: pathlib.Path, name: str, content: str) -> pathlib.Path:
    p = parent / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=_ENCODING)
    return p


# ---------------------------------------------------------------------------
# Full pipeline PASS
# ---------------------------------------------------------------------------

class TestE2EFullPipelinePass(unittest.TestCase):

    def test_healthy_manifest_produces_gatekeeper_pass(self):
        """manifest_doctor PASS report → pipeline_gatekeeper PASS."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = _write_file(td_path, "app.py", "app = True\n")
            manifest = td_path / "manifest.csv"
            _write_csv(manifest, [
                {
                    "root": str(td_path),
                    "rel_path": "app.py",
                    "abs_path": str(src),
                    "ext": ".py",
                    "size": "12",
                    "mtime_iso": "2026-04-30T00:00:00",
                    "sha1": "aabbcc112233445566778899aabbcc1122334455",
                },
            ])
            doctor_out = str(td_path / "doctor.json")
            _run([sys.executable, _DOCTOR, "--manifest", str(manifest), "--out", doctor_out])

            gatekeeper_out = str(td_path / "gate.json")
            gate_result = _run([
                sys.executable, _GATEKEEPER,
                "--manifest-report", doctor_out,
                "--out", gatekeeper_out,
            ])
            gate_report = json.loads(pathlib.Path(gatekeeper_out).read_text(encoding=_ENCODING))

        self.assertIn(gate_result.returncode, (0,), gate_result.stderr)
        self.assertEqual(gate_report["status"], "PASS")
        self.assertEqual(gate_report["failed_gates"], [])


# ---------------------------------------------------------------------------
# Full pipeline BLOCK
# ---------------------------------------------------------------------------

class TestE2EFullPipelineBlock(unittest.TestCase):

    def test_block_manifest_produces_gatekeeper_block(self):
        """manifest_doctor BLOCK report (missing file) → pipeline_gatekeeper BLOCK."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            manifest = td_path / "manifest.csv"
            _write_csv(manifest, [
                {
                    "root": str(td_path),
                    "rel_path": "nonexistent.py",
                    "abs_path": str(td_path / "nonexistent.py"),
                    "ext": ".py",
                    "size": "0",
                    "mtime_iso": "2026-04-30T00:00:00",
                    "sha1": "",
                },
            ])
            doctor_out = str(td_path / "doctor.json")
            _run([sys.executable, _DOCTOR, "--manifest", str(manifest), "--out", doctor_out])
            doctor_report = json.loads(pathlib.Path(doctor_out).read_text(encoding=_ENCODING))

            gatekeeper_out = str(td_path / "gate.json")
            gate_result = _run([
                sys.executable, _GATEKEEPER,
                "--manifest-report", doctor_out,
                "--out", gatekeeper_out,
            ])
            gate_report = json.loads(pathlib.Path(gatekeeper_out).read_text(encoding=_ENCODING))

        self.assertEqual(doctor_report["status"], "BLOCK")
        self.assertEqual(gate_result.returncode, 2, "Expected gatekeeper exit 2 for BLOCK")
        self.assertEqual(gate_report["status"], "BLOCK")
        failed_gate_ids = [g["gate_id"] for g in gate_report.get("failed_gates", [])]
        self.assertIn("manifest", failed_gate_ids)


# ---------------------------------------------------------------------------
# Runtime watcher bounded e2e
# ---------------------------------------------------------------------------

class TestE2EWatcherArtifacts(unittest.TestCase):

    def test_watcher_produces_all_seven_artifacts(self):
        """Run a short Python command under the watcher; verify all 7 artifacts exist."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "watcher_out"
            result = _run([
                sys.executable, _WATCHER,
                "--name", "e2e_test",
                "--out-dir", str(out_dir),
                "--metrics-mode", "none",
                "--cmd", sys.executable, "-c", "print('hello')",
            ])
            artifacts = {name: (out_dir / name).exists() for name in _WATCHER_ARTIFACTS}

        self.assertIn(result.returncode, (0, 1), result.stderr)
        for name, present in artifacts.items():
            self.assertTrue(present, f"Missing watcher artifact: {name}")

    def test_watcher_timeout_exits_nonzero(self):
        """Process that sleeps longer than --timeout should exit 1."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = pathlib.Path(td) / "watcher_timeout"
            result = _run([
                sys.executable, _WATCHER,
                "--name", "timeout_test",
                "--out-dir", str(out_dir),
                "--timeout", "1",
                "--metrics-mode", "none",
                "--cmd", sys.executable, "-c", "import time; time.sleep(30)",
            ])
        self.assertEqual(result.returncode, 1, "Expected exit 1 on timeout")


# ---------------------------------------------------------------------------
# Golden snapshot: deterministic fields
# ---------------------------------------------------------------------------

class TestE2EGoldenSnapshot(unittest.TestCase):

    def test_doctor_output_has_required_keys(self):
        """manifest_doctor output always contains a stable set of top-level keys."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = _write_file(td_path, "mod.py", "pass\n")
            manifest = td_path / "manifest.csv"
            _write_csv(manifest, [
                {
                    "root": str(td_path),
                    "rel_path": "mod.py",
                    "abs_path": str(src),
                    "ext": ".py",
                    "size": "5",
                    "mtime_iso": "2026-04-30T00:00:00",
                    "sha1": "aabbcc",
                },
            ])
            out = str(td_path / "doctor.json")
            result = _run([sys.executable, _DOCTOR, "--manifest", str(manifest), "--out", out])
            report = json.loads(pathlib.Path(out).read_text(encoding=_ENCODING))

        self.assertEqual(result.returncode, 0, result.stderr)
        for key in ("status", "manifest_path", "summary", "findings", "recommended_action"):
            self.assertIn(key, report, f"Missing expected key: {key}")
        for key in ("row_count", "missing_files", "suspicious_paths", "bundle_artifacts"):
            self.assertIn(key, report["summary"], f"Missing summary key: {key}")


# ---------------------------------------------------------------------------
# Failure artifact: gatekeeper BLOCK report copied to test_artifacts/
# ---------------------------------------------------------------------------

class TestE2EFailureArtifact(unittest.TestCase):

    def test_block_report_saved_to_test_artifacts(self):
        """When gatekeeper BLOCKs, save the report to test_artifacts/ for CI upload."""
        _ARTIFACTS_DIR.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            manifest = td_path / "manifest.csv"
            _write_csv(manifest, [
                {
                    "root": str(td_path),
                    "rel_path": "gone.py",
                    "abs_path": str(td_path / "gone.py"),
                    "ext": ".py",
                    "size": "0",
                    "mtime_iso": "2026-04-30T00:00:00",
                    "sha1": "",
                },
            ])
            doctor_out = str(td_path / "doctor.json")
            _run([sys.executable, _DOCTOR, "--manifest", str(manifest), "--out", doctor_out])

            artifact_out = str(_ARTIFACTS_DIR / "e2e_block_gatekeeper.json")
            gate_result = _run([
                sys.executable, _GATEKEEPER,
                "--manifest-report", doctor_out,
                "--out", artifact_out,
            ])
            report_text = pathlib.Path(artifact_out).read_text(encoding=_ENCODING)
            report = json.loads(report_text)

        self.assertEqual(gate_result.returncode, 2, "Expected BLOCK exit 2")
        self.assertEqual(report["status"], "BLOCK")
        self.assertTrue(
            pathlib.Path(artifact_out).exists(),
            "Failure artifact not written to test_artifacts/",
        )


if __name__ == "__main__":
    unittest.main()
