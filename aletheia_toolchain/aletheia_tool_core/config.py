import json
from pathlib import Path
from typing import Any, Dict, Optional


class ConfigError(Exception):
    pass


# Optional top-level sections and their expected Python types.
_OPTIONAL_SECTION_TYPES: Dict[str, type] = {
    "project_identity": dict,
    "source_scope": list,
    "profiles": dict,
    "architecture_expectations": dict,
    "risk_rules": dict,
    "runtime_dynamics": dict,
    "gates": dict,
    "plugins": list,
}


def default_config_skeleton() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project_identity": {
            "name": "",
            "version": "",
            "description": "",
        },
        "source_scope": [],
        "profiles": {},
        "architecture_expectations": {},
        "risk_rules": {},
        "runtime_dynamics": {},
        "gates": {},
        "plugins": [],
    }


def load_json_config(path: Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be a JSON object")
    return data


def validate_config(data: Dict[str, Any]) -> None:
    """Validate the top-level shape of a loaded config dict.

    Raises ConfigError for any structural violation:
    - ``schema_version`` must be present and a string.
    - Optional top-level sections must have the correct container type when present.
    - Each entry inside ``profiles`` must itself be a JSON object (dict).

    Does NOT validate the internal keys of individual profiles — callers that
    need stricter validation should inspect the resolved profile directly.
    """
    if "schema_version" not in data:
        raise ConfigError("Missing required field: schema_version")
    if not isinstance(data["schema_version"], str):
        raise ConfigError("schema_version must be a string")

    for section, expected_type in _OPTIONAL_SECTION_TYPES.items():
        if section in data and not isinstance(data[section], expected_type):
            raise ConfigError(
                f"'{section}' must be a {expected_type.__name__}, "
                f"got {type(data[section]).__name__}"
            )

    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        for pname, pval in profiles.items():
            if not isinstance(pval, dict):
                raise ConfigError(
                    f"Profile '{pname}' must be a JSON object, "
                    f"got {type(pval).__name__}"
                )


def resolve_profile(config: Dict[str, Any], profile_name: str) -> Dict[str, Any]:
    """Return the profile dict for *profile_name* from *config*.

    Raises ConfigError when:
    - ``config["profiles"]`` is missing or not a dict.
    - *profile_name* is not a key in ``config["profiles"]``.
    """
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ConfigError("Config 'profiles' section must be a JSON object")
    if profile_name not in profiles:
        available = sorted(profiles.keys())
        raise ConfigError(
            f"Profile '{profile_name}' not found. "
            f"Available: {available if available else ['(none)']}"
        )
    return profiles[profile_name]


def resolve_precedence(
    cli_overrides: Optional[Dict[str, Any]],
    profile_settings: Optional[Dict[str, Any]],
    project_defaults: Optional[Dict[str, Any]],
    builtin_defaults: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge four configuration layers with explicit precedence.

    Priority (highest → lowest):
    1. *cli_overrides*   — flags supplied directly on the command line
    2. *profile_settings* — values from the resolved named profile
    3. *project_defaults* — values from the project config file
    4. *builtin_defaults* — hard-coded tool defaults

    A value of ``None`` in any layer is treated as "not set" and falls through
    to the next layer.  An explicit non-None value — even ``False`` or ``0`` —
    wins over all lower-priority layers.

    Any of the four arguments may itself be ``None`` or an empty dict.
    """
    all_keys = (
        set(builtin_defaults or {})
        | set(project_defaults or {})
        | set(profile_settings or {})
        | set(cli_overrides or {})
    )
    result: Dict[str, Any] = {}
    for key in all_keys:
        for source in (cli_overrides, profile_settings, project_defaults, builtin_defaults):
            if source is not None and key in source and source[key] is not None:
                result[key] = source[key]
                break
    return result