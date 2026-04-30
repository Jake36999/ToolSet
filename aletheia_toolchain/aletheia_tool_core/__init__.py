"""Aletheia shared internal tool core.

This package provides common helpers for security redaction, manifest handling,
report generation, and config loading without introducing external dependencies.
"""

from .config import (
    ConfigError,
    default_config_skeleton,
    load_json_config,
    validate_config,
    resolve_profile,
    resolve_precedence,
)
from .manifest import (
    MANIFEST_COLUMNS,
    DEFAULT_SUSPICIOUS_DIRECTORIES,
    analyze_manifest_rows,
    is_suspicious_manifest_path,
    load_manifest_csv,
    validate_manifest_headers,
)
from .reports import format_markdown_section, write_json_report, write_markdown_report
from .security import (
    DEFAULT_IGNORE_EXTENSIONS,
    SecurityKernel,
    calculate_entropy,
    compute_file_fingerprint,
    is_binary_file,
    is_ignored_dir,
    sanitize_content,
)

__all__ = [
    "ConfigError",
    "default_config_skeleton",
    "load_json_config",
    "validate_config",
    "resolve_profile",
    "resolve_precedence",
    "MANIFEST_COLUMNS",
    "DEFAULT_SUSPICIOUS_DIRECTORIES",
    "analyze_manifest_rows",
    "is_suspicious_manifest_path",
    "load_manifest_csv",
    "validate_manifest_headers",
    "format_markdown_section",
    "write_json_report",
    "write_markdown_report",
    "DEFAULT_IGNORE_EXTENSIONS",
    "SecurityKernel",
    "calculate_entropy",
    "compute_file_fingerprint",
    "is_binary_file",
    "is_ignored_dir",
    "sanitize_content",
]