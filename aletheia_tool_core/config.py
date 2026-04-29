import json
from pathlib import Path
from typing import Any, Dict


class ConfigError(Exception):
    pass


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