import pathlib
import os
import tempfile
import unittest

from local_tool_assist_mcp.tool_registry import ToolEntry, _scan_flags, _scan_artifacts, _doctor_flags, _doctor_artifacts, _slicer_flags, _slicer_artifacts
from local_tool_assist_mcp.workflow import run_guided_repository_investigation

_FIX = pathlib.Path(__file__).parent / "fixtures"


def _registry():
    return {
        "scan_directory": ToolEntry(action="scan_directory", script_name="fake_scanner.py", timeout_seconds=15, requires_review_approval=False, build_flags=_scan_flags, collect_artifacts=_scan_artifacts, primary_json_report_key="manifest_health_json"),
        "validate_manifest": ToolEntry(action="validate_manifest", script_name="fake_doctor.py", timeout_seconds=15, requires_review_approval=False, build_flags=_doctor_flags, collect_artifacts=_doctor_artifacts, primary_json_report_key="manifest_doctor_json"),
        "run_semantic_slice": ToolEntry(action="run_semantic_slice", script_name="fake_slicer.py", timeout_seconds=15, requires_review_approval=True, build_flags=_slicer_flags, collect_artifacts=_slicer_artifacts, primary_json_report_key="slicer_json"),
    }


class TestGuidedWorkflow(unittest.TestCase):
    def test_default_stop_before_slice(self):
        with tempfile.TemporaryDirectory() as d:
            out = run_guided_repository_investigation("obj", "/repo", output_root=d, _registry=_registry(), _toolchain_root=_FIX)
        events = [e["event"] for e in out["events"]]
        self.assertIn("REVIEW_REQUIRED", events)
        self.assertNotIn("SLICE_COMPLETE", events)

    def test_block_on_manifest_failure(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["FAKE_DOCTOR_EXIT"] = "2"
            try:
                out = run_guided_repository_investigation("obj", "/repo/block", output_root=d, _registry=_registry(), _toolchain_root=_FIX)
            finally:
                os.environ.pop("FAKE_DOCTOR_EXIT", None)
        events = [e["event"] for e in out["events"]]
        self.assertIn("MANIFEST_BLOCK", events)
        self.assertIn("ARCHIVED", events)

    def test_approval_gated_slice(self):
        with tempfile.TemporaryDirectory() as d:
            out = run_guided_repository_investigation("obj", "/repo", allow_slice=True, output_root=d, _registry=_registry(), _toolchain_root=_FIX)
        events = [e["event"] for e in out["events"]]
        self.assertIn("REVIEW_REQUIRED", events)
