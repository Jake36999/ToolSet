#!/usr/bin/env python3
"""workspace_packager_v2.4.py

Aletheia Workspace Packager v2.4.

Upgraded successor to workspace_packager_v2.3.py.  Adds manifest-driven
file selection, semantic project config integration, and staging-directory
output routing.  All v2.3 behaviours are preserved.

New flags (vs v2.3):
  --manifest PATH    Package only files listed in the manifest CSV
                     (uses abs_path for reading, rel_path for bundle path).
  --config PATH      Load a semantic_project_config JSON; honours the
                     profile's exclude_dirs, include_exts, and max_file_size.
  --profile NAME     Named profile to resolve from --config (optional).
  --staging-dir DIR  Route output to this directory instead of
                     auto-generating a bundle subdirectory.

Legacy flags preserved (v2.3 compatible):
  path               Project root to scan (positional, optional, default ".").
  --format           text | json | xml.
  -o / --output      Explicit output filename.

Exit codes:
  0  — Package written successfully.
  1  — Invocation error or packaging failure.
"""

import argparse
import ast
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional

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

    _DEFAULT_IGNORE_EXTS = [
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff",
        ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".exe",
        ".dll", ".so", ".dylib", ".pdf", ".bin", ".class", ".pyc",
    ]
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

PACKAGER_VERSION: str = "v2.4"
_ENCODING: str = "utf-8"

_DEFAULT_MAX_FILE_SIZE: int = 1_500_000

_DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache",
}


# ---------------------------------------------------------------------------
# Polyglot analyser (stdlib-only; replicated from v2.3)
# ---------------------------------------------------------------------------

class _PolyglotAnalyzer:
    def analyze(self, filename: str, content: str) -> Dict[str, Any]:
        ext = pathlib.Path(filename).suffix.lower()
        if ext == ".py":
            return self._python(content)
        if ext == ".json":
            return self._json(content)
        if ext in (".yaml", ".yml"):
            return self._yaml(content)
        if ext in (".md", ".markdown"):
            return self._markdown(content)
        return {"summary": {}, "complexity": 0}

    @staticmethod
    def _python(content: str) -> Dict[str, Any]:
        stats: Dict[str, Any] = {"functions": [], "classes": [], "imports": 0}
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    stats["imports"] += 1
                elif isinstance(node, ast.FunctionDef):
                    stats["functions"].append(node.name)
                elif isinstance(node, ast.ClassDef):
                    stats["classes"].append(node.name)
            return {"summary": stats, "complexity": stats["imports"]}
        except SyntaxError:
            return {"summary": {"error": "Syntax Error"}, "complexity": 999}
        except Exception:
            return {"summary": {}, "complexity": 0}

    @staticmethod
    def _json(content: str) -> Dict[str, Any]:
        try:
            data = json.loads(content)
            keys = list(data.keys())[:10] if isinstance(data, dict) else ["<array>"]
            return {"summary": {"keys": keys}, "complexity": 0}
        except json.JSONDecodeError:
            return {"summary": {"error": "Invalid JSON"}, "complexity": 0}

    @staticmethod
    def _yaml(content: str) -> Dict[str, Any]:
        keys: List[str] = []
        for line in content.splitlines():
            m = re.match(r"^([a-zA-Z0-9_-]+):", line)
            if m:
                keys.append(m.group(1))
        return {"summary": {"keys": keys[:15]}, "complexity": 0}

    @staticmethod
    def _markdown(content: str) -> Dict[str, Any]:
        headers = re.findall(r"^#{1,3}\s+(.*)", content, re.MULTILINE)
        return {"summary": {"headers": headers[:10]}, "complexity": 0}


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------

def _traverse(
    root: pathlib.Path,
    effective_ignore_dirs: set,
    staging_dir: Optional[pathlib.Path] = None,
):
    """Yield file paths under root, honouring ignore dirs and staging dir."""
    stack: List[pathlib.Path] = [root]
    staging_resolved = staging_dir.resolve() if staging_dir else None
    while stack:
        current = stack.pop()
        if not current.is_dir():
            continue
        if staging_resolved and current.resolve() == staging_resolved:
            continue
        try:
            with os.scandir(current) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    if entry.name in effective_ignore_dirs or entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(pathlib.Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        yield pathlib.Path(entry.path)
        except PermissionError:
            continue


# ---------------------------------------------------------------------------
# Core packager
# ---------------------------------------------------------------------------

class WorkspacePackager:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root.resolve()
        self.files_registry: List[Dict[str, Any]] = []
        self.scan_stats: Dict[str, Any] = {
            "bundled": 0,
            "skipped": 0,
            "errors": 0,
            "skipped_details": {"binary_or_ext": 0, "oversize": 0},
            "skipped_by_ext": {},
        }
        self.timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def _track_skip(self, reason: str, ext: str) -> None:
        self.scan_stats["skipped"] += 1
        self.scan_stats["skipped_details"][reason] = (
            self.scan_stats["skipped_details"].get(reason, 0) + 1
        )
        safe_ext = (ext or "").lower()
        self.scan_stats["skipped_by_ext"][safe_ext] = (
            self.scan_stats["skipped_by_ext"].get(safe_ext, 0) + 1
        )

    def _add_file(
        self,
        path_obj: pathlib.Path,
        rel_path: str,
        max_file_size: int,
        analyzer: _PolyglotAnalyzer,
    ) -> bool:
        """Read, guard, and register a single file. Returns True on success."""
        if is_binary_file(str(path_obj)):
            self._track_skip("binary_or_ext", path_obj.suffix)
            return False
        try:
            size = path_obj.stat().st_size
        except OSError:
            self.scan_stats["errors"] += 1
            return False
        if size > max_file_size:
            self._track_skip("oversize", path_obj.suffix)
            return False
        try:
            raw = path_obj.read_text(encoding=_ENCODING, errors="ignore")
        except Exception:
            self.scan_stats["errors"] += 1
            return False
        analysis = analyzer.analyze(path_obj.name, raw)
        self.files_registry.append({
            "path": rel_path,
            "size_bytes": size,
            "content": raw,
            "summary": analysis.get("summary"),
            "complexity": analysis.get("complexity", 0),
        })
        self.scan_stats["bundled"] += 1
        return True

    def run_path_mode(
        self,
        ignore_dirs: set,
        include_exts: Optional[List[str]],
        max_file_size: int,
        staging_dir: Optional[pathlib.Path],
    ) -> None:
        analyzer = _PolyglotAnalyzer()
        for path_obj in _traverse(self.root, ignore_dirs, staging_dir=staging_dir):
            if include_exts and path_obj.suffix.lower() not in include_exts:
                self._track_skip("binary_or_ext", path_obj.suffix)
                continue
            rel_path = path_obj.relative_to(self.root).as_posix()
            self._add_file(path_obj, rel_path, max_file_size, analyzer)

    def run_manifest_mode(
        self,
        manifest_rows: List[Dict[str, str]],
        max_file_size: int,
        include_exts: Optional[List[str]],
    ) -> None:
        analyzer = _PolyglotAnalyzer()
        for row in manifest_rows:
            abs_path = row.get("abs_path", "").strip()
            rel_path = row.get("rel_path", "").strip()
            if not abs_path or not rel_path:
                continue
            path_obj = pathlib.Path(abs_path)
            if not path_obj.exists():
                self.scan_stats["errors"] += 1
                continue
            if include_exts and path_obj.suffix.lower() not in include_exts:
                self._track_skip("binary_or_ext", path_obj.suffix)
                continue
            self._add_file(path_obj, rel_path, max_file_size, analyzer)

    def _tree_map(self) -> str:
        lines: List[str] = [f"{self.root.name}/"]
        paths = sorted(pathlib.Path(f["path"]) for f in self.files_registry)
        seen: set = set()
        for p in paths:
            parts = p.parts
            indent = 0
            for i, part in enumerate(parts[:-1]):
                d = str(pathlib.Path(*parts[: i + 1]))
                if d not in seen:
                    lines.append(f"{'  ' * (indent + 1)}|-- {part}/")
                    seen.add(d)
                indent += 1
            lines.append(f"{'  ' * (indent + 1)}|-- {parts[-1]}")
        return "\n".join(lines)

    def _format_text(self) -> str:
        lines = [
            f"# Workspace Bundle: {self.root.name}",
            f"# Generated: {self.timestamp}",
            "# Compliance: manifest/path mode; binary guard; redaction; polyglot summaries",
            "",
            "# Project Structure:",
        ]
        lines.extend(f"# {ln}" for ln in self._tree_map().splitlines())
        lines.append("")
        for f in self.files_registry:
            lines.append(f"--- FILE: {f['path']} ---")
            lines.append(f"Size: {f['size_bytes']} bytes")
            s = f["summary"] or {}
            parts: List[str] = []
            if s.get("classes"):
                parts.append("Classes: " + ", ".join(s["classes"]))
            if s.get("functions"):
                parts.append("Functions: " + ", ".join(s["functions"]))
            if s.get("keys"):
                parts.append("Keys: " + ", ".join(s["keys"]))
            if s.get("headers"):
                parts.append("Headers: " + ", ".join(s["headers"]))
            lines.append(f"Summary: {'; '.join(parts) or '(none)'}")
            lines.append("Content: |")
            for line in sanitize_content(f["content"]).splitlines():
                lines.append(f"  {line}")
            lines.append("")
        return "\n".join(lines)

    def _format_json(self) -> str:
        payload = {
            "meta": {
                "project": self.root.name,
                "generated_at": self.timestamp,
                "packager_version": PACKAGER_VERSION,
                "stats": self.scan_stats,
                "tree": self._tree_map(),
            },
            "files": [
                {
                    "path": f["path"],
                    "size_bytes": f["size_bytes"],
                    "complexity": f["complexity"],
                    "summary": f["summary"],
                    "content": sanitize_content(f["content"]),
                }
                for f in self.files_registry
            ],
        }
        return json.dumps(payload, indent=2)

    def _format_xml(self) -> str:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<workspace>"]
        lines.append(f'  <meta project="{self.root.name}" generated="{self.timestamp}">')
        lines.append("    <tree><![CDATA[")
        lines.append(self._tree_map())
        lines.append("    ]]></tree>")
        lines.append("  </meta>")
        lines.append("  <files>")
        for f in self.files_registry:
            lines.append(
                f'    <file path="{f["path"]}" size="{f["size_bytes"]}" '
                f'complexity="{f["complexity"]}">'
            )
            if f["summary"]:
                s_text = (
                    str(f["summary"])
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                lines.append(f"      <summary>{s_text}</summary>")
            lines.append("      <content><![CDATA[")
            lines.append(sanitize_content(f["content"]))
            lines.append("      ]]></content>")
            lines.append("    </file>")
        lines.append("  </files>")
        lines.append("</workspace>")
        return "\n".join(lines)

    def format_output(self, format_type: str) -> str:
        self.files_registry.sort(key=lambda x: (x["complexity"], x["path"]))
        if format_type == "json":
            return self._format_json()
        if format_type == "xml":
            return self._format_xml()
        return self._format_text()

    def print_stats(self) -> None:
        print(f"Workspace Packager {PACKAGER_VERSION}")
        print(f"  Bundled: {self.scan_stats['bundled']}")
        print(f"  Skipped: {self.scan_stats['skipped']}")
        print(f"    - Binary/Ext: {self.scan_stats['skipped_details'].get('binary_or_ext', 0)}")
        print(f"    - Oversize:   {self.scan_stats['skipped_details'].get('oversize', 0)}")
        print(f"  Errors:  {self.scan_stats['errors']}")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_settings(
    config_path: Optional[pathlib.Path],
    profile_name: Optional[str],
) -> Dict[str, Any]:
    """Return effective settings from config + optional profile."""
    if config_path is None:
        return {}
    cfg = load_json_config(config_path)
    settings: Dict[str, Any] = {}

    # Project-level risk_rules
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
        prog="workspace_packager_v2.4",
        description=(
            "Aletheia Workspace Packager v2.4. "
            "Produces a workspace bundle with safe traversal, redaction, "
            "polyglot analysis, manifest mode, config integration, "
            "and staging-directory output routing."
        ),
    )
    parser.add_argument(
        "path", nargs="?", default=".", help="Project root to scan (path mode)."
    )
    parser.add_argument(
        "--manifest", default=None, metavar="CSV",
        help="Manifest CSV; package only files listed (abs_path column for reading).",
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
        help="Route output to this directory instead of auto-generating one.",
    )
    parser.add_argument(
        "--format", choices=["text", "json", "xml"], default="text",
        help="Output format.",
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="FILE",
        help="Explicit output filename.",
    )

    args = parser.parse_args()

    root_path = pathlib.Path(args.path).resolve()
    if not root_path.exists():
        print(f"ERROR: path does not exist: {root_path}", file=sys.stderr)
        sys.exit(1)

    # Resolve config settings
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

    packager = WorkspacePackager(root_path)

    if args.manifest:
        manifest_path = pathlib.Path(args.manifest)
        if not manifest_path.exists():
            print(f"ERROR: --manifest not found: {manifest_path}", file=sys.stderr)
            sys.exit(1)
        try:
            manifest_rows = load_manifest_csv(manifest_path)
        except Exception as exc:
            print(f"ERROR: reading manifest: {exc}", file=sys.stderr)
            sys.exit(1)
        packager.run_manifest_mode(manifest_rows, max_file_size, include_exts)
    else:
        effective_ignore = _DEFAULT_IGNORE_DIRS | set(exclude_dirs_extra)
        packager.run_path_mode(effective_ignore, include_exts, max_file_size, staging_dir)

    bundle_content = packager.format_output(args.format)
    packager.print_stats()

    # Determine output path
    if args.output:
        out_path = pathlib.Path(args.output)
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ext_map = {"text": "yaml", "json": "json", "xml": "xml"}
        fname = f"{root_path.name}_bundle_{ts}.{ext_map[args.format]}"
        if staging_dir:
            out_dir = staging_dir
        else:
            out_dir = pathlib.Path(f"{root_path.name}_bundle_{ts}")
            out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / fname

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(bundle_content, encoding=_ENCODING)
    except Exception as exc:
        print(f"ERROR writing output: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Bundle saved to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
