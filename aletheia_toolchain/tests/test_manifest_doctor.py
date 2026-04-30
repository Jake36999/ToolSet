import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


class TestManifestDoctorCLI(unittest.TestCase):
    _DOCTOR = str(pathlib.Path(__file__).resolve().parents[1] / "manifest_doctor.py")
    _COLUMNS = "root,rel_path,abs_path,ext,size,mtime_iso,sha1"

    def _run(self, args):
        return subprocess.run(
            [sys.executable, self._DOCTOR] + args,
            cwd=str(pathlib.Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
        )

    def _write_manifest(self, path: pathlib.Path, rows: list[str]) -> None:
        lines = [self._COLUMNS] + rows
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _make_real_row(self, tmp: pathlib.Path, name: str = "source.py") -> tuple[pathlib.Path, str]:
        """Create a real file and return (file_path, CSV row)."""
        f = tmp / name
        f.write_text("# source\n", encoding="utf-8")
        ext = f.suffix
        size = f.stat().st_size
        row = f".,{name},{f},{ext},{size},2026-04-29T10:00:00,"
        return f, row

    # ----- PASS -----

    def test_clean_manifest_passes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            _, row = self._make_real_row(tmp)
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, [row])
            out = tmp / "report.json"

            result = self._run(["--manifest", str(manifest), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "PASS")
            self.assertIn("Manifest Doctor status: PASS", result.stdout)

    # ----- WARN -----

    def test_venv_pollution_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            venv_dir = tmp / ".venv" / "lib"
            venv_dir.mkdir(parents=True)
            f = venv_dir / "util.py"
            f.write_text("# venv\n", encoding="utf-8")
            row = f".,{f.relative_to(tmp)},{f},.py,10,2026-04-29T10:00:00,"
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, [str(row)])
            out = tmp / "report.json"

            result = self._run(["--manifest", str(manifest), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "WARN")
            self.assertGreater(data["summary"]["suspicious_paths"], 0)

    def test_bundle_artifact_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            # Real file whose rel_path contains _bundle_
            f = tmp / "project_bundle_20260429.py"
            f.write_text("# bundle\n", encoding="utf-8")
            row = f".,project_bundle_20260429.py,{f},.py,10,2026-04-29T10:00:00,"
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, [str(row)])
            out = tmp / "report.json"

            result = self._run(["--manifest", str(manifest), "--out", str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "WARN")
            self.assertGreater(data["summary"]["bundle_artifacts"], 0)

    def test_oversize_file_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            _, row = self._make_real_row(tmp)
            # Override size in the row to exceed the threshold
            parts = row.split(",")
            parts[4] = "2000000"  # 2 MB in size column
            row = ",".join(parts)
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, [row])
            out = tmp / "report.json"

            result = self._run([
                "--manifest", str(manifest), "--out", str(out),
                "--max-file-size", "1000000",
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "WARN")
            self.assertGreater(data["summary"]["oversize_files"], 0)

    def test_soft_row_threshold_warns(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rows = []
            for i in range(5):
                _, row = self._make_real_row(tmp, f"file{i}.py")
                rows.append(row)
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, rows)
            out = tmp / "report.json"

            result = self._run([
                "--manifest", str(manifest), "--out", str(out),
                "--max-rows-soft", "3",
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "WARN")
            self.assertTrue(data["summary"]["rows_exceeded_soft"])

    # ----- BLOCK -----

    def test_missing_required_path_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            _, row = self._make_real_row(tmp)
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, [row])
            out = tmp / "report.json"

            result = self._run([
                "--manifest", str(manifest), "--out", str(out),
                "--required-path", "README.md",
            ])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "BLOCK")
            self.assertIn("README.md", data["findings"]["missing_required_paths"])

    def test_missing_required_ext_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            _, row = self._make_real_row(tmp, "source.py")
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, [row])
            out = tmp / "report.json"

            result = self._run([
                "--manifest", str(manifest), "--out", str(out),
                "--required-ext", ".md",
            ])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "BLOCK")
            self.assertIn(".md", data["findings"]["missing_required_exts"])

    def test_hard_row_threshold_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rows = []
            for i in range(5):
                _, row = self._make_real_row(tmp, f"file{i}.py")
                rows.append(row)
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, rows)
            out = tmp / "report.json"

            result = self._run([
                "--manifest", str(manifest), "--out", str(out),
                "--max-rows-hard", "3",
            ])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "BLOCK")
            self.assertTrue(data["summary"]["rows_exceeded_hard"])

    def test_missing_required_columns_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            manifest = tmp / "manifest.csv"
            # Malformed: missing required columns
            manifest.write_text("path,size\n./foo.py,100\n", encoding="utf-8")
            out = tmp / "report.json"

            result = self._run(["--manifest", str(manifest), "--out", str(out)])
            self.assertEqual(result.returncode, 2, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "BLOCK")
            self.assertIn("error", data)

    # ----- Markdown output -----

    def test_markdown_out_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            _, row = self._make_real_row(tmp)
            manifest = tmp / "manifest.csv"
            self._write_manifest(manifest, [row])
            out = tmp / "report.json"
            md_out = tmp / "report.md"

            result = self._run([
                "--manifest", str(manifest),
                "--out", str(out),
                "--markdown-out", str(md_out),
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(md_out.exists(), "Markdown report file was not created")
            content = md_out.read_text(encoding="utf-8")
            self.assertIn("# Manifest Doctor Report", content)
            self.assertIn("PASS", content)


if __name__ == "__main__":
    unittest.main()
