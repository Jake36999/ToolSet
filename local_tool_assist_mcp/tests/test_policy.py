import os
import pathlib
import tempfile
import unittest

from local_tool_assist_mcp.policy import (
    PolicyError,
    ensure_no_arbitrary_shell,
    ensure_no_secrets_in_text,
    ensure_outputs_outside_toolchain,
    ensure_remote_mcp_auth,
    ensure_session_owned_read,
    ensure_slice_prereqs,
)
from local_tool_assist_mcp.session import TOOLCHAIN_ROOT


class TestPolicy(unittest.TestCase):
    def test_no_arbitrary_shell_blocks_unknown_script(self):
        with self.assertRaises(PolicyError):
            ensure_no_arbitrary_shell(["python", "evil.py"], {"ok.py"})

    def test_session_owned_reads_only_and_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            session_dir = root / "sessions" / "s1"
            session_dir.mkdir(parents=True)
            good = session_dir / "a.txt"
            good.write_text("ok", encoding="utf-8")
            ensure_session_owned_read(good, root, session_dir)
            with self.assertRaises(PolicyError):
                ensure_session_owned_read(root.parent / "x.txt", root, session_dir)

    def test_no_outputs_in_toolchain(self):
        with self.assertRaises(PolicyError):
            ensure_outputs_outside_toolchain(TOOLCHAIN_ROOT / "x")

    def test_manifest_required_before_slice(self):
        with self.assertRaises(PolicyError):
            ensure_slice_prereqs({"artifacts": {}}, dev_mode=False)

    def test_doctor_pass_warn_required_before_slice(self):
        sd = {"artifacts": {"manifest_csv": "m.csv", "manifest_doctor_json": "d.json"}, "latest": {"manifest_doctor_status": "BLOCK"}, "review_state": {"slice_approved": True}}
        with self.assertRaises(PolicyError):
            ensure_slice_prereqs(sd, dev_mode=False)

    def test_review_approval_required_before_slice(self):
        sd = {"artifacts": {"manifest_csv": "m.csv", "manifest_doctor_json": "d.json"}, "latest": {"manifest_doctor_status": "PASS"}, "review_state": {"slice_approved": False}}
        with self.assertRaises(PolicyError):
            ensure_slice_prereqs(sd, dev_mode=False)

    def test_remote_auth_refusal(self):
        old = os.environ.pop("LTA_MCP_AUTH_TOKEN", None)
        old_dev = os.environ.pop("LTA_DEV_MODE", None)
        try:
            with self.assertRaises(PolicyError):
                ensure_remote_mcp_auth("https://remote-mcp.example")
        finally:
            if old is not None:
                os.environ["LTA_MCP_AUTH_TOKEN"] = old
            if old_dev is not None:
                os.environ["LTA_DEV_MODE"] = old_dev

    def test_secret_detection(self):
        with self.assertRaises(PolicyError):
            ensure_no_secrets_in_text("API_KEY=abc123")

    def test_policy_error_serializable(self):
        err = PolicyError("X", "msg")
        data = err.to_result()
        self.assertIn("policy", data)
        self.assertEqual(data["policy"]["code"], "X")


if __name__ == "__main__":
    unittest.main()
