"""Tests for semantic_slicer_v7.0.py — Phase 6 config integration.

Strategy: almost all tests drive the slicer via subprocess so we don't pollute the
test process's import namespace, exactly like the other CLI test files.  A handful
of unit tests import the module directly where subprocess overhead would be wasteful
(flag-parse smoke tests, etc.).
"""

import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

_SLICER = str(pathlib.Path(__file__).resolve().parents[1] / "semantic_slicer_v7.0.py")
_CWD = str(pathlib.Path(__file__).resolve().parents[1])
_EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples" / "configs"


def _run(args: list, cwd: str = _CWD) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _SLICER] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _make_py_file(directory: pathlib.Path, name: str = "hello.py") -> pathlib.Path:
    """Write a minimal Python source file and return its path."""
    f = directory / name
    f.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    return f


def _make_csv_manifest(directory: pathlib.Path, files: list) -> pathlib.Path:
    """Write a file-map CSV with the columns expected by the v6/v7 slicer."""
    manifest = directory / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["root", "rel_path", "abs_path", "ext", "size", "mtime_iso", "sha1"],
        )
        writer.writeheader()
        for f in files:
            writer.writerow({
                "root": str(f.parent),
                "rel_path": f.name,
                "abs_path": str(f),
                "ext": f.suffix,
                "size": f.stat().st_size,
                "mtime_iso": "2026-04-30T00:00:00",
                "sha1": "",
            })
    return manifest


# ===========================================================================
# Basic CLI smoke tests
# ===========================================================================

class TestHelpAndVersion(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)

    def test_help_shows_v6_flags(self):
        result = _run(["--help"])
        for flag in ["--manifest", "--git-diff", "--focus", "--depth",
                     "--append-rules", "--deterministic", "--heatmap", "--explain",
                     "--no-redaction", "--agent-role", "--agent-task", "--workers"]:
            self.assertIn(flag, result.stdout, f"Missing v6 flag: {flag}")

    def test_help_shows_v7_flags(self):
        result = _run(["--help"])
        for flag in ["--config", "--task-profile", "--validate-only",
                     "--allow-path-merge-with-manifest"]:
            self.assertIn(flag, result.stdout, f"Missing v7 flag: {flag}")

    def test_no_files_exits_nonzero(self):
        """Running with no inputs and no git/manifest should fail."""
        result = _run([])
        self.assertNotEqual(result.returncode, 0)


# ===========================================================================
# v6 flag preservation — parse / behaviour
# ===========================================================================

class TestV6FlagPreservation(unittest.TestCase):
    """All critical v6 flags must still parse and produce sensible output."""

    def test_format_json_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.json"
            result = _run([str(py), "--format", "json", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("meta", data)

    def test_format_text_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--format", "text", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())
            self.assertIn("LAYER 1", out.read_text(encoding="utf-8"))

    def test_deterministic_flag_omits_timestamp(self):
        """Two --deterministic runs of the same file must produce identical bundles."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out1 = tmp / "b1.txt"
            out2 = tmp / "b2.txt"
            _run([str(py), "--deterministic", "-o", str(out1), "--base-dir", str(tmp)])
            _run([str(py), "--deterministic", "-o", str(out2), "--base-dir", str(tmp)])
            self.assertTrue(out1.exists(), "First deterministic run did not produce output")
            self.assertEqual(
                out1.read_text(encoding="utf-8"),
                out2.read_text(encoding="utf-8"),
            )

    def test_focus_flag_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--focus", "hello", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_depth_flag_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--depth", "2", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_append_rules_flag_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--append-rules", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PATCH INSTRUCTIONS", out.read_text(encoding="utf-8"))

    def test_heatmap_flag_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--heatmap", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_redaction_flag_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--no-redaction", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_agent_role_and_task_parse(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([
                str(py),
                "--agent-role", "reviewer",
                "--agent-task", "audit",
                "-o", str(out),
                "--deterministic",
                "--base-dir", str(tmp),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_workers_flag_parses(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--workers", "1", "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)


# ===========================================================================
# Manifest mode
# ===========================================================================

class TestManifestMode(unittest.TestCase):
    def test_manifest_csv_produces_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            manifest = _make_csv_manifest(tmp, [py])
            out = tmp / "bundle.txt"
            result = _run(["--manifest", str(manifest), "-o", str(out),
                           "--deterministic", "--base-dir", str(tmp)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())
            self.assertIn("hello", out.read_text(encoding="utf-8"))

    def test_manifest_plus_positional_path_blocks_by_default(self):
        """Combining --manifest and positional paths must exit non-zero without the allow flag."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            manifest = _make_csv_manifest(tmp, [py])
            out = tmp / "bundle.txt"
            result = _run(["--manifest", str(manifest), str(py), "-o", str(out),
                           "--base-dir", str(tmp)])
            self.assertNotEqual(result.returncode, 0)
            error_text = result.stdout + result.stderr
            self.assertIn("--allow-path-merge-with-manifest", error_text)

    def test_manifest_plus_positional_allowed_with_flag(self):
        """--allow-path-merge-with-manifest must permit both sources."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            manifest = _make_csv_manifest(tmp, [py])
            out = tmp / "bundle.txt"
            result = _run([
                "--manifest", str(manifest),
                str(py),
                "--allow-path-merge-with-manifest",
                "-o", str(out),
                "--deterministic",
                "--base-dir", str(tmp),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out.exists())


# ===========================================================================
# --validate-only mode
# ===========================================================================

class TestValidateOnlyMode(unittest.TestCase):
    def test_validate_only_exits_zero_with_no_config(self):
        """--validate-only with no config and no real inputs should report strategy and exit 0."""
        result = _run(["--validate-only"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validate-only", result.stdout)

    def test_validate_only_does_not_produce_bundle(self):
        """No bundle file must be written in validate-only mode."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run([str(py), "--validate-only", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(out.exists(), "validate-only must not write the bundle file")

    def test_validate_only_with_valid_config(self):
        config_path = _EXAMPLES / "python_project.json"
        self.assertTrue(config_path.exists(), "example config must exist for this test")
        result = _run(["--validate-only", "--config", str(config_path), "--task-profile", "default"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python_project", result.stdout)

    def test_validate_only_with_missing_config_exits_nonzero(self):
        result = _run(["--validate-only", "--config", "nonexistent_config.json"])
        self.assertNotEqual(result.returncode, 0)

    def test_validate_only_with_bad_profile_name_exits_nonzero(self):
        config_path = _EXAMPLES / "python_project.json"
        result = _run([
            "--validate-only",
            "--config", str(config_path),
            "--task-profile", "no_such_profile_xyz",
        ])
        self.assertNotEqual(result.returncode, 0)
        error_text = result.stdout + result.stderr
        self.assertIn("no_such_profile_xyz", error_text)


# ===========================================================================
# Config/profile resolution wired into scan
# ===========================================================================

class TestConfigProfileIntegration(unittest.TestCase):
    def _write_config(self, directory: pathlib.Path, profile: dict) -> pathlib.Path:
        cfg = {
            "schema_version": "1.0",
            "profiles": {"test_profile": profile},
        }
        path = directory / "test_config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_profile_exclude_dirs_filters_directory(self):
        """Files inside a profile-excluded dir must not appear in the bundle."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            good = _make_py_file(tmp, "good.py")
            excluded_dir = tmp / "excluded_subdir"
            excluded_dir.mkdir()
            bad = excluded_dir / "bad.py"
            bad.write_text("def bad(): pass\n", encoding="utf-8")

            cfg = self._write_config(tmp, {"exclude_dirs": ["excluded_subdir"]})
            out = tmp / "bundle.txt"
            result = _run([
                str(tmp),
                "--config", str(cfg),
                "--task-profile", "test_profile",
                "-o", str(out),
                "--deterministic",
                "--base-dir", str(tmp),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            content = out.read_text(encoding="utf-8")
            self.assertIn("good.py", content)
            self.assertNotIn("bad.py", content)

    def test_profile_include_exts_whitelist(self):
        """Only files whose extension is in profile include_exts must appear in the bundle."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp, "source.py")
            md = tmp / "readme.md"
            md.write_text("# hello\n", encoding="utf-8")

            cfg = self._write_config(tmp, {"include_exts": [".py"]})
            out = tmp / "bundle.txt"
            result = _run([
                str(tmp),
                "--config", str(cfg),
                "--task-profile", "test_profile",
                "-o", str(out),
                "--deterministic",
                "--base-dir", str(tmp),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            content = out.read_text(encoding="utf-8")
            self.assertIn("source.py", content)
            self.assertNotIn("readme.md", content)

    def test_profile_deterministic_default_applies(self):
        """Profile deterministic:true must produce a DETERMINISTIC_BUILD timestamp."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            cfg = self._write_config(tmp, {"deterministic": True})
            out = tmp / "bundle.txt"
            result = _run([
                str(py),
                "--config", str(cfg),
                "--task-profile", "test_profile",
                "-o", str(out),
                "--base-dir", str(tmp),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DETERMINISTIC_BUILD", out.read_text(encoding="utf-8"))

    def test_profile_max_file_size_skips_oversize_files(self):
        """A very small max_file_size in profile must cause large files to be skipped."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            big = tmp / "big.py"
            big.write_text("x = 1\n" * 200, encoding="utf-8")  # ~1200 bytes
            small = _make_py_file(tmp, "small.py")

            cfg = self._write_config(tmp, {"max_file_size": 50})  # 50 bytes limit
            out = tmp / "bundle.txt"
            result = _run([
                str(tmp),
                "--config", str(cfg),
                "--task-profile", "test_profile",
                "-o", str(out),
                "--deterministic",
                "--base-dir", str(tmp),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            content = out.read_text(encoding="utf-8")
            # big.py should be skipped; small.py (28 bytes) should be included
            self.assertIn("small.py", content)
            self.assertNotIn("big.py", content)

    def test_example_configs_pass_validate_only(self):
        """All three example configs must survive --validate-only without error."""
        for fname in ("python_project.json", "polyglot_runtime.json", "training_pipeline.json"):
            cfg_path = _EXAMPLES / fname
            self.assertTrue(cfg_path.exists(), f"Missing example config: {fname}")
            result = _run([
                "--validate-only",
                "--config", str(cfg_path),
                "--task-profile", "default",
            ])
            self.assertEqual(result.returncode, 0, f"{fname}: {result.stdout}\n{result.stderr}")


# ===========================================================================
# Git-diff mode (graceful handling)
# ===========================================================================

class TestGitDiffMode(unittest.TestCase):
    def test_git_diff_flag_parses_gracefully_outside_repo(self):
        """Outside a git repo --git-diff should warn but not crash with a traceback."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            py = _make_py_file(tmp)
            out = tmp / "bundle.txt"
            result = _run(
                ["--git-diff", "-o", str(out), "--deterministic", "--base-dir", str(tmp)],
                cwd=td,
            )
            # May exit with "No valid files found" (nonzero) if git fails — that's fine.
            # What must NOT happen is an unhandled Python traceback.
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
