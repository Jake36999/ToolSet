import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

class TestCreateFileMapV3CLI(unittest.TestCase):
    def run_cli(self, args, cwd=None):
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "create_file_map_v3.py")] + args,
            cwd=cwd or str(pathlib.Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
        )
        return result

    def test_v2_csv_schema_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            file_path = root / "hello.py"
            file_path.write_text("print('hello')\n", encoding="utf-8")

            out_csv = pathlib.Path(temp_dir) / "file_map.csv"
            result = self.run_cli(["--roots", str(root), "--out", str(out_csv)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out_csv.exists())

            with out_csv.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, ["root", "rel_path", "abs_path", "ext", "size", "mtime_iso", "sha1"])
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rel_path"], "hello.py")

    def test_out_alias_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            (root / "hello.py").write_text("print('hello')\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli(["--roots", str(root), "-o", str(out_csv)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(out_csv.exists())
            self.assertIn("Using -o as an alias for --out", result.stderr)

    def test_profile_filters_by_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            (root / "keep.py").write_text("print('keep')\n", encoding="utf-8")
            (root / "skip.txt").write_text("skip me\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli(["--roots", str(root), "--out", str(out_csv), "--profile", "python"])
            self.assertEqual(result.returncode, 0, result.stderr)
            with out_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rel_path"], "keep.py")

    def test_health_report_and_pollution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            repo_path = root / "node_modules"
            repo_path.mkdir(parents=True)
            (repo_path / "ignore.py").write_text("print('ignored')\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"
            health_report = pathlib.Path(temp_dir) / "health.json"

            result = self.run_cli([
                "--roots",
                str(root),
                "--out",
                str(out_csv),
                "--health-report",
                str(health_report),
                "--exclude-dirs",
                ".git,.venv,venv,__pycache__,.pytest_cache,.idea,.vscode,dist,build,.next",
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(health_report.exists())
            data = json.loads(health_report.read_text(encoding="utf-8"))
            self.assertEqual(data["pollution_count"], 1)
            self.assertEqual(data["status"], "WARN")

    def test_fail_on_pollution_exit_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            repo_path = root / "node_modules"
            repo_path.mkdir(parents=True)
            (repo_path / "ignore.py").write_text("print('ignored')\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"
            health_report = pathlib.Path(temp_dir) / "health.json"

            result = self.run_cli([
                "--roots",
                str(root),
                "--out",
                str(out_csv),
                "--health-report",
                str(health_report),
                "--fail-on-pollution",
                "--exclude-dirs",
                ".git,.venv,venv,__pycache__,.pytest_cache,.idea,.vscode,dist,build,.next",
            ])
            self.assertEqual(result.returncode, 2)
            self.assertTrue(health_report.exists())
            data = json.loads(health_report.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
