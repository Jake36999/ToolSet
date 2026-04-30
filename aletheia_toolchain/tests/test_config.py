import json
import pathlib
import tempfile
import unittest

from aletheia_tool_core.config import (
    ConfigError,
    default_config_skeleton,
    load_json_config,
    validate_config,
    resolve_profile,
    resolve_precedence,
)

# Paths used for the example-config round-trip tests.
_TOOLCHAIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
_EXAMPLES_DIR = _TOOLCHAIN_ROOT / "examples" / "configs"


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


class TestValidateConfig(unittest.TestCase):
    """validate_config() accepts well-formed configs and rejects structural violations."""

    def test_valid_minimal_config_passes(self):
        """A config with only schema_version should validate without error."""
        validate_config({"schema_version": "1.0"})

    def test_valid_full_skeleton_passes(self):
        """The default skeleton must always pass validation."""
        validate_config(default_config_skeleton())

    def test_missing_schema_version_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            validate_config({"profiles": {}})
        self.assertIn("schema_version", str(ctx.exception))

    def test_non_string_schema_version_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            validate_config({"schema_version": 1})
        self.assertIn("schema_version", str(ctx.exception))

    def test_profiles_as_list_raises(self):
        """profiles must be a dict, not a list."""
        with self.assertRaises(ConfigError) as ctx:
            validate_config({"schema_version": "1.0", "profiles": ["default"]})
        self.assertIn("profiles", str(ctx.exception))

    def test_non_dict_profile_entry_raises(self):
        """Each value inside profiles must be a dict."""
        with self.assertRaises(ConfigError) as ctx:
            validate_config({
                "schema_version": "1.0",
                "profiles": {"bad_profile": "just-a-string"},
            })
        self.assertIn("bad_profile", str(ctx.exception))

    def test_source_scope_as_dict_raises(self):
        """source_scope must be a list, not a dict."""
        with self.assertRaises(ConfigError) as ctx:
            validate_config({"schema_version": "1.0", "source_scope": {"key": "val"}})
        self.assertIn("source_scope", str(ctx.exception))

    def test_optional_sections_absent_passes(self):
        """A config with schema_version only must pass — all other sections are optional."""
        validate_config({"schema_version": "1.0"})


class TestResolveProfile(unittest.TestCase):
    """resolve_profile() returns the right profile dict and raises on unknown names."""

    _CFG = {
        "schema_version": "1.0",
        "profiles": {
            "default": {"include_exts": [".py"], "max_rows_soft": 500},
            "ci":      {"include_exts": [".py", ".toml"], "max_rows_hard": 1000},
        },
    }

    def test_known_profile_returned(self):
        profile = resolve_profile(self._CFG, "default")
        self.assertEqual(profile["include_exts"], [".py"])
        self.assertEqual(profile["max_rows_soft"], 500)

    def test_second_profile_returned(self):
        profile = resolve_profile(self._CFG, "ci")
        self.assertIn(".toml", profile["include_exts"])

    def test_unknown_profile_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            resolve_profile(self._CFG, "nonexistent")
        self.assertIn("nonexistent", str(ctx.exception))

    def test_error_message_lists_available_profiles(self):
        with self.assertRaises(ConfigError) as ctx:
            resolve_profile(self._CFG, "missing")
        msg = str(ctx.exception)
        self.assertIn("default", msg)
        self.assertIn("ci", msg)

    def test_empty_profiles_section_raises(self):
        with self.assertRaises(ConfigError):
            resolve_profile({"schema_version": "1.0", "profiles": {}}, "default")


class TestResolvePrecedence(unittest.TestCase):
    """resolve_precedence() merges four layers with CLI winning over all others."""

    def test_cli_overrides_all_lower_layers(self):
        result = resolve_precedence(
            cli_overrides={"max_file_size": 100},
            profile_settings={"max_file_size": 200},
            project_defaults={"max_file_size": 300},
            builtin_defaults={"max_file_size": 400},
        )
        self.assertEqual(result["max_file_size"], 100)

    def test_profile_overrides_project_and_builtin(self):
        result = resolve_precedence(
            cli_overrides=None,
            profile_settings={"max_file_size": 200},
            project_defaults={"max_file_size": 300},
            builtin_defaults={"max_file_size": 400},
        )
        self.assertEqual(result["max_file_size"], 200)

    def test_project_overrides_builtin(self):
        result = resolve_precedence(
            cli_overrides=None,
            profile_settings=None,
            project_defaults={"max_file_size": 300},
            builtin_defaults={"max_file_size": 400},
        )
        self.assertEqual(result["max_file_size"], 300)

    def test_builtin_used_when_all_higher_absent(self):
        result = resolve_precedence(
            cli_overrides=None,
            profile_settings=None,
            project_defaults=None,
            builtin_defaults={"max_file_size": 400},
        )
        self.assertEqual(result["max_file_size"], 400)

    def test_none_value_falls_through_to_lower_layer(self):
        """An explicit None in a higher layer must fall through, not win."""
        result = resolve_precedence(
            cli_overrides={"max_file_size": None},
            profile_settings={"max_file_size": None},
            project_defaults={"max_file_size": 300},
            builtin_defaults={"max_file_size": 400},
        )
        self.assertEqual(result["max_file_size"], 300)

    def test_false_zero_do_not_fall_through(self):
        """False and 0 are valid values and must NOT fall through to lower layers."""
        result = resolve_precedence(
            cli_overrides={"flag": False, "count": 0},
            profile_settings={"flag": True, "count": 99},
            project_defaults={},
            builtin_defaults={},
        )
        self.assertIs(result["flag"], False)
        self.assertEqual(result["count"], 0)

    def test_key_only_in_builtin_is_included(self):
        result = resolve_precedence(
            cli_overrides={},
            profile_settings={},
            project_defaults={},
            builtin_defaults={"only_here": "value"},
        )
        self.assertEqual(result["only_here"], "value")

    def test_all_none_layers_returns_empty(self):
        result = resolve_precedence(None, None, None, None)
        self.assertEqual(result, {})

    def test_keys_from_all_layers_are_merged(self):
        result = resolve_precedence(
            cli_overrides={"a": 1},
            profile_settings={"b": 2},
            project_defaults={"c": 3},
            builtin_defaults={"d": 4},
        )
        self.assertEqual(result, {"a": 1, "b": 2, "c": 3, "d": 4})


class TestSchemaSyncProfileFormat(unittest.TestCase):
    """Schema-sync repair (Phase 7): profile 'format' field must validate cleanly."""

    def test_profile_with_format_json_validates(self):
        """A profile containing format:'json' must not raise ConfigError."""
        data = {
            "schema_version": "1.0",
            "profiles": {
                "strict": {
                    "include_exts": [".py"],
                    "format": "json",
                }
            },
        }
        # validate_config only checks structural types, not profile-internal keys,
        # so this is a smoke test that the dict itself is accepted.
        validate_config(data)

    def test_profile_with_format_text_validates(self):
        data = {
            "schema_version": "1.0",
            "profiles": {"default": {"format": "text"}},
        }
        validate_config(data)

    def test_profile_format_is_resolvable(self):
        """resolve_profile must return the format key intact."""
        data = {
            "schema_version": "1.0",
            "profiles": {"ci": {"format": "json", "deterministic": True}},
        }
        profile = resolve_profile(data, "ci")
        self.assertEqual(profile["format"], "json")
        self.assertTrue(profile["deterministic"])

    def test_validate_only_with_profile_format_key(self):
        """--validate-only via slicer v7 must succeed for a config with format in the profile."""
        import subprocess, sys, tempfile, pathlib
        _SLICER = str(pathlib.Path(__file__).resolve().parents[1] / "semantic_slicer_v7.0.py")
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            cfg = tmp / "test_cfg.json"
            import json as _json
            cfg.write_text(_json.dumps({
                "schema_version": "1.0",
                "profiles": {"default": {"format": "json", "deterministic": True}},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, _SLICER, "--validate-only", "--config", str(cfg), "--task-profile", "default"],
                cwd=str(pathlib.Path(__file__).resolve().parents[1]),
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("validate-only check passed", result.stdout)


class TestExampleConfigs(unittest.TestCase):
    """Example config files must load and pass validate_config."""

    def _load_and_validate(self, filename: str) -> dict:
        path = _EXAMPLES_DIR / filename
        self.assertTrue(path.exists(), f"Example config not found: {path}")
        data = load_json_config(path)
        validate_config(data)   # must not raise
        return data

    def test_python_project_loads_and_validates(self):
        data = self._load_and_validate("python_project.json")
        self.assertEqual(data["schema_version"], "1.0")
        self.assertIn("profiles", data)

    def test_polyglot_runtime_loads_and_validates(self):
        data = self._load_and_validate("polyglot_runtime.json")
        self.assertEqual(data["schema_version"], "1.0")
        self.assertIn("profiles", data)

    def test_training_pipeline_loads_and_validates(self):
        data = self._load_and_validate("training_pipeline.json")
        self.assertEqual(data["schema_version"], "1.0")
        self.assertIn("profiles", data)

    def test_example_profiles_are_resolvable(self):
        """Each named profile in every example must be resolvable without error."""
        for fname in ("python_project.json", "polyglot_runtime.json", "training_pipeline.json"):
            data = self._load_and_validate(fname)
            for profile_name in data.get("profiles", {}):
                profile = resolve_profile(data, profile_name)
                self.assertIsInstance(profile, dict, f"{fname}/{profile_name} is not a dict")


if __name__ == "__main__":
    unittest.main()
