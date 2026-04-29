import os
import pathlib
import tempfile
import unittest

from aletheia_tool_core.security import (
    DEFAULT_IGNORE_EXTENSIONS,
    SecurityKernel,
    calculate_entropy,
    compute_file_fingerprint,
    is_binary_file,
    sanitize_content,
)


class TestSecurityHelpers(unittest.TestCase):
    def test_is_binary_file_detects_text_false(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write("hello world\n")
            path = handle.name
        try:
            self.assertFalse(is_binary_file(path, ignore_exts=[]))
        finally:
            os.unlink(path)

    def test_is_binary_file_respects_ignore_extensions(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as handle:
            handle.write(b"\x00\x89PNG")
            path = handle.name
        try:
            self.assertTrue(is_binary_file(path, ignore_exts=DEFAULT_IGNORE_EXTENSIONS))
        finally:
            os.unlink(path)

    def test_compute_file_fingerprint_returns_expected_keys(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write("test content")
            path = pathlib.Path(handle.name)
        try:
            fingerprint = compute_file_fingerprint(path)
            self.assertIn("sha1", fingerprint)
            self.assertIn("mtime_iso", fingerprint)
            self.assertIn("size_bytes", fingerprint)
            self.assertEqual(int(fingerprint["size_bytes"]), path.stat().st_size)
        finally:
            path.unlink()

    def test_calculate_entropy_low_and_high(self):
        self.assertEqual(calculate_entropy(""), 0.0)
        self.assertGreater(calculate_entropy("a1b2c3d4e5f6g7"), 0.0)

    def test_sanitize_content_redacts_secret_patterns(self):
        source = "api_key = 'AAAAAAAAAAAAAAAAAAAA'\nnormal=1"
        sanitized = sanitize_content(source)
        self.assertIn("[REDACTED_SENSITIVE_PATTERN]", sanitized)
        self.assertIn("normal=1", sanitized)

    def test_security_kernel_sanitize_content(self):
        raw = "-----BEGIN RSA KEY-----\nsecret\n-----END RSA KEY-----"
        output = SecurityKernel.sanitize(raw)
        self.assertIn("[REDACTED_SENSITIVE_PATTERN]", output)


if __name__ == "__main__":
    unittest.main()
