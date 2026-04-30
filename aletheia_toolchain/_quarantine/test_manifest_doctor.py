import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from aletheia_tool_core.manifest import DEFAULT_SUSPICIOUS_DIRECTORIES, analyze_manifest_rows, load_manifest_csv


class TestManifestDoctorCLI(unittest.TestCase):
    def test_manifest_doctor_warns_on_suspicious_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = pathlib.Path(temp_dir)
            manifest_path = temp_dir_path / "manifest.csv"
            output_path = temp_dir_path / "report.json"

            suspicious_file = temp_dir_path / "node_modules" / "ignored.py"
            suspicious_file.parent.mkdir(parents=True, exist_ok=True)
            suspicious_file.write_text("print('ignored')", encoding="utf-8")

            with manifest_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write("root,rel_path,abs_path,ext,size,mtime_iso,sha1\n")
                handle.write(
                    f".,test.py,{suspicious_file},.py,10,2026-04-28T00:00:00,abcd1234\n"
                )

            result = subprocess.run(
                [
                    sys.executable,
                    str(pathlib.Path(__file__).resolve().parents[1] / "manifest_doctor.py"),
                    "--manifest",
                    str(manifest_path),
                    "--out",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("Manifest Doctor status: WARN", result.stdout)
            self.assertTrue(output_path.exists())
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "WARN")
            self.assertEqual(report["summary"]["row_count"], 1)
            self.assertEqual(report["summary"]["suspicious_paths"], 1)

    def test_analyze_manifest_rows_fails_on_missing_path(self):
        rows = [
            {
                "root": ".",
                "rel_path": "missing.py",
                "abs_path": str(pathlib.Path("/path/does/not/exist.py")),
                "ext": ".py",
                "size": "0",
                "mtime_iso": "2026-04-28T00:00:00",
                "sha1": "",
            }
        ]
        analysis = analyze_manifest_rows(rows, DEFAULT_SUSPICIOUS_DIRECTORIES)
        self.assertEqual(analysis["status"], "FAIL")
        self.assertEqual(analysis["summary"]["missing_files"], 1)


if __name__ == "__main__":
    unittest.main()
