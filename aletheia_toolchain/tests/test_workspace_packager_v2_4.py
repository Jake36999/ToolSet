"""tests/test_workspace_packager_v2_4.py — Phase 11.

Tests for workspace_packager_v2.4.py.
Covers: path mode, manifest mode, config excludes, staging dir, redaction,
and legacy file preservation.
"""

import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_CWD = str(pathlib.Path(__file__).parent.parent)
_TOOL = str(pathlib.Path(__file__).parent.parent / "workspace_packager_v2.4.py")
_LEGACY_TOOL = (
    pathlib.Path(__file__).parent.parent.parent / "workspace_packager_v2.3.py"
)
_ENCODING = "utf-8"


def _run(args: list, cwd: str = _CWD) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _TOOL] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _write_file(directory: pathlib.Path, name: str, content: str) -> pathlib.Path:
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=_ENCODING)
    return p


def _write_manifest(
    directory: pathlib.Path, rows: list, name: str = "manifest.csv"
) -> pathlib.Path:
    """Write manifest CSV with all required columns."""
    p = directory / name
    with p.open("w", newline="", encoding=_ENCODING) as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["root", "rel_path", "abs_path", "ext", "size", "mtime_iso", "sha1"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return p


def _write_config(
    directory: pathlib.Path,
    data: dict,
    name: str = "config.json",
) -> pathlib.Path:
    p = directory / name
    p.write_text(json.dumps(data), encoding=_ENCODING)
    return p


def _minimal_config(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "profiles": {},
        "risk_rules": {},
        "architecture_expectations": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# CLI basics
# ---------------------------------------------------------------------------

class TestWorkspacePackagerCLI(unittest.TestCase):

    def test_help_exits_zero(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("workspace_packager_v2.4", result.stdout)

    def test_nonexistent_path_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            result = _run([str(pathlib.Path(td) / "nonexistent")])
        self.assertEqual(result.returncode, 1)

    def test_legacy_file_unchanged(self):
        """workspace_packager_v2.3.py must still exist and be unchanged."""
        self.assertTrue(
            _LEGACY_TOOL.exists(),
            f"Legacy tool missing: {_LEGACY_TOOL}",
        )
        content = _LEGACY_TOOL.read_text(encoding=_ENCODING)
        self.assertIn("v2.3.1", content)


# ---------------------------------------------------------------------------
# Path mode
# ---------------------------------------------------------------------------

class TestWorkspacePackagerPathMode(unittest.TestCase):

    def test_path_mode_bundles_text_files(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "hello.py", "def hello():\n    return 'hello'\n")
            _write_file(src, "notes.md", "# Notes\nSome text.\n")
            out = td_path / "bundle.json"
            result = _run([str(src), "--format", "json", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        self.assertIn("files", data)
        paths = [f["path"] for f in data["files"]]
        self.assertIn("hello.py", paths)
        self.assertIn("notes.md", paths)

    def test_path_mode_skips_ignored_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "main.py", "x = 1\n")
            pycache = src / "__pycache__"
            pycache.mkdir()
            _write_file(pycache, "main.cpython-311.pyc", "binary-ish content")
            out = td_path / "bundle.json"
            result = _run([str(src), "--format", "json", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        paths = [f["path"] for f in data["files"]]
        self.assertNotIn("__pycache__/main.cpython-311.pyc", paths)

    def test_xml_output_format(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "a.py", "a = 1\n")
            out = td_path / "bundle.xml"
            result = _run([str(src), "--format", "xml", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            content = out.read_text(encoding=_ENCODING)
        self.assertIn("<?xml", content)
        self.assertIn("<workspace>", content)

    def test_text_output_format(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "b.py", "b = 2\n")
            out = td_path / "bundle.txt"
            result = _run([str(src), "--format", "text", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            content = out.read_text(encoding=_ENCODING)
        self.assertIn("--- FILE:", content)


# ---------------------------------------------------------------------------
# Manifest mode
# ---------------------------------------------------------------------------

class TestWorkspacePackagerManifestMode(unittest.TestCase):

    def test_manifest_mode_bundles_only_listed_files(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            included = _write_file(td_path, "included.py", "x = 1\n")
            _write_file(td_path, "excluded.py", "y = 2\n")
            manifest = _write_manifest(td_path, [
                {
                    "root": str(td_path),
                    "rel_path": "included.py",
                    "abs_path": str(included),
                    "ext": ".py",
                    "size": "6",
                    "mtime_iso": "2026-01-01T00:00:00",
                    "sha1": "abc123",
                },
            ])
            out = td_path / "bundle.json"
            result = _run([
                str(td_path), "--manifest", str(manifest),
                "--format", "json", "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        paths = [f["path"] for f in data["files"]]
        self.assertIn("included.py", paths)
        self.assertNotIn("excluded.py", paths)

    def test_manifest_mode_uses_rel_path_in_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            f = _write_file(td_path / "sub", "module.py", "pass\n")
            manifest = _write_manifest(td_path, [
                {
                    "root": str(td_path),
                    "rel_path": "sub/module.py",
                    "abs_path": str(f),
                    "ext": ".py",
                    "size": "5",
                    "mtime_iso": "2026-01-01T00:00:00",
                    "sha1": "def456",
                },
            ])
            out = td_path / "bundle.json"
            result = _run([
                str(td_path), "--manifest", str(manifest),
                "--format", "json", "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        paths = [f["path"] for f in data["files"]]
        self.assertIn("sub/module.py", paths)


# ---------------------------------------------------------------------------
# Config mode
# ---------------------------------------------------------------------------

class TestWorkspacePackagerConfigMode(unittest.TestCase):

    def test_config_profile_excludes_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "main.py", "main = True\n")
            gen = src / "generated"
            gen.mkdir()
            _write_file(gen, "output.py", "out = 1\n")
            config = _write_config(td_path, _minimal_config(
                profiles={
                    "ci": {
                        "exclude_dirs": ["generated"],
                        "include_exts": [".py"],
                    }
                }
            ))
            out = td_path / "bundle.json"
            result = _run([
                str(src), "--config", str(config), "--profile", "ci",
                "--format", "json", "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        paths = [f["path"] for f in data["files"]]
        self.assertIn("main.py", paths)
        self.assertNotIn("generated/output.py", paths)

    def test_config_include_exts_filters(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "script.py", "pass\n")
            _write_file(src, "readme.md", "# docs\n")
            config = _write_config(td_path, _minimal_config(
                profiles={
                    "pyonly": {
                        "include_exts": [".py"],
                    }
                }
            ))
            out = td_path / "bundle.json"
            result = _run([
                str(src), "--config", str(config), "--profile", "pyonly",
                "--format", "json", "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        paths = [f["path"] for f in data["files"]]
        self.assertIn("script.py", paths)
        self.assertNotIn("readme.md", paths)


# ---------------------------------------------------------------------------
# Staging directory
# ---------------------------------------------------------------------------

class TestWorkspacePackagerStagingDir(unittest.TestCase):

    def test_staging_dir_receives_output(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "a.py", "a = 1\n")
            staging = td_path / "staging"
            result = _run([
                str(src), "--staging-dir", str(staging),
                "--format", "json",
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            json_files = list(staging.glob("*.json"))
        self.assertTrue(len(json_files) >= 1, "No output file in staging dir")

    def test_staging_dir_not_included_in_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(src, "code.py", "code = True\n")
            staging = src / "staging"  # inside source tree
            out = td_path / "bundle.json"
            result = _run([
                str(src), "--staging-dir", str(staging),
                "--format", "json", "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        paths = [f["path"] for f in data["files"]]
        self.assertNotIn("staging", [p.split("/")[0] for p in paths])


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class TestWorkspacePackagerRedaction(unittest.TestCase):

    def test_api_key_is_redacted(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "src"
            src.mkdir()
            _write_file(
                src,
                "config.py",
                "api_key = 'abcdefghijklmnopqrstuvwxyz0123456789abc'\n",
            )
            out = td_path / "bundle.json"
            result = _run([str(src), "--format", "json", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding=_ENCODING))
        file_content = next(
            f["content"] for f in data["files"] if f["path"] == "config.py"
        )
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789abc", file_content)
        self.assertIn("REDACTED", file_content)


if __name__ == "__main__":
    unittest.main()
