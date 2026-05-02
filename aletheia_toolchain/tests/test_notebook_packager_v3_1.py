"""tests/test_notebook_packager_v3_1.py — Phase 11.

Tests for notebook_packager_v3.1.py.
Covers: path mode, manifest mode, config excludes, staging dir,
requirements-mode (auto/off/required), no generated artifact leakage,
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
_TOOL = str(pathlib.Path(__file__).parent.parent / "notebook_packager_v3.1.py")
_LEGACY_TOOL = pathlib.Path(__file__).parent.parent.parent / "notebook_packager.py"
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
    directory: pathlib.Path, data: dict, name: str = "config.json"
) -> pathlib.Path:
    p = directory / name
    p.write_text(json.dumps(data), encoding=_ENCODING)
    return p


def _load_notebook(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding=_ENCODING))


def _all_cell_sources(nb: dict) -> str:
    parts = []
    for cell in nb.get("cells", []):
        parts.append("".join(cell.get("source", [])))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI basics
# ---------------------------------------------------------------------------

class TestNotebookPackagerCLI(unittest.TestCase):

    def test_help_exits_zero(self):
        result = _run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("notebook_packager_v3.1", result.stdout)

    def test_nonexistent_path_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            result = _run([str(pathlib.Path(td) / "nonexistent")])
        self.assertEqual(result.returncode, 1)

    def test_legacy_file_unchanged(self):
        """notebook_packager.py (v3) must still exist and be unchanged."""
        self.assertTrue(
            _LEGACY_TOOL.exists(),
            f"Legacy tool missing: {_LEGACY_TOOL}",
        )
        content = _LEGACY_TOOL.read_text(encoding=_ENCODING)
        self.assertIn("Self-Extracting Colab Bundle", content)


# ---------------------------------------------------------------------------
# Path mode
# ---------------------------------------------------------------------------

class TestNotebookPackagerPathMode(unittest.TestCase):

    def test_path_mode_generates_valid_notebook(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "myproject"
            src.mkdir()
            _write_file(src, "main.py", "print('hello')\n")
            _write_file(src, "utils.py", "def util(): pass\n")
            out = td_path / "out.ipynb"
            result = _run([str(src), "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        self.assertEqual(nb["nbformat"], 4)
        self.assertIn("cells", nb)
        sources = _all_cell_sources(nb)
        self.assertIn("%%writefile main.py", sources)
        self.assertIn("%%writefile utils.py", sources)

    def test_path_mode_skips_ignored_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "app.py", "app = True\n")
            pycache = src / "__pycache__"
            pycache.mkdir()
            _write_file(pycache, "app.cpython-311.pyc", "bytecode")
            out = td_path / "out.ipynb"
            result = _run([str(src), "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertNotIn("__pycache__", sources)

    def test_path_mode_creates_directory_setup_cell(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            subdir = src / "models"
            subdir.mkdir()
            _write_file(subdir, "model.py", "class M: pass\n")
            out = td_path / "out.ipynb"
            _run([str(src), "-o", str(out)])
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertIn("os.makedirs", sources)
        self.assertIn("models", sources)


# ---------------------------------------------------------------------------
# Manifest mode
# ---------------------------------------------------------------------------

class TestNotebookPackagerManifestMode(unittest.TestCase):

    def test_manifest_mode_bundles_only_listed_files(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            included = _write_file(td_path, "include.py", "pass\n")
            _write_file(td_path, "exclude.py", "nope\n")
            manifest = _write_manifest(td_path, [
                {
                    "root": str(td_path),
                    "rel_path": "include.py",
                    "abs_path": str(included),
                    "ext": ".py",
                    "size": "5",
                    "mtime_iso": "2026-01-01T00:00:00",
                    "sha1": "aaa",
                },
            ])
            out = td_path / "out.ipynb"
            result = _run([
                str(td_path), "--manifest", str(manifest), "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertIn("%%writefile include.py", sources)
        self.assertNotIn("%%writefile exclude.py", sources)


# ---------------------------------------------------------------------------
# Config mode
# ---------------------------------------------------------------------------

class TestNotebookPackagerConfigMode(unittest.TestCase):

    def test_config_profile_excludes_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "app.py", "run = True\n")
            gen = src / "generated"
            gen.mkdir()
            _write_file(gen, "auto.py", "auto = 1\n")
            config = _write_config(td_path, {
                "schema_version": "1.0",
                "profiles": {
                    "clean": {
                        "exclude_dirs": ["generated"],
                        "include_exts": [".py"],
                    }
                },
                "risk_rules": {},
            })
            out = td_path / "out.ipynb"
            result = _run([
                str(src), "--config", str(config), "--profile", "clean",
                "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertIn("%%writefile app.py", sources)
        self.assertNotIn("%%writefile generated/auto.py", sources)


# ---------------------------------------------------------------------------
# Staging directory
# ---------------------------------------------------------------------------

class TestNotebookPackagerStagingDir(unittest.TestCase):

    def test_staging_dir_receives_output(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "x.py", "x = 1\n")
            staging = td_path / "out_staging"
            result = _run([str(src), "--staging-dir", str(staging)])
            self.assertEqual(result.returncode, 0, result.stderr)
            ipynb_files = list(staging.glob("*.ipynb"))
        self.assertTrue(len(ipynb_files) >= 1, "No .ipynb in staging dir")

    def test_staging_dir_not_packaged(self):
        """The staging dir itself must not appear as a %%writefile path."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "code.py", "code = True\n")
            staging = src / "stage"  # nested inside source
            out = td_path / "out.ipynb"
            result = _run([
                str(src), "--staging-dir", str(staging), "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertNotIn("%%writefile stage/", sources)


# ---------------------------------------------------------------------------
# Requirements mode
# ---------------------------------------------------------------------------

class TestNotebookPackagerRequirementsMode(unittest.TestCase):

    def test_requirements_mode_auto_installs_when_present(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "main.py", "pass\n")
            _write_file(src, "requirements.txt", "numpy>=1.0\n")
            out = td_path / "out.ipynb"
            result = _run([str(src), "--requirements-mode", "auto", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertIn("pip install -r requirements.txt", sources)

    def test_requirements_mode_auto_no_install_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "main.py", "pass\n")
            out = td_path / "out.ipynb"
            result = _run([str(src), "--requirements-mode", "auto", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertNotIn("pip install", sources)

    def test_requirements_mode_off_suppresses_install(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "main.py", "pass\n")
            _write_file(src, "requirements.txt", "flask\n")
            out = td_path / "out.ipynb"
            result = _run([str(src), "--requirements-mode", "off", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertNotIn("pip install", sources)

    def test_requirements_mode_required_exits_1_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "main.py", "pass\n")
            out = td_path / "out.ipynb"
            result = _run([str(src), "--requirements-mode", "required", "-o", str(out)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("requirements", result.stderr.lower())

    def test_requirements_mode_required_succeeds_when_present(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "main.py", "pass\n")
            _write_file(src, "requirements.txt", "requests\n")
            out = td_path / "out.ipynb"
            result = _run([str(src), "--requirements-mode", "required", "-o", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertIn("pip install -r requirements.txt", sources)


# ---------------------------------------------------------------------------
# No generated artifacts packaged
# ---------------------------------------------------------------------------

class TestNotebookPackagerNoGeneratedArtifacts(unittest.TestCase):

    def test_generated_dirs_excluded_by_config(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            src = td_path / "proj"
            src.mkdir()
            _write_file(src, "run.py", "run = 1\n")
            bundles = src / "bundles"
            bundles.mkdir()
            _write_file(bundles, "output_bundle.py", "bundle = True\n")
            config = _write_config(td_path, {
                "schema_version": "1.0",
                "profiles": {
                    "ci": {"exclude_dirs": ["bundles"], "include_exts": [".py"]},
                },
                "risk_rules": {},
            })
            out = td_path / "out.ipynb"
            result = _run([
                str(src), "--config", str(config), "--profile", "ci",
                "-o", str(out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            nb = _load_notebook(out)
        sources = _all_cell_sources(nb)
        self.assertNotIn("output_bundle.py", sources)
        self.assertIn("%%writefile run.py", sources)


if __name__ == "__main__":
    unittest.main()
