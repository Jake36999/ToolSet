"""Session schema loading and validation for Local Tool Assist."""

import json
import pathlib
from typing import List, Tuple

_SCHEMA_PATH = pathlib.Path(__file__).parent / "local_tool_assist_session.schema.json"
_schema_cache: dict | None = None

_REQUIRED_TOP = (
    "schema_version", "session_id", "created_at", "updated_at",
    "request", "execution_mode", "review_state", "policy",
    "artifacts", "steps", "redaction",
)
_REQUIRED_REVIEW_STATE = (
    "scan_reviewed", "manifest_reviewed", "slice_approved",
    "approved_by", "approved_at", "approval_notes",
)


def get_schema_path() -> pathlib.Path:
    """Return the absolute path to the session JSON Schema file."""
    return _SCHEMA_PATH


def _load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        with open(_SCHEMA_PATH, encoding="utf-8") as fh:
            _schema_cache = json.load(fh)
    return _schema_cache


def validate_session(session_dict: dict) -> Tuple[bool, List[str]]:
    """Validate *session_dict* against the session JSON Schema.

    Returns ``(valid, errors)`` where *errors* is an empty list when valid.
    Uses :mod:`jsonschema` if installed; falls back to manual key checks.
    """
    try:
        import jsonschema  # optional dependency
        schema = _load_schema()
        validator = jsonschema.Draft7Validator(schema)
        errors = [
            e.message
            for e in sorted(validator.iter_errors(session_dict), key=lambda e: str(e.path))
        ]
        return len(errors) == 0, errors
    except ImportError:
        return _manual_validate(session_dict)


def _manual_validate(session_dict: dict) -> Tuple[bool, List[str]]:
    """Minimal key-presence validation used when jsonschema is not available."""
    errors: List[str] = []

    for key in _REQUIRED_TOP:
        if key not in session_dict:
            errors.append(f"Missing required top-level field: {key!r}")

    if session_dict.get("schema_version") != "LocalToolAssistSession/v1.0":
        errors.append("schema_version must be 'LocalToolAssistSession/v1.0'")

    review = session_dict.get("review_state", {})
    for key in _REQUIRED_REVIEW_STATE:
        if key not in review:
            errors.append(f"Missing review_state field: {key!r}")

    exec_mode = session_dict.get("execution_mode", {})
    if exec_mode.get("allow_command_execution") is not False:
        errors.append("execution_mode.allow_command_execution must be false")

    return len(errors) == 0, errors
