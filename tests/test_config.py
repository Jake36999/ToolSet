import json
import pathlib
import tempfile
import unittest

from aletheia_tool_core.config import ConfigError, default_config_skeleton, load_json_config


class TestConfigLoading(unittest.TestCase):
    def test_default_config_skeleton_includes_expected_keys(self):
        skeleton = default_config_skeleton()
        self.assertIn("schema_version", skeleton)
        self.assertIn("project_identity", skeleton)
        self.assertIn("profiles", skeleton)

    def test_load_json_config_reads_valid_json(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            json.dump({"schema_version": "1.0"}, handle)
            path = pathlib.Path(handle.name)
        try:
            config = load_json_config(path)
            self.assertEqual(config["schema_version"], "1.0")
        finally:
            path.unlink()

    def test_load_json_config_raises_on_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write("{ invalid json }")
            path = pathlib.Path(handle.name)
        try:
            with self.assertRaises(ConfigError):
                load_json_config(path)
        finally:
            path.unlink()

    def test_load_json_config_raises_on_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_json_config(pathlib.Path("does_not_exist.json"))


if __name__ == "__main__":
    unittest.main()
