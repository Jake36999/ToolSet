"""
Aletheia Workspace Packager (CLI) v2.3.1
Purpose: Produce a workspace bundle with safe traversal, PEM/entropy redaction,
         polyglot summarization, and multi-format output.

Updates:
    - Timestamped default output naming.
    - Granular skip tracking (binary/ignored extension vs oversize) with summary logging.
"""

import argparse
import ast
import datetime
import json
import math
import mimetypes
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Literal, Optional, TypedDict


MAX_FILE_SIZE_BYTES = 1_500_000
IGNORE_EXTENSIONS = [
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".tiff",
    ".zip",
    ".gz",
    ".tar",
    ".tgz",
    ".bz2",
    ".xz",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pdf",
    ".bin",
    ".class",
    ".pyc",
]
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".mypy_cache",
}


class SkipDetails(TypedDict):
    binary_or_ext: int
    oversize: int


class ScanStats(TypedDict):
    bundled: int
    skipped: int
    errors: int
    skipped_details: SkipDetails
    skipped_by_ext: Dict[str, int]


class PyStats(TypedDict):
    functions: List[str]
    classes: List[str]
    imports: int


# ============================================================================
# SECURITY KERNEL
# ============================================================================
class SecurityKernel:
    SENSITIVE_PATTERNS = [
        re.compile(r"-----BEGIN[A-Z0-9 ]+KEY-----.*?-----END[A-Z0-9 ]+KEY-----", re.DOTALL),
        re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL),
        re.compile(r"(api_key|secret_key|auth_token)\s*[:=]\s*['\"][A-Za-z0-9+/=]{20,}['\"]", re.IGNORECASE),
    ]

    @staticmethod
    def is_binary(filepath: str, scan_bytes: int = 2048) -> bool:
        if any(filepath.lower().endswith(ext) for ext in IGNORE_EXTENSIONS):
            return True
        try:
            with open(filepath, 'rb') as handle:
                chunk = handle.read(scan_bytes)
            if not chunk:
                return False
            if b'\x00' in chunk:
                return True
            guess, _ = mimetypes.guess_type(filepath)
            if guess and not guess.startswith(('text', 'application')):
                return True
            # Fallback heuristic: high non-text ratio
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x7F)))
            nontext = sum(byte not in text_chars for byte in chunk)
            return (nontext / len(chunk)) > 0.40
        except Exception:
            return True

    @staticmethod
    def calculate_entropy(text: str) -> float:
        if not text:
            return 0.0
        entropy = 0.0
        for idx in range(256):
            p_x = float(text.count(chr(idx))) / len(text)
            if p_x > 0:
                entropy -= p_x * math.log(p_x, 2)
        return entropy

    @classmethod
    def sanitize_content(cls, content: str) -> str:
        # 1) Block-level regex redaction
        for pattern in cls.SENSITIVE_PATTERNS:
            content = pattern.sub("[REDACTED_SENSITIVE_PATTERN]", content)

        # 2) Line-level entropy redaction
        sanitized_lines: List[str] = []
        for line in content.splitlines():
            if any(key in line.lower() for key in ['api', 'key', 'secret', 'token', 'auth', 'password']):
                if cls.calculate_entropy(line) > 4.5:
                    parts = line.split('=', 1)
                    prefix = parts[0] if len(parts) == 2 else line.split(':', 1)[0]
                    sanitized_lines.append(f"{prefix}= [REDACTED_HIGH_ENTROPY]")
                    continue
            sanitized_lines.append(line)
        return "\n".join(sanitized_lines)


# ============================================================================
# POLYGLOT ANALYZER
# ============================================================================
class PolyglotAnalyzer:
    def __init__(self, event_callback=None) -> None:
        self.callback = event_callback

    def analyze_python(self, content: str, filename: Optional[str] = None) -> Dict[str, Any]:
        stats: PyStats = {"functions": [], "classes": [], "imports": 0}
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    stats["imports"] += 1
                    if self.callback:
                        module_name = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                        self.callback("dependency_detected", {"source_file": filename, "module": module_name})
                elif isinstance(node, ast.FunctionDef):
                    stats["functions"].append(node.name)
                elif isinstance(node, ast.ClassDef):
                    stats["classes"].append(node.name)
            return {"summary": stats, "complexity": stats["imports"]}
        except SyntaxError:
            return {"summary": {"error": "Syntax Error"}, "complexity": 999}
        except Exception:
            return {"summary": {}, "complexity": 0}

    def analyze_json(self, content: str) -> Dict[str, Any]:
        try:
            data = json.loads(content)
            keys = list(data.keys()) if isinstance(data, dict) else ["<array>"]
            return {"summary": {"keys": keys[:10]}, "complexity": 0}
        except json.JSONDecodeError:
            return {"summary": {"error": "Invalid JSON"}, "complexity": 0}

    def analyze_markdown(self, content: str) -> Dict[str, Any]:
        headers = re.findall(r'^#{1,3}\s+(.*)', content, re.MULTILINE)
        return {"summary": {"headers": headers[:10]}, "complexity": 0}

    def analyze_yaml(self, content: str) -> Dict[str, Any]:
        """Heuristic YAML top-level key extractor without external deps."""
        keys: List[str] = []
        try:
            for line in content.splitlines():
                match = re.match(r'^([a-zA-Z0-9_-]+):', line)
                if match:
                    keys.append(match.group(1))
            return {"summary": {"keys": keys[:15]}, "complexity": 0}
        except Exception:
            return {"summary": {}, "complexity": 0}

    def analyze(self, filename: str, content: str) -> Dict[str, Any]:
        ext = pathlib.Path(filename).suffix.lower()
        if ext == '.py':
            return self.analyze_python(content, filename)
        if ext == '.json':
            return self.analyze_json(content)
        if ext in ['.yaml', '.yml']:
            return self.analyze_yaml(content)
        if ext in ['.md', '.markdown']:
            return self.analyze_markdown(content)
        return {"summary": {}, "complexity": 0}


# ============================================================================
# TRAVERSAL
# ============================================================================
def traverse_project(root_path: pathlib.Path):
    stack: List[pathlib.Path] = [root_path]
    while stack:
        current = stack.pop()
        if not current.is_dir():
            continue
        try:
            with os.scandir(current) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    if entry.name in IGNORE_DIRS or entry.name.startswith('.'):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(pathlib.Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        yield pathlib.Path(entry.path)
        except PermissionError:
            continue


# ============================================================================
# CORE PACKAGER
# ============================================================================
class WorkspacePackager:
    def __init__(self, root_path: pathlib.Path, event_callback=None):
        self.root = root_path.resolve()
        self.event_callback = event_callback
        self.files_registry: List[Dict[str, Any]] = []
        self.scan_stats: ScanStats = {
            "bundled": 0,
            "skipped": 0,
            "errors": 0,
            "skipped_details": {
                "binary_or_ext": 0,
                "oversize": 0,
            },
            "skipped_by_ext": {},
        }
        self.timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def _track_skip(self, reason: Literal["binary_or_ext", "oversize"], ext: str) -> None:
        self.scan_stats['skipped'] += 1
        if reason == "binary_or_ext":
            self.scan_stats['skipped_details']["binary_or_ext"] += 1
        else:
            self.scan_stats['skipped_details']["oversize"] += 1
        safe_ext = (ext or "").lower()
        self.scan_stats['skipped_by_ext'][safe_ext] = self.scan_stats['skipped_by_ext'].get(safe_ext, 0) + 1

    def _generate_tree_map(self) -> str:
        lines: List[str] = [f"{self.root.name}/"]
        paths = sorted(pathlib.Path(f['path']) for f in self.files_registry)
        seen_dirs: set[str] = set()
        for p in paths:
            parts = p.parts
            indent = 0
            for i, part in enumerate(parts[:-1]):
                d = pathlib.Path(*parts[:i + 1])
                if str(d) not in seen_dirs:
                    lines.append(f"{'  ' * (indent + 1)}|-- {part}/")
                    seen_dirs.add(str(d))
                indent += 1
            lines.append(f"{'  ' * (indent + 1)}|-- {parts[-1]}")
        return "\n".join(lines)

    def _generate_text_output(self) -> str:
        lines: List[str] = [
            f"# Workspace Bundle: {self.root.name}",
            f"# Generated: {self.timestamp}",
            "# Compliance: os.scandir traversal; binary guard; PEM + entropy redaction; polyglot summaries",
            "",
            "# Project Structure:",
        ]
        lines.extend(f"# {ln}" for ln in self._generate_tree_map().splitlines())
        lines.append("")

        for f in self.files_registry:
            lines.append(f"--- FILE: {f['path']} ---")
            lines.append(f"Size: {f['size_bytes']} bytes")
            summary = f['summary']
            if summary:
                parts = []
                if summary.get("classes"):
                    parts.append("Classes: " + ", ".join(summary["classes"]))
                if summary.get("functions"):
                    parts.append("Functions: " + ", ".join(summary["functions"]))
                if summary.get("keys"):
                    parts.append("Keys: " + ", ".join(summary["keys"]))
                if summary.get("headers"):
                    parts.append("Headers: " + ", ".join(summary["headers"]))
                lines.append(f"Summary: {'; '.join(parts)}")
            else:
                lines.append("Summary: (none)")
            lines.append("Content: |")
            for line in SecurityKernel.sanitize_content(f['content']).splitlines():
                lines.append(f"  {line}")
            lines.append("")
        return "\n".join(lines)

    def _generate_json_output(self) -> str:
        payload = {
            "meta": {
                "project": self.root.name,
                "generated_at": self.timestamp,
                "stats": self.scan_stats,
                "tree": self._generate_tree_map(),
            },
            "files": [
                {
                    "path": f['path'],
                    "size_bytes": f['size_bytes'],
                    "complexity": f['complexity'],
                    "summary": f['summary'],
                    "content": SecurityKernel.sanitize_content(f['content']),
                }
                for f in self.files_registry
            ],
        }
        return json.dumps(payload, indent=2)

    def _generate_xml_output(self) -> str:
        lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>', '<workspace>']
        lines.append(f'  <meta project="{self.root.name}" generated="{self.timestamp}">')
        lines.append("    <tree><![CDATA[")
        lines.append(self._generate_tree_map())
        lines.append("    ]]></tree>")
        lines.append("  </meta>")
        lines.append("  <files>")
        for f in self.files_registry:
            lines.append(f'    <file path="{f["path"]}" size="{f["size_bytes"]}" complexity="{f["complexity"]}">')
            if f['summary']:
                s_text = str(f['summary']).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                lines.append(f"      <summary>{s_text}</summary>")
            lines.append("      <content><![CDATA[")
            lines.append(SecurityKernel.sanitize_content(f['content']))
            lines.append("      ]]></content>")
            lines.append("    </file>")
        lines.append("  </files>")
        lines.append("</workspace>")
        return "\n".join(lines)

    def run(self, format_type: str = "text") -> str:
        print(f"Scanning root: {self.root}")
        analyzer = PolyglotAnalyzer(event_callback=self.event_callback)
        for path_obj in traverse_project(self.root):
            try:
                if SecurityKernel.is_binary(str(path_obj)):
                    self._track_skip("binary_or_ext", path_obj.suffix)
                    continue
                size = path_obj.stat().st_size
                if size > MAX_FILE_SIZE_BYTES:
                    self._track_skip("oversize", path_obj.suffix)
                    continue
                try:
                    with open(path_obj, "r", encoding="utf-8", errors="ignore") as handle:
                        raw = handle.read()
                except Exception:
                    self.scan_stats['errors'] += 1
                    continue

                analysis = analyzer.analyze(path_obj.name, raw)

                self.files_registry.append({
                    "path": path_obj.relative_to(self.root).as_posix(),
                    "size_bytes": size,
                    "content": raw,
                    "summary": analysis.get("summary"),
                    "complexity": analysis.get("complexity", 0),
                })
                self.scan_stats['bundled'] += 1
            except Exception:
                self.scan_stats['errors'] += 1

        self.files_registry.sort(key=lambda x: (x['complexity'], x['path']))
        print("Completed.")
        print(f"  Bundled: {self.scan_stats['bundled']}")
        print(f"  Skipped: {self.scan_stats['skipped']}")
        print(f"    - Binary/Ignored Ext: {self.scan_stats['skipped_details']['binary_or_ext']}")
        print(f"    - Oversize (>1.5MB):  {self.scan_stats['skipped_details']['oversize']}")
        if self.scan_stats["skipped_by_ext"]:
            print("  Skipped by extension:")
            for ext, count in sorted(self.scan_stats["skipped_by_ext"].items(), key=lambda kv: kv[1], reverse=True):
                label = ext or "<none>"
                print(f"    {label}: {count}")
        print(f"  Errors:  {self.scan_stats['errors']}")

        if format_type == "json":
            return self._generate_json_output()
        if format_type == "xml":
            return self._generate_xml_output()
        return self._generate_text_output()


def main() -> None:
    parser = argparse.ArgumentParser(description="Aletheia Workspace Packager v2.3.1 (CLI)")
    parser.add_argument("path", nargs="?", default=".", help="Project root to package")
    parser.add_argument("--format", choices=["text", "json", "xml"], default="text", help="Output format")
    parser.add_argument("-o", "--output", help="Output filename")
    args = parser.parse_args()

    root_path = pathlib.Path(args.path).resolve()
    if not root_path.exists():
        sys.exit(f"Error: path does not exist: {root_path}")

    packager = WorkspacePackager(root_path)
    bundle_content = packager.run(args.format)

    if args.output:
        out_path = pathlib.Path(args.output)
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = {"text": "yaml", "json": "json", "xml": "xml"}[args.format]
        out_dir = pathlib.Path(f"{root_path.name}_bundle_{ts}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{root_path.name}_bundle_{ts}.{ext}"

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(bundle_content)
    except Exception as exc:
        sys.exit(f"Error writing output: {exc}")

    print(f"Bundle saved to: {out_path.resolve()}")
    print(f"Stats: {packager.scan_stats}")


if __name__ == "__main__":
    main()
