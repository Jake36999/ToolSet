#!/usr/bin/env python3
"""notebook_packager_v3.1.py

Jupyter Notebook Packager (Self-Extracting Colab Bundle) v3.1.

Upgraded successor to notebook_packager.py (v3).  Adds manifest-driven
file selection, semantic project config integration, staging-directory
output routing, and a requirements-mode flag.

New flags (vs v3):
  --manifest PATH              Package only files listed in the manifest CSV.
  --config PATH                Load a semantic_project_config JSON; respects
                               profile exclude_dirs and include_exts.
  --profile NAME               Named profile to resolve from --config.
  --staging-dir DIR            Route output .ipynb to this directory.
  --requirements-mode MODE     auto | off | required  (default: auto)
    auto      Install from requirements.txt if present (v3 behaviour).
    off       Never add a dependency-install cell.
    required  Add install cell; exit 1 with a warning if requirements.txt
              is absent from the collected files.

Legacy flags preserved (v3 compatible):
  path               Project directory to package (positional, optional).
  -o / --output      Explicit output .ipynb filename.

Exit codes:
  0  — Notebook written successfully.
  1  — Invocation error, packaging failure, or requirements-mode=required
       with requirements.txt absent.
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Optional aletheia_tool_core helpers
# ---------------------------------------------------------------------------

try:
    from aletheia_tool_core.security import is_binary_file, sanitize_content
    from aletheia_tool_core.manifest import load_manifest_csv
    from aletheia_tool_core.config import load_json_config, resolve_profile
    _CORE_AVAILABLE = True
except ImportError:
    _CORE_AVAILABLE = False

    import math
    import mimetypes

    _DEFAULT_IGNORE_EXTS = {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff",
        ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".exe",
        ".dll", ".so", ".dylib", ".pdf", ".bin", ".class", ".pyc",
    }
    _SENSITIVE_PATTERNS = [
        re.compile(r"-----BEGIN[A-Z0-9 ]+KEY-----.*?-----END[A-Z0-9 ]+KEY-----", re.DOTALL),
        re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL),
        re.compile(r"(api_key|secret_key|auth_token)\s*[:=]\s*['\"][A-Za-z0-9+/=]{20,}['\"]", re.IGNORECASE),
        re.compile(r"(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    ]

    def is_binary_file(filepath: str, ignore_exts=None, scan_bytes: int = 2048) -> bool:  # type: ignore[misc]
        exts = ignore_exts if ignore_exts is not None else _DEFAULT_IGNORE_EXTS
        if any(filepath.lower().endswith(e) for e in exts):
            return True
        try:
            with open(filepath, "rb") as fh:
                chunk = fh.read(scan_bytes)
            if not chunk:
                return False
            if b"\x00" in chunk:
                return True
            guess, _ = mimetypes.guess_type(filepath)
            if guess and not guess.startswith(("text", "application")):
                return True
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x7F)))
            nontext = sum(b not in text_chars for b in chunk)
            return (nontext / len(chunk)) > 0.40
        except Exception:
            return True

    def _entropy(text: str) -> float:
        if not text:
            return 0.0
        e = 0.0
        for i in range(256):
            p = text.count(chr(i)) / len(text)
            if p > 0:
                e -= p * math.log(p, 2)
        return e

    def sanitize_content(content: str) -> str:  # type: ignore[misc]
        for pat in _SENSITIVE_PATTERNS:
            content = pat.sub("[REDACTED_SENSITIVE_PATTERN]", content)
        lines: List[str] = []
        for line in content.splitlines():
            if any(k in line.lower() for k in ["api", "key", "secret", "token", "auth", "password"]):
                if _entropy(line) > 4.5:
                    parts = line.split("=", 1)
                    prefix = parts[0] if len(parts) == 2 else line.split(":", 1)[0]
                    lines.append(f"{prefix}= [REDACTED_HIGH_ENTROPY]")
                    continue
            lines.append(line)
        return "\n".join(lines)

    def load_manifest_csv(path) -> List[Dict[str, str]]:  # type: ignore[misc]
        import csv
        rows = []
        with open(path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append(dict(row))
        return rows

    def load_json_config(path) -> Dict[str, Any]:  # type: ignore[misc]
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def resolve_profile(config: Dict[str, Any], profile_name: str) -> Dict[str, Any]:  # type: ignore[misc]
        return config.get("profiles", {}).get(profile_name, {})


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKAGER_VERSION: str = "v3.1"
_ENCODING: str = "utf-8"
_DEFAULT_MAX_FILE_SIZE: int = 1_500_000

_DEFAULT_IGNORE_DIRS: Set[str] = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache",
}

_REQUIREMENTS_FILE = "requirements.txt"


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

class _FileEntry:
    __slots__ = ("path", "size_bytes", "content")

    def __init__(self, path: str, size_bytes: int, content: str) -> None:
        self.path = path
        self.size_bytes = size_bytes
        self.content = content


def _collect_path_mode(
    target_dir: pathlib.Path,
    ignore_dirs: Set[str],
    include_exts: Optional[List[str]],
    max_file_size: int,
    staging_dir: Optional[pathlib.Path],
) -> List[_FileEntry]:
    entries: List[_FileEntry] = []
    staging_resolved = staging_dir.resolve() if staging_dir else None

    for root, dirs, files in os.walk(target_dir):
        root_path = pathlib.Path(root)
        if staging_resolved and root_path.resolve() == staging_resolved:
            dirs.clear()
            continue
        dirs[:] = [
            d for d in dirs
            if d not in ignore_dirs and not d.startswith(".")
        ]
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            path_obj = root_path / fname
            rel_path = path_obj.relative_to(target_dir).as_posix()
            if include_exts and path_obj.suffix.lower() not in include_exts:
                continue
            if is_binary_file(str(path_obj)):
                continue
            size = path_obj.stat().st_size
            is_py = path_obj.suffix.lower() == ".py"
            if not is_py and size > max_file_size:
                print(f"Skipping {rel_path} (size limit)", file=sys.stderr)
                continue
            try:
                content = path_obj.read_text(encoding=_ENCODING, errors="ignore")
                entries.append(_FileEntry(rel_path, size, sanitize_content(content)))
            except Exception as exc:
                print(f"Skipping {rel_path} (read error: {exc})", file=sys.stderr)
    return entries


def _collect_manifest_mode(
    manifest_rows: List[Dict[str, str]],
    include_exts: Optional[List[str]],
    max_file_size: int,
) -> List[_FileEntry]:
    entries: List[_FileEntry] = []
    for row in manifest_rows:
        abs_path = row.get("abs_path", "").strip()
        rel_path = row.get("rel_path", "").strip()
        if not abs_path or not rel_path:
            continue
        path_obj = pathlib.Path(abs_path)
        if not path_obj.exists():
            print(f"WARNING: manifest file not found: {abs_path}", file=sys.stderr)
            continue
        if include_exts and path_obj.suffix.lower() not in include_exts:
            continue
        if is_binary_file(str(path_obj)):
            continue
        size = path_obj.stat().st_size
        is_py = path_obj.suffix.lower() == ".py"
        if not is_py and size > max_file_size:
            print(f"Skipping {rel_path} (size limit)", file=sys.stderr)
            continue
        try:
            content = path_obj.read_text(encoding=_ENCODING, errors="ignore")
            entries.append(_FileEntry(rel_path, size, sanitize_content(content)))
        except Exception as exc:
            print(f"Skipping {rel_path} (read error: {exc})", file=sys.stderr)
    return entries


# ---------------------------------------------------------------------------
# Notebook builder
# ---------------------------------------------------------------------------

def _markdown_cell(text: str) -> Dict[str, Any]:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [line + "\n" for line in text.splitlines()],
    }


def _code_cell(text: str) -> Dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [line + "\n" for line in text.splitlines()],
    }


def build_notebook(
    entries: List[_FileEntry],
    project_name: str,
    requirements_mode: str,
) -> str:
    """Assemble the self-extracting notebook JSON string."""
    cells = []
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    intro = (
        f"# Self-Extracting Workspace Bundle: `{project_name}`\n"
        f"**Generated:** {timestamp} — Packager {PACKAGER_VERSION}\n\n"
        "**Instructions:** Run all cells to recreate the directory structure "
        "and files in your Colab/Jupyter environment."
    )
    cells.append(_markdown_cell(intro))

    # Directory setup cell
    dirs: Set[str] = set()
    for e in entries:
        parent = str(pathlib.Path(e.path).parent)
        if parent != ".":
            dirs.add(parent)
    if dirs:
        dirs_list = ",\n    ".join(f'"{d}"' for d in sorted(dirs))
        setup_code = (
            "import os\n\n"
            f"for d in [\n    {dirs_list}\n]:\n"
            "    os.makedirs(d, exist_ok=True)\n"
            "    print(f'Created: {d}')\n"
            "print('Directory setup complete!')"
        )
        cells.append(_markdown_cell("### Step 1: Create Directory Tree"))
        cells.append(_code_cell(setup_code))

    # File write cells
    cells.append(_markdown_cell("### Step 2: Extract Files"))
    has_requirements = False
    for e in sorted(entries, key=lambda x: x.path):
        if e.path.lower() == _REQUIREMENTS_FILE:
            has_requirements = True
        write_code = f"%%writefile {e.path}\n{e.content}"
        cells.append(_markdown_cell(f"**Extracting:** `{e.path}` ({e.size_bytes} bytes)"))
        cells.append(_code_cell(write_code))

    # Requirements install cell
    if requirements_mode == "off":
        pass
    elif requirements_mode == "required":
        cells.append(_markdown_cell("### Step 3: Install Dependencies"))
        cells.append(_code_cell("!pip install -r requirements.txt"))
    else:  # auto
        if has_requirements:
            cells.append(_markdown_cell(
                "### Step 3: Install Dependencies\n"
                "Automatically installing packages from `requirements.txt`."
            ))
            cells.append(_code_cell("!pip install -r requirements.txt"))

    notebook = {
        "cells": cells,
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook, indent=2)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_settings(
    config_path: Optional[pathlib.Path],
    profile_name: Optional[str],
) -> Dict[str, Any]:
    if config_path is None:
        return {}
    cfg = load_json_config(config_path)
    settings: Dict[str, Any] = {}

    risk = cfg.get("risk_rules") or {}
    if risk.get("max_file_size"):
        settings["max_file_size"] = int(risk["max_file_size"])

    if profile_name:
        try:
            profile = resolve_profile(cfg, profile_name)
        except Exception:
            profile = {}
    else:
        profile = {}

    if profile.get("exclude_dirs"):
        settings["exclude_dirs"] = list(profile["exclude_dirs"])
    if profile.get("include_exts"):
        settings["include_exts"] = [
            e if e.startswith(".") else f".{e}" for e in profile["include_exts"]
        ]
    if profile.get("max_file_size"):
        settings["max_file_size"] = int(profile["max_file_size"])

    return settings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="notebook_packager_v3.1",
        description=(
            "Jupyter Notebook Packager v3.1. "
            "Packages a directory into a self-extracting .ipynb bundle with "
            "%%writefile cells, manifest mode, config integration, staging "
            "directory routing, and requirements-mode control."
        ),
    )
    parser.add_argument(
        "path", nargs="?", default=".", help="Project directory to package."
    )
    parser.add_argument(
        "--manifest", default=None, metavar="CSV",
        help="Manifest CSV; package only listed files.",
    )
    parser.add_argument(
        "--config", default=None, metavar="JSON",
        help="Semantic project config JSON for exclude/include filters.",
    )
    parser.add_argument(
        "--profile", default=None, metavar="NAME",
        help="Named profile to resolve from --config.",
    )
    parser.add_argument(
        "--staging-dir", default=None, dest="staging_dir", metavar="DIR",
        help="Route output .ipynb to this directory.",
    )
    parser.add_argument(
        "--requirements-mode",
        choices=["auto", "off", "required"],
        default="auto",
        dest="requirements_mode",
        help=(
            "auto: install if requirements.txt present (default). "
            "off: never add install cell. "
            "required: always add install cell; exit 1 if requirements.txt absent."
        ),
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="FILE",
        help="Explicit output .ipynb filename.",
    )

    args = parser.parse_args()

    target_dir = pathlib.Path(args.path).resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        print(f"ERROR: target must be a directory: {target_dir}", file=sys.stderr)
        sys.exit(1)

    config_path = pathlib.Path(args.config) if args.config else None
    if config_path is not None and not config_path.exists():
        print(f"ERROR: --config not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    try:
        cfg_settings = _resolve_settings(config_path, args.profile)
    except Exception as exc:
        print(f"ERROR: config: {exc}", file=sys.stderr)
        sys.exit(1)

    max_file_size: int = cfg_settings.get("max_file_size", _DEFAULT_MAX_FILE_SIZE)
    exclude_dirs_extra: List[str] = cfg_settings.get("exclude_dirs", [])
    include_exts: Optional[List[str]] = cfg_settings.get("include_exts")

    staging_dir = pathlib.Path(args.staging_dir) if args.staging_dir else None
    if staging_dir is not None:
        staging_dir.mkdir(parents=True, exist_ok=True)

    # Collect files
    if args.manifest:
        manifest_path = pathlib.Path(args.manifest)
        if not manifest_path.exists():
            print(f"ERROR: --manifest not found: {manifest_path}", file=sys.stderr)
            sys.exit(1)
        try:
            rows = load_manifest_csv(manifest_path)
        except Exception as exc:
            print(f"ERROR: reading manifest: {exc}", file=sys.stderr)
            sys.exit(1)
        entries = _collect_manifest_mode(rows, include_exts, max_file_size)
    else:
        effective_ignore = _DEFAULT_IGNORE_DIRS | set(exclude_dirs_extra)
        entries = _collect_path_mode(
            target_dir, effective_ignore, include_exts, max_file_size, staging_dir
        )

    # requirements-mode validation
    has_requirements = any(e.path.lower() == _REQUIREMENTS_FILE for e in entries)
    if args.requirements_mode == "required" and not has_requirements:
        print(
            f"WARNING: --requirements-mode=required but '{_REQUIREMENTS_FILE}' "
            "was not found in the collected files.",
            file=sys.stderr,
        )
        sys.exit(1)

    nb_content = build_notebook(entries, target_dir.name, args.requirements_mode)
    print(f"Notebook Packager {PACKAGER_VERSION}")
    print(f"  Files collected: {len(entries)}")

    # Determine output path
    if args.output:
        out_path = pathlib.Path(args.output)
        if out_path.suffix != ".ipynb":
            out_path = out_path.with_suffix(".ipynb")
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{target_dir.name}_bundle_{ts}.ipynb"
        if staging_dir:
            out_path = staging_dir / fname
        else:
            out_path = target_dir.parent / fname

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(nb_content, encoding=_ENCODING)
    except Exception as exc:
        print(f"ERROR writing output: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Notebook saved to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
