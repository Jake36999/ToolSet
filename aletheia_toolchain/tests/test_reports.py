import pathlib
import tempfile
import unittest

from aletheia_tool_core.reports import format_markdown_section, write_json_report, write_markdown_report


class TestReportGeneration(unittest.TestCase):
    def test_write_json_report_creates_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = pathlib.Path(temp_dir) / "report.json"
            write_json_report({"status": "pass"}, out_path)
            self.assertTrue(out_path.exists())
            content = out_path.read_text(encoding="utf-8")
            self.assertIn("pass", content)

    def test_write_markdown_report_outputs_sections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = pathlib.Path(temp_dir) / "report.md"
            write_markdown_report("Summary Report", {"Status": "PASS", "Details": ["one", "two"]}, out_path)
            text = out_path.read_text(encoding="utf-8")
            self.assertIn("# Summary Report", text)
            self.assertIn("## Status", text)
            self.assertIn("PASS", text)
            self.assertIn("- one", text)

    def test_format_markdown_section_handles_string_and_dict(self):
        markdown_text = format_markdown_section("Info", "All good")
        self.assertIn("## Info", markdown_text)
        self.assertIn("All good", markdown_text)


if __name__ == "__main__":
    unittest.main()
