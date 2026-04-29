import csv
import pathlib
import tempfile
import unittest

from aletheia_tool_core.manifest import MANIFEST_COLUMNS, load_manifest_csv, validate_manifest_headers


class TestManifestLoading(unittest.TestCase):
    def test_validate_manifest_headers_accepts_required_columns(self):
        validate_manifest_headers(MANIFEST_COLUMNS)

    def test_validate_manifest_headers_rejects_missing_columns(self):
        with self.assertRaises(ValueError):
            validate_manifest_headers(["root", "abs_path"])

    def test_load_manifest_csv_reads_rows(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "root": "/tmp",
                "rel_path": "file.py",
                "abs_path": "/tmp/file.py",
                "ext": ".py",
                "size": "12",
                "mtime_iso": "2026-04-28T00:00:00",
                "sha1": "abcd1234",
            })
            path = pathlib.Path(handle.name)
        try:
            rows = load_manifest_csv(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rel_path"], "file.py")
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
