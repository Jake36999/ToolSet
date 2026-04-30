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

    # --- v2 compatibility ---

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

    # --- legacy profile still works ---

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

    # --- required profile names ---

    def test_required_profile_python_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            (root / "main.py").write_text("print('main')\n", encoding="utf-8")
            venv = root / ".venv" / "lib"
            venv.mkdir(parents=True)
            (venv / "some.py").write_text("# venv file\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli(["--roots", str(root), "--out", str(out_csv), "--profile", "python_project"])
            self.assertEqual(result.returncode, 0, result.stderr)
            with out_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rel_paths = [r["rel_path"] for r in rows]
            self.assertIn("main.py", rel_paths)
            self.assertFalse(any(".venv" in p for p in rel_paths), "python_project must exclude .venv")

    def test_required_profile_polyglot_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            (root / "app.py").write_text("print('app')\n", encoding="utf-8")
            (root / "index.ts").write_text("export {};\n", encoding="utf-8")
            nm = root / "node_modules" / "pkg"
            nm.mkdir(parents=True)
            (nm / "index.js").write_text("// pkg\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli(["--roots", str(root), "--out", str(out_csv), "--profile", "polyglot_runtime"])
            self.assertEqual(result.returncode, 0, result.stderr)
            with out_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rel_paths = [r["rel_path"] for r in rows]
            self.assertFalse(any("node_modules" in p for p in rel_paths), "polyglot_runtime must exclude node_modules")
            self.assertTrue(any("app.py" in p for p in rel_paths))
            self.assertTrue(any("index.ts" in p for p in rel_paths))

    def test_required_profile_training_pipeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            (root / "train.py").write_text("# training\n", encoding="utf-8")
            (root / "config.yaml").write_text("lr: 0.001\n", encoding="utf-8")
            ckpt = root / "checkpoints"
            ckpt.mkdir(parents=True)
            (ckpt / "model.bin").write_text("", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli(["--roots", str(root), "--out", str(out_csv), "--profile", "training_pipeline"])
            self.assertEqual(result.returncode, 0, result.stderr)
            with out_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rel_paths = [r["rel_path"] for r in rows]
            self.assertFalse(any("checkpoints" in p for p in rel_paths), "training_pipeline must exclude checkpoints")
            self.assertTrue(any("train.py" in p for p in rel_paths))

    def test_required_profile_node_python_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            (root / "server.py").write_text("# server\n", encoding="utf-8")
            (root / "client.ts").write_text("// client\n", encoding="utf-8")
            nm = root / "node_modules" / "pkg"
            nm.mkdir(parents=True)
            (nm / "index.js").write_text("// pkg\n", encoding="utf-8")
            venv = root / "venv" / "lib"
            venv.mkdir(parents=True)
            (venv / "util.py").write_text("# venv\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli(["--roots", str(root), "--out", str(out_csv), "--profile", "node_python_runtime"])
            self.assertEqual(result.returncode, 0, result.stderr)
            with out_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rel_paths = [r["rel_path"] for r in rows]
            self.assertFalse(any("node_modules" in p for p in rel_paths), "node_python_runtime must exclude node_modules")
            self.assertFalse(any("venv" in p for p in rel_paths), "node_python_runtime must exclude venv")
            self.assertTrue(any("server.py" in p for p in rel_paths))
            self.assertTrue(any("client.ts" in p for p in rel_paths))

    # --- health report and pollution ---

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
            self.assertEqual(data["status"], "BLOCK")

    def test_fail_on_pollution_without_health_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            repo_path = root / "node_modules"
            repo_path.mkdir(parents=True)
            (repo_path / "ignore.py").write_text("print('ignored')\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli([
                "--roots",
                str(root),
                "--out",
                str(out_csv),
                "--fail-on-pollution",
                "--exclude-dirs",
                ".git,.venv,venv,__pycache__,.pytest_cache,.idea,.vscode,dist,build,.next",
            ])
            self.assertEqual(result.returncode, 2, "--fail-on-pollution must exit 2 without --health-report when pollution found")

    def test_no_pollution_exits_zero_with_fail_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "repo"
            root.mkdir(parents=True)
            (root / "clean.py").write_text("print('clean')\n", encoding="utf-8")
            out_csv = pathlib.Path(temp_dir) / "file_map.csv"

            result = self.run_cli([
                "--roots",
                str(root),
                "--out",
                str(out_csv),
                "--fail-on-pollution",
            ])
            self.assertEqual(result.returncode, 0, "--fail-on-pollution must not exit non-zero when there is no pollution")


if __name__ == "__main__":
    unittest.main()
