"""
Agnostic Semantic Slicer v5.6 - Research-Grade LLM Context Engine (Consolidated)
Purpose: Produce verifiable workspace bundles with deterministic hashing, syntax validation,
         safe traversal, optional redaction, polyglot summarization, and AST slicing.
         Completely domain-agnostic for any software architecture.

Consolidated Features (v5.5 + v5.6):
    - Restored CLI: --agent-role, --agent-task, --workers
    - Added Idempotency: _looks_like_formatter_output() prevents recursive bundle slicing.
    - Added Diagnostics: --heatmap and --explain <SLICE_ID>
    - Deep Text Layers: Layer 1.7 (Imports) and 1.8 (Entry Points) restored to text output.
    - External Module Tracking: Layer 1.5 now identifies external dependencies.
    - Git-aware scanning (--git-diff) and comprehensive Manifest processing (--manifest)
    - State mutation maps (tracking variable mutations across slices)
    - Agent reasoning checklists and Layer 2.5 dependency graphs

Snapshot-Compatible Improvements:
    - Stable Slice Identity (Content-derived semantic hashing vs line-number fragility).
    - Semantic Risk Model (Factoring in mutations and side-effects into scores with soft scaling).
    - Semantic Density Layer 2.2 (Compression proxy tracking LOC, calls, mutations, etc.).
    - Execution Flow Confidence (Confidence scores + Truncation flags for paths).
    - Granular External Dependencies (Classifying Stdlib vs Third-Party vs Internal).
    - Slice Role Confidence (Classifier hit-rates to prevent LLM over-assumption).
    - Analysis Uncertainties Layer X (Tracking dynamic imports, eval/exec, syntax skips).
    - Structural Motif Detection Layer 2.9 (Static pattern detection).
"""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import hashlib
import json
import logging
import math
import mimetypes
import os
import pathlib
import re
import sys
import csv
import subprocess
from typing import Any, Callable, Dict, List, Literal, Optional, Set, TypedDict


# ============================================================================
# CONSTANTS & DEFAULTS
# ============================================================================
MAX_FILE_SIZE_BYTES = 1_500_000
ENABLE_DETERMINISTIC_HASH = True
MAX_WORKERS_DEFAULT = 8
MAX_WORKERS_LIMIT = 32
AST_CACHE_VERSION = "agnostic-stage4-v7"
BUNDLE_SCHEMA_VERSION = "agnostic_bundle_v5.6_snapshot_r3"

DEFAULT_SYSTEM_PURPOSE = "General software system."
DEFAULT_RESEARCH_TARGET = "Analyze and safely modify the codebase based on user instructions."

DEFAULT_IGNORE_EXTENSIONS = [
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff", ".zip",
    ".gz", ".tar", ".tgz", ".bz2", ".xz", ".exe", ".dll", ".so",
    ".dylib", ".pdf", ".bin", ".class", ".pyc"
]

DEFAULT_IGNORE_DIRS = [
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", "tests", "test_suite"
]


# ============================================================================
# LOGGING SETUP
# ============================================================================
def setup_logging(verbose: bool = False) -> logging.Logger:
    log_level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger = logging.getLogger("semantic_slicer")
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger

logger = logging.getLogger("semantic_slicer")


# ============================================================================
# TYPE DEFINITIONS
# ============================================================================
class SkipDetails(TypedDict):
    binary_or_ext: int
    oversize: int
    outside_base: int
    recursive_bundle: int

class ScanStats(TypedDict):
    bundled: int
    skipped: int
    errors: int
    skipped_details: SkipDetails
    skipped_by_ext: Dict[str, int]

class FileFingerprint(TypedDict):
    sha1: str
    mtime_iso: str
    size_bytes: int


# ============================================================================
# SECURITY KERNEL
# ============================================================================
class SecurityKernel:
    SENSITIVE_PATTERNS = [
        re.compile(r"-----BEGIN[A-Z0-9 ]+KEY-----.*?-----END[A-Z0-9 ]+KEY-----", re.DOTALL),
        re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL),
        re.compile(r"(api_key|secret_key|auth_token)\s*[:=]\s*['\"][A-Za-z0-9+/=]{20,}['\"]", re.IGNORECASE),
        re.compile(r"(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    ]

    @staticmethod
    def compute_file_fingerprint(filepath: pathlib.Path) -> FileFingerprint:
        try:
            sha1_hash = hashlib.sha1()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    sha1_hash.update(chunk)
            
            sha1_str = sha1_hash.hexdigest()[:8]
            mtime = os.path.getmtime(filepath)
            mtime_iso = datetime.datetime.fromtimestamp(mtime).isoformat()
            size_bytes = filepath.stat().st_size
            
            return {"sha1": sha1_str, "mtime_iso": mtime_iso, "size_bytes": size_bytes}
        except Exception as e:
            logger.warning(f"Cannot compute fingerprint for {filepath}: {e}")
            return {"sha1": "unknown", "mtime_iso": "unknown", "size_bytes": 0}

    @staticmethod
    def is_binary(filepath: str, ignore_exts: List[str], scan_bytes: int = 2048) -> bool:
        if any(filepath.lower().endswith(ext) for ext in ignore_exts):
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
        for pattern in cls.SENSITIVE_PATTERNS:
            try:
                content = pattern.sub("[REDACTED_SENSITIVE_PATTERN]", content)
            except Exception as e:
                logger.debug(f"Regex pattern error during sanitization: {e}")

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


class ASTCache:
    def __init__(self, cache_dir: pathlib.Path, enabled: bool = True) -> None:
        self.cache_dir = cache_dir
        self.enabled = enabled
        if self.enabled:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Failed to initialize AST cache directory {cache_dir}: {e}")
                self.enabled = False

    def _cache_key(self, file_path: pathlib.Path, content: str) -> str:
        material = f"{AST_CACHE_VERSION}|{file_path.as_posix()}|{content}"
        return hashlib.sha256(material.encode("utf-8", errors="ignore")).hexdigest()

    def _cache_path(self, key: str) -> pathlib.Path:
        return self.cache_dir / f"{key}.json"

    def get(self, file_path: pathlib.Path, content: str) -> Optional[Dict[str, Any]]:
        if not self.enabled: return None
        path: Optional[pathlib.Path] = None
        try:
            key = self._cache_key(file_path, content)
            path = self._cache_path(key)
            if not path.exists(): return None
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def set(self, file_path: pathlib.Path, content: str, analysis: Dict[str, Any]) -> None:
        if not self.enabled: return
        tmp_path: Optional[pathlib.Path] = None
        try:
            key = self._cache_key(file_path, content)
            path = self._cache_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(analysis, handle, ensure_ascii=False)
            try:
                os.replace(tmp_path, path)
            except PermissionError:
                pass
        except Exception:
            pass
        finally:
            if tmp_path and tmp_path.exists():
                try: tmp_path.unlink()
                except Exception: pass


# ============================================================================
# POLYGLOT ANALYZER 
# ============================================================================
class CallVisitor(ast.NodeVisitor):
    def __init__(self, filename: str = "") -> None:
        self.calls: List[str] = []
        self.filename = filename

    def visit_Call(self, node: ast.Call) -> None:
        try:
            prefix = f"{self.filename}::" if self.filename else ""
            if isinstance(node.func, ast.Name): self.calls.append(f"{prefix}{node.func.id}")
            elif isinstance(node.func, ast.Attribute): self.calls.append(f"{prefix}{node.func.attr}")
        except Exception: pass
        self.generic_visit(node)

class ImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: List[str] = []
    def visit_Import(self, node: ast.Import) -> None:
        try:
            for alias in node.names:
                if alias.name: self.imports.append(alias.name)
        except Exception: pass
        self.generic_visit(node)
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        try:
            base_module = node.module or ""
            for alias in node.names:
                if base_module: self.imports.append(f"{base_module}.{alias.name}")
                else: self.imports.append(alias.name)
        except Exception: pass
        self.generic_visit(node)

class ASTSliceExtractor(ast.NodeVisitor):
    def __init__(self, source_code: str, filename: str) -> None:
        self.source_lines = source_code.splitlines()
        self.filename = filename.replace("\\", "/")
        self.slices: List[Dict[str, Any]] = []
        self.call_graph: List[str] = []
        self.uncertainties: List[Dict[str, str]] = []

    def _canonical_slice_id(self, node_name: str, node: ast.AST) -> str:
        """Stable Slice Identity: Uses structural hashing of the AST body to bypass formatting changes."""
        try:
            node_dump = ast.dump(node, annotate_fields=False, include_attributes=False)
            clean_dump = node_dump.replace(" ", "").replace("\n", "")
            body_hash = hashlib.sha1(clean_dump.encode("utf-8")).hexdigest()[:10]
        except Exception:
            body_hash = "unknown"
        return f"{self.filename}::{node_name}@{body_hash}"

    def _expr_to_text(self, node: Optional[ast.AST]) -> str:
        if node is None: return "unknown"
        try: return ast.unparse(node)
        except Exception: return getattr(node, "id", "unknown")

    def _extract_args(self, node: ast.AST) -> List[str]:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): return []
        args: List[str] = []
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            args.append(arg.arg)
        if node.args.vararg: args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg: args.append(f"**{node.args.kwarg.arg}")
        return args

    def _infer_return_type(self, node: ast.AST) -> str:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            return self._expr_to_text(node.returns)
        return "unknown"

    def _extract_mutations(self, node: ast.AST) -> List[str]:
        mutations: Set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, (ast.Name, ast.Attribute, ast.Subscript)):
                        mutations.add(self._expr_to_text(t))
            elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(child.target, (ast.Name, ast.Attribute, ast.Subscript)):
                    mutations.add(self._expr_to_text(child.target))
        return sorted(mutations)

    def _extract_side_effects(self, node: ast.AST, mutations: List[str], calls: List[str]) -> List[str]:
        effects: Set[str] = set()
        lowered_calls = {str(c).split('::')[-1].lower() for c in calls} # handle prefixed calls
        if mutations: effects.add("state_mutation")
        if lowered_calls & {"open", "write", "dump", "save", "tofile"}: effects.add("file_io")
        if lowered_calls & {"print", "logger", "info", "warning", "error", "debug"}: effects.add("logging")
        if lowered_calls & {"request", "get", "post", "fetch", "session"}: effects.add("network_io")
        if lowered_calls & {"execute", "commit", "query", "session", "add", "flush"}: effects.add("database_io")
        if lowered_calls & {"cuda", "cupy", "torch", "tensor", "device"}: effects.add("hw_acceleration")
        return sorted(effects) if effects else ["none"]

    def _classify_component(self, node_name: str) -> Dict[str, str]:
        """Slice Role Confidence Classifier."""
        token = node_name.lower()
        mapping = {
            "testing": ["test", "mock", "fixture"],
            "data_model": ["model", "dto", "schema", "entity"],
            "service_orchestrator": ["service", "handler", "manager", "dispatch"],
            "utility": ["util", "helper", "common"],
            "ui_component": ["view", "ui", "component", "render"],
            "api_controller": ["api", "route", "controller", "endpoint"],
            "repository": ["repo", "dao", "db"],
            "auditor_guard": ["audit", "verify", "check", "guard"],
            "logic_engine": ["logic", "reason", "compute"]
        }
        best_match = "general_component"
        max_hits = 0
        
        for c_type, keywords in mapping.items():
            hits = sum(1 for k in keywords if k in token)
            if hits > max_hits:
                max_hits = hits
                best_match = c_type
                
        confidence = "HIGH" if max_hits >= 2 else "MEDIUM" if max_hits == 1 else "LOW"
        return {"type": best_match, "confidence": confidence}

    def _extract_code(self, node: ast.AST) -> str:
        try:
            start = max(0, int(getattr(node, "lineno", 1)) - 1)
            end = min(len(self.source_lines), int(getattr(node, "end_lineno", getattr(node, "lineno", 1))))
            return "\n".join(f"{start + i + 1:4d} | {line}" for i, line in enumerate(self.source_lines[start:end]))
        except Exception:
            return "[ERROR: Could not extract code]"

    def _process_slice(self, node: ast.AST, slice_type: str) -> None:
        try:
            cv = CallVisitor(filename=self.filename)
            cv.visit(node)
            node_name = getattr(node, 'name', '<unknown>')
            calls = sorted(set(cv.calls))
            mutations = self._extract_mutations(node)
            
            # Uncertainty Tracking (eval, exec, getattr, runtime imports)
            lowered_calls = {str(c).split('::')[-1].lower() for c in calls}
            if lowered_calls & {"eval", "exec", "getattr", "setattr", "import_module", "__import__"}:
                self.uncertainties.append({
                    "type": "dynamic_behavior",
                    "slice": str(node_name),
                    "detail": "eval/exec/getattr/import"
                })
            
            # Semantic Density Mapping
            loc = getattr(node, "end_lineno", getattr(node, "lineno", 0)) - getattr(node, "lineno", 0) + 1
            raw_nodes = len(list(ast.walk(node)))
            cyclomatic_proxy = int(math.log(raw_nodes + 1) * 10) if raw_nodes > 0 else 0
            density_score_raw = loc + len(calls) * 2 + len(mutations) * 3 + cyclomatic_proxy
            density_level = "HIGH" if density_score_raw > 100 else "MEDIUM" if density_score_raw > 40 else "LOW"

            self.slices.append({
                "slice_id": self._canonical_slice_id(str(node_name), node),
                "type": slice_type,
                "name": node_name,
                "start_line": getattr(node, "lineno", 0),
                "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 0)),
                "code": self._extract_code(node),
                "calls": calls,
                "signature": {
                    "args": self._extract_args(node),
                    "returns": self._infer_return_type(node),
                    "calls": calls,
                    "side_effects": self._extract_side_effects(node, mutations, calls),
                },
                "mutations": mutations,
                "component": self._classify_component(str(node_name)),
                "density": {
                    "loc": loc,
                    "cyclomatic_proxy": cyclomatic_proxy,
                    "level": density_level,
                    "score": density_score_raw
                }
            })
        except Exception as e:
            logger.debug(f"Error processing {slice_type} slice: {e}")
        finally:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None: self._process_slice(node, "async_function")
    def visit_ClassDef(self, node: ast.ClassDef) -> None: self._process_slice(node, "class")
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None: self._process_slice(node, "function")
    def visit_Call(self, node: ast.Call) -> None:
        try:
            prefix = f"{self.filename}::"
            if isinstance(node.func, ast.Name): self.call_graph.append(f"{prefix}{node.func.id}")
            elif isinstance(node.func, ast.Attribute): self.call_graph.append(f"{prefix}{node.func.attr}")
        except Exception: pass
        self.generic_visit(node)


class PolyglotAnalyzer:
    def __init__(self, event_callback: Optional[Callable[..., None]] = None) -> None:
        self.callback = event_callback

    def _has_main_entrypoint(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
                if isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
                    for comp in node.test.comparators:
                        if isinstance(comp, ast.Constant) and comp.value == "__main__":
                            return True
        return False

    def analyze_python(self, content: str, filename: Optional[str] = None) -> Dict[str, Any]:
        try:
            tree = ast.parse(content)
            extractor = ASTSliceExtractor(content, filename or "unknown.py")
            extractor.visit(tree)
            
            iv = ImportVisitor()
            iv.visit(tree)
            
            return {
                "summary": {
                    "slices": extractor.slices,
                    "call_graph": sorted(set(extractor.call_graph)),
                    "import_graph": sorted(set(iv.imports)),
                    "is_entry_point": self._has_main_entrypoint(tree),
                    "syntax_valid": True,
                    "uncertainties": extractor.uncertainties,
                },
                "complexity": len(extractor.slices)
            }
        except SyntaxError as e:
            return {"summary": {"error": f"Syntax Error at line {e.lineno}: {e.msg}", "syntax_valid": False, "error_line": e.lineno}, "complexity": 999}
        except Exception:
            return {"summary": {"syntax_valid": True}, "complexity": 0}

    def analyze(self, filename: str, content: str) -> Dict[str, Any]:
        ext = pathlib.Path(filename).suffix.lower()
        if ext == '.py': return self.analyze_python(content, filename)
        
        # Generic handlers for config/docs
        if ext == '.json':
            try: return {"summary": {"keys": list(json.loads(content).keys())[:10], "syntax_valid": True}, "complexity": 0}
            except json.JSONDecodeError: return {"summary": {"error": "Invalid JSON", "syntax_valid": False}, "complexity": 0}
        if ext in {'.yaml', '.yml'}:
            return {"summary": {"keys": [m.group(1) for m in re.finditer(r'^([a-zA-Z0-9_-]+):', content, re.MULTILINE)][:15], "syntax_valid": True}, "complexity": 0}
        
        return {"summary": {"syntax_valid": True}, "complexity": 0}


# ============================================================================
# CORE PACKAGER
# ============================================================================
class WorkspacePackager:
    def __init__(
        self,
        target_files: List[pathlib.Path],
        base_path: pathlib.Path,
        ignore_exts: List[str],
        enable_redaction: bool = True,
        project_name: str = "workspace_bundle",
        focus_target: Optional[str] = None,
        depth: int = 0,
        append_rules: bool = False,
        custom_rules: Optional[List[str]] = None,
        deterministic: bool = False,
        agent_role: Optional[str] = None,
        agent_task: Optional[str] = None,
        agent_target: Optional[str] = None,
        system_purpose: str = DEFAULT_SYSTEM_PURPOSE,
        research_target: str = DEFAULT_RESEARCH_TARGET,
        workers: int = MAX_WORKERS_DEFAULT,
        ast_cache_enabled: bool = True,
        event_callback: Optional[Callable[..., None]] = None
    ) -> None:
        self.target_files = target_files
        self.base_path = base_path.resolve()
        self.ignore_exts = ignore_exts
        self.enable_redaction = enable_redaction
        self.project_name = project_name
        self.focus_target = focus_target
        self.depth = depth
        self.append_rules = append_rules
        self.custom_rules = custom_rules or [
            "Preserve existing architecture and public function signatures.",
            "Maintain consistency with the existing data model and testing patterns.",
            "Ensure safe error handling; do not swallow exceptions silently."
        ]
        self.deterministic = deterministic
        self.agent_role = agent_role
        self.agent_task = agent_task
        self.agent_target = agent_target
        self.system_purpose = system_purpose
        self.research_target = research_target
        self.workers = max(1, min(workers, MAX_WORKERS_LIMIT))
        self.event_callback = event_callback
        
        self.files_registry: List[Dict[str, Any]] = []
        self.syntax_errors: List[str] = []
        self.bundle_hash: Optional[str] = None
        self.scan_stats: ScanStats = {"bundled": 0, "skipped": 0, "errors": 0, "skipped_details": {"binary_or_ext": 0, "oversize": 0, "outside_base": 0, "recursive_bundle": 0}, "skipped_by_ext": {}}
        
        self.timestamp = "DETERMINISTIC_BUILD" if deterministic else datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.ast_cache = ASTCache(cache_dir=self.base_path / ".cache" / "semantic_ast", enabled=ast_cache_enabled)

    def _looks_like_formatter_output(self, content: str) -> bool:
        """Idempotency safeguard: check for existing bundle schema or headers."""
        if len(content) < 100: return False
        sample = content[:1000]
        return "agnostic_bundle_v" in sample or "# Workspace Bundle:" in sample or "BUNDLE_SCHEMA_VERSION" in sample

    def _track_skip(self, reason: Literal["binary_or_ext", "oversize", "outside_base", "recursive_bundle"], ext: str) -> None:
        self.scan_stats['skipped'] += 1
        self.scan_stats['skipped_details'][reason] += 1
        
        safe_ext = (ext or "").lower()
        self.scan_stats['skipped_by_ext'][safe_ext] = self.scan_stats['skipped_by_ext'].get(safe_ext, 0) + 1

    def _process_content(self, content: str) -> str:
        return SecurityKernel.sanitize_content(content) if self.enable_redaction else content

    def _process_single_file(self, path_obj: pathlib.Path, analyzer: "PolyglotAnalyzer") -> Dict[str, Any]:
        try:
            if not path_obj.exists() or not path_obj.is_file(): return {"status": "error"}
            if SecurityKernel.is_binary(str(path_obj), self.ignore_exts): return {"status": "skipped", "skip_reason": "binary_or_ext", "ext": path_obj.suffix}
            
            size = path_obj.stat().st_size
            if size > MAX_FILE_SIZE_BYTES: return {"status": "skipped", "skip_reason": "oversize", "ext": path_obj.suffix}

            with open(path_obj, "r", encoding="utf-8", errors="ignore") as handle:
                raw = handle.read()

            if self._looks_like_formatter_output(raw):
                return {"status": "skipped", "skip_reason": "recursive_bundle", "ext": path_obj.suffix}

            try: rel_path = path_obj.relative_to(self.base_path).as_posix()
            except ValueError: return {"status": "skipped", "skip_reason": "outside_base", "ext": path_obj.suffix}

            # AST Analysis
            cached = self.ast_cache.get(path_obj, raw) if path_obj.suffix == '.py' else None
            analysis = cached or analyzer.analyze(rel_path, raw)
            if not cached and path_obj.suffix == '.py': self.ast_cache.set(path_obj, raw, analysis)

            summary = analysis.get("summary") or {}
            syntax_error = f"{rel_path} (Line {summary.get('error_line', '?')}): {summary.get('error')}" if not summary.get("syntax_valid", True) else None

            return {
                "status": "bundled",
                "entry": {
                    "path": rel_path,
                    "size_bytes": size,
                    "content": raw,
                    "summary": summary,
                    "complexity": analysis.get("complexity", 0),
                    "fingerprint": SecurityKernel.compute_file_fingerprint(path_obj),
                },
                "syntax_error": syntax_error,
            }
        except Exception:
            return {"status": "error"}

    def _get_allowed_focus_slices(self) -> Set[str]:
        if not self.focus_target:
            return set()
            
        allowed_names = {self.focus_target.lower()}
        all_slices = []
        for f in self.files_registry:
            all_slices.extend((f.get('summary') or {}).get("slices", []))
            
        current_focus = set(allowed_names)
        for _ in range(self.depth):
            new_calls = set()
            for s in all_slices:
                if s.get('name', '').lower() in current_focus:
                    for c in s.get('calls', []):
                        # Ensure we strip prefix if present to match names
                        raw_call = c.split("::")[-1]
                        new_calls.add(raw_call.lower())
            
            allowed_names.update(new_calls)
            current_focus = new_calls
            
        return allowed_names

    def _build_slice_dependency_graph(self) -> Dict[str, List[str]]:
        name_to_slice_ids: Dict[str, List[str]] = {}
        for f in self.files_registry:
            for s in (f.get("summary") or {}).get("slices", []):
                name = str(s.get("name", "")).lower()
                if name: 
                    # Store by fully qualified and raw name to support context-aware calls
                    fq_name = f"{f.get('path')}::{name}"
                    name_to_slice_ids.setdefault(name, []).append(s.get("slice_id"))
                    name_to_slice_ids.setdefault(fq_name, []).append(s.get("slice_id"))

        graph: Dict[str, List[str]] = {}
        for f in self.files_registry:
            for s in (f.get("summary") or {}).get("slices", []):
                sid = s.get("slice_id")
                targets = set()
                for called in s.get("calls", []):
                    search_key = str(called).lower()
                    for target_sid in name_to_slice_ids.get(search_key, []):
                        if target_sid != sid: targets.add(target_sid)
                graph[sid] = sorted(targets)
        return graph

    def _build_reverse_dependency_graph(self, slice_graph: Dict[str, List[str]]) -> Dict[str, List[str]]:
        reverse_graph: Dict[str, List[str]] = {slice_id: [] for slice_id in slice_graph.keys()}
        for src, targets in slice_graph.items():
            for target in targets:
                reverse_graph.setdefault(target, []).append(src)
        for target in reverse_graph:
            reverse_graph[target] = sorted(set(reverse_graph[target]))
        return reverse_graph

    def _compute_slice_risk_scores(self, slice_graph: Dict[str, List[str]], reverse_graph: Dict[str, List[str]]) -> Dict[str, Dict[str, Any]]:
        """Semantic Risk Model: factors in structural connections, mutations, and system side-effects with log scale saturation."""
        risk_meta: Dict[str, Dict[str, Any]] = {}
        for f in self.files_registry:
            summary = f.get('summary') or {}
            is_entry = summary.get("is_entry_point", False)
            for s in summary.get("slices", []):
                sid = s.get("slice_id")
                callers_count = len(reverse_graph.get(sid, []))
                callees_count = len(slice_graph.get(sid, []))
                
                muts_count = len(s.get("mutations", []))
                effects = s.get("signature", {}).get("side_effects", [])
                
                effect_weight = 0
                for e in effects:
                    if e in {"file_io", "network_io", "database_io", "hw_acceleration"}:
                        effect_weight += 15
                    elif e == "state_mutation":
                        effect_weight += 5
                    elif e == "logging":
                        effect_weight += 2
                
                raw_score = callers_count * 20 + callees_count * 10 + muts_count * 5 + effect_weight + (20 if is_entry else 0)
                # Soft scaling to prevent harsh clumping at 100
                score = int(100 * (1 - math.exp(-raw_score / 100.0)))
                
                level = "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MEDIUM" if score >= 40 else "LOW"
                risk_meta[sid] = {
                    "slice_id": sid,
                    "file_path": f.get("path", "unknown"),
                    "slice_name": s.get("name", "unknown"),
                    "callers": reverse_graph.get(sid, []),
                    "callees": slice_graph.get(sid, []),
                    "callers_count": callers_count,
                    "callees_count": callees_count,
                    "mutations_count": muts_count,
                    "side_effects_weight": effect_weight,
                    "entrypoint_context": is_entry,
                    "risk_score": score,
                    "risk_level": level,
                }
        return risk_meta

    def _build_patch_safety_map(self, risk_meta: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        recommendations = {
            "CRITICAL": "review only", "HIGH": "minimal patch only", 
            "MEDIUM": "bounded surgical edit", "LOW": "safe to refactor"
        }
        safety_map: Dict[str, Dict[str, Any]] = {}
        for slice_id, meta in sorted(risk_meta.items()):
            risk_level = str(meta.get("risk_level", "LOW"))
            safety_map[slice_id] = {
                "risk": risk_level,
                "recommended": recommendations.get(risk_level, "manual review"),
                "slice_name": meta.get("slice_name", "unknown"),
                "file_path": meta.get("file_path", "unknown"),
            }
        return safety_map

    def _build_patch_target_validation_registry(self) -> List[Dict[str, Any]]:
        registry: List[Dict[str, Any]] = []
        for f in self.files_registry:
            fp = (f.get("fingerprint") or {}).get("sha1", "unknown")
            for s in (f.get('summary') or {}).get("slices", []):
                registry.append({
                    "slice_id": s.get("slice_id"),
                    "file": f.get("path"),
                    "lines": {"start": int(s.get("start_line", 0) or 0), "end": int(s.get("end_line", 0) or 0)},
                    "fingerprint": fp,
                })
        return registry

    def _build_state_mutation_map(self) -> Dict[str, Dict[str, List[str]]]:
        state_map: Dict[str, Dict[str, List[str]]] = {}
        for file_meta in self.files_registry:
            path = str(file_meta.get("path", "unknown"))
            summary = file_meta.get("summary") or {}
            for slice_meta in summary.get("slices", []):
                slice_name = str(slice_meta.get("name", "unknown"))
                for var_name in sorted(set(slice_meta.get("mutations", []))):
                    bucket = state_map.setdefault(path, {}).setdefault(var_name, [])
                    marker = f"{slice_name}()"
                    if marker not in bucket:
                        bucket.append(marker)
        return {k: state_map[k] for k in sorted(state_map.keys())}

    def _build_execution_paths(self, slice_graph: Dict[str, List[str]]) -> Dict[str, Dict[str, Any]]:
        """Execution Flow Confidence mapping + Truncation Tracking."""
        file_entry_slices: Dict[str, Dict[str, Any]] = {}
        for file_meta in self.files_registry:
            summary = file_meta.get("summary") or {}
            if not summary.get("is_entry_point"): continue
            
            entry_candidates: List[str] = []
            confidence = "HIGH"
            
            for s in summary.get("slices", []):
                if str(s.get("name", "")).lower() in {"main", "run", "start", "cli", "entrypoint"}:
                    entry_candidates.append(s.get("slice_id"))
                    
            if not entry_candidates:
                confidence = "MEDIUM"
                entry_candidates = [s.get("slice_id") for s in summary.get("slices", []) if s.get("type") in {"function", "async_function"}]
                
            uncerts = summary.get("uncertainties", [])
            dynamic_count = sum(1 for u in uncerts if isinstance(u, dict) and u.get("type") == "dynamic_behavior")
            if dynamic_count >= 2:
                confidence = "LOW"
            elif dynamic_count == 1 and confidence == "HIGH":
                confidence = "MEDIUM"
                
            if entry_candidates:
                file_entry_slices[file_meta.get("path", "unknown")] = {
                    "roots": [sid for sid in entry_candidates if sid], 
                    "confidence": confidence
                }

        execution_paths: Dict[str, Dict[str, Any]] = {}
        for file_path, entry_info in file_entry_slices.items():
            discovered: List[str] = []
            visited: Set[str] = set()
            frontier = list(entry_info["roots"])
            depth = 0
            while frontier and depth < 6: # max depth
                next_frontier: List[str] = []
                for node in frontier:
                    if node in visited: continue
                    visited.add(node)
                    discovered.append(node)
                    for child in slice_graph.get(node, []):
                        if child not in visited: next_frontier.append(child)
                frontier = next_frontier
                depth += 1
            execution_paths[file_path] = {
                "path": discovered,
                "confidence": entry_info["confidence"],
                "truncated": depth >= 6
            }

        return execution_paths

    def _extract_system_architecture_context(self) -> Dict[str, Any]:
        contracts: List[str] = []
        observability: List[str] = []
        failure_conds: List[str] = []
        components: Dict[str, Dict[str, Any]] = {}
        external_modules: Dict[str, Set[str]] = {"stdlib": set(), "third_party": set()}

        assert_pattern = re.compile(r"\bassert\s+(.+)")
        raise_pattern = re.compile(r"\braise\s+([A-Za-z]+(?:Error|Exception))\b")
        log_pattern = re.compile(r"\b(?:logger|logging|log|metrics|stats)\.(info|warning|error|debug|critical)\b(.+)")
        stdlib_names = getattr(sys, 'stdlib_module_names', set(['os', 'sys', 're', 'math', 'json', 'datetime', 'hashlib', 'pathlib', 'logging', 'subprocess', 'ast', 'concurrent', 'csv', 'argparse', 'typing', 'collections', 'itertools', 'functools']))

        internal_paths = {f['path'].replace('.py', '').replace('/', '.') for f in self.files_registry}
        internal_bases = {p.split('.')[0] for p in internal_paths}

        for file_meta in self.files_registry:
            path = file_meta.get("path", "unknown")
            content = file_meta.get("content", "")
            summary = file_meta.get("summary") or {}
            
            # External Module Tracking (Granular Classification)
            for imp in summary.get("import_graph", []):
                base_mod = imp.split('.')[0]
                if base_mod not in internal_bases:
                    if base_mod in stdlib_names:
                        external_modules["stdlib"].add(imp)
                    else:
                        external_modules["third_party"].add(imp)

            for slice_meta in summary.get("slices", []):
                comp = slice_meta.get("component", {})
                comp_type = comp.get("type", "general_component")
                if comp_type != "general_component":
                    components[f"{path}::{slice_meta.get('name')}"] = {
                        "type": comp_type, 
                        "slice_id": slice_meta.get("slice_id"),
                        "confidence": comp.get("confidence", "LOW")
                    }

            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'): continue
                
                # Contracts
                if m := assert_pattern.search(line): contracts.append(f"Assertion constraint: {m.group(1)[:50]}")
                elif m := raise_pattern.search(line): failure_conds.append(f"Throws {m.group(1)}: {line[:50]}")
                
                # Observability
                if m := log_pattern.search(line): observability.append(f"{m.group(1).upper()} Log trigger: {line[:60]}")

        def dedupe(items: List[str], limit=30) -> List[str]:
            seen, out = set(), []
            for x in items:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
                    if len(out) >= limit: break
            return out

        return {
            "system_contracts": dedupe(contracts),
            "observability_telemetry": dedupe(observability),
            "failure_conditions": dedupe(failure_conds),
            "external_dependencies": {
                "stdlib": sorted(external_modules["stdlib"]),
                "third_party": sorted(external_modules["third_party"])
            },
            "component_registry": components,
        }

    def _detect_structural_motifs(self, layer_2_intel: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Static pattern detection to infer structural motifs like stateful handlers."""
        motifs = []
        stateful_io_handlers = []
        pure_compute = []
        
        for file_data in layer_2_intel:
            for s in file_data.get("slices", []):
                effects = s.get("signature", {}).get("side_effects", [])
                
                if "state_mutation" in effects and any(io in effects for io in ["file_io", "network_io", "database_io"]):
                    stateful_io_handlers.append(s.get("slice_id"))
                
                if not effects or effects == ["none"]:
                    pure_compute.append(s.get("slice_id"))

        if len(stateful_io_handlers) >= 2:
            motifs.append({
                "pattern": "stateful_io_handler",
                "slices": stateful_io_handlers,
                "confidence": "HIGH" if len(stateful_io_handlers) >= 3 else "MEDIUM"
            })
            
        if len(pure_compute) >= 3:
            motifs.append({
                "pattern": "pure_compute_engine",
                "slices": pure_compute,
                "confidence": "HIGH"
            })
            
        return motifs

    @staticmethod
    def _agent_reasoning_checklist() -> List[str]:
        return [
            "Verify SLICE_ID exists",
            "Check slice risk level",
            "Verify no system contract violation",
            "Ensure side-effects are preserved or safely migrated",
            "Verify system modification rules are followed",
        ]

    def _generate_tree_map(self) -> str:
        lines: List[str] = [f"{self.project_name}/"]
        seen_dirs: Set[str] = set()
        for p in sorted(pathlib.Path(f['path']) for f in self.files_registry):
            indent = 0
            for i, part in enumerate(p.parts[:-1]):
                d = pathlib.Path(*p.parts[:i + 1])
                if str(d) not in seen_dirs:
                    lines.append(f"{'  ' * (indent + 1)}|-- {part}/")
                    seen_dirs.add(str(d))
                indent += 1
            lines.append(f"{'  ' * (indent + 1)}|-- {p.parts[-1]}")
        return "\n".join(lines)

    def _generate_json_output(self) -> str:
        try:
            slice_graph = self._build_slice_dependency_graph()
            reverse_graph = self._build_reverse_dependency_graph(slice_graph)
            risk_meta = self._compute_slice_risk_scores(slice_graph, reverse_graph)
            execution_paths = self._build_execution_paths(slice_graph)
            allowed_slices = self._get_allowed_focus_slices()
            arch_context = self._extract_system_architecture_context()
            patch_registry = self._build_patch_target_validation_registry()
            patch_safety = self._build_patch_safety_map(risk_meta)
            state_mutation_map = self._build_state_mutation_map()

            layer_2_intelligence: List[Dict[str, Any]] = []
            for f in self.files_registry:
                summary = f.get('summary')
                if not summary: continue

                raw_slices = summary.get("slices", [])
                if self.focus_target:
                    raw_slices = [s for s in raw_slices if str(s.get('name', '')).lower() in allowed_slices]

                sanitized_slices = [
                    {
                        **s,
                        "calls": sorted(set(s.get("calls", []))),
                        "code": self._process_content(str(s.get("code", ""))),
                    }
                    for s in raw_slices
                ]

                layer_2_intelligence.append({
                    "path": f['path'],
                    "fingerprint": f.get('fingerprint', {}),
                    "call_graph": sorted(set(summary.get("call_graph", []))),
                    "import_graph": summary.get("import_graph", []),
                    "is_entry_point": summary.get("is_entry_point", False),
                    "slices": sanitized_slices,
                    "syntax_valid": summary.get("syntax_valid", True),
                    "metadata": summary if summary and not summary.get("slices") else None
                })

            payload: Dict[str, Any] = {
                "meta": {
                    "project": self.project_name,
                    "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
                    "system_purpose": self.system_purpose,
                    "generated_at": None if self.deterministic else self.timestamp,
                    "deterministic": self.deterministic,
                    "stats": self.scan_stats,
                    "agent_context": {"role": self.agent_role, "task": self.agent_task, "target": self.agent_target or self.focus_target},
                },
                "verification": {
                    "bundle_hash": None,
                    "estimated_tokens": None,
                    "syntax_errors": self.syntax_errors,
                    "files_with_syntax_issues": len(self.syntax_errors),
                },
                "layer_1_topology": self._generate_tree_map(),
                "layer_1_7_import_graph": [{"path": f['path'], "imports": f['summary'].get("import_graph", []) if f.get('summary') else []} for f in self.files_registry if (f.get('summary') or {}).get("import_graph")],
                "layer_1_8_entry_points": [f['path'] for f in self.files_registry if (f.get('summary') or {}).get("is_entry_point", False)],
                "layer_2_intelligence": layer_2_intelligence,
                "layer_2_2_semantic_density": [
                    {
                        "path": f['path'],
                        "density_map": [
                            {
                                "slice_name": s['name'],
                                "slice_id": s['slice_id'],
                                **s.get("density", {}),
                                "calls_count": len(s.get("calls", [])),
                                "mutations_count": len(s.get("mutations", [])),
                                "side_effects_count": len(s.get("signature", {}).get("side_effects", [])),
                            }
                            for s in (f.get("summary") or {}).get("slices", [])
                        ]
                    }
                    for f in self.files_registry if f.get('summary')
                ],
                "layer_2_5_slice_dependency_graph": slice_graph,
                "layer_2_6_execution_flow": execution_paths,
                "layer_2_7_patch_collision_risk": risk_meta,
                "layer_2_8_patch_target_validation": patch_registry,
                "layer_2_9_structural_motifs": self._detect_structural_motifs(layer_2_intelligence),
                "layer_3_full_files": [] if self.focus_target else [
                    {
                        "path": f['path'],
                        "size_bytes": f['size_bytes'],
                        "complexity": f['complexity'],
                        "fingerprint": f.get('fingerprint', {}),
                        "content": self._process_content(f['content']),
                    }
                    for f in self.files_registry
                ],
                "layer_x_uncertainties": {
                    "syntax_errors": self.syntax_errors,
                    "dynamic_behaviors": [
                        {"path": f['path'], "flags": f['summary'].get("uncertainties", [])}
                        for f in self.files_registry if (f.get('summary') or {}).get("uncertainties")
                    ]
                },
                "system_architecture_context": arch_context,
                "system_modification_rules": self.custom_rules,
                "research_target": self.research_target,
                "patch_safety": patch_safety,
                "state_mutation_map": state_mutation_map,
                "agent_reasoning_checklist": self._agent_reasoning_checklist(),
            }
            interim = json.dumps(payload, indent=2, sort_keys=True, default=str)
            estimated_tokens = len(interim) // 4
            self.bundle_hash = hashlib.sha256(interim.encode("utf-8")).hexdigest()[:12]
            
            verification = payload.get("verification")
            if isinstance(verification, dict):
                verification["bundle_hash"] = self.bundle_hash
                verification["estimated_tokens"] = estimated_tokens
            
            payload["meta"]["bundle_hash"] = self.bundle_hash
            
            return json.dumps(payload, indent=2, sort_keys=True, default=str)
        except Exception as e:
            logger.error(f"Error generating JSON output: {e}")
            return json.dumps({"error": str(e)}, indent=2)

    def _generate_text_output(self) -> str:
        lines: List[str] = []
        arch_context = self._extract_system_architecture_context()
        slice_graph = self._build_slice_dependency_graph()
        reverse_graph = self._build_reverse_dependency_graph(slice_graph)
        risk_meta = self._compute_slice_risk_scores(slice_graph, reverse_graph)
        execution_paths = self._build_execution_paths(slice_graph)
        allowed_slices = self._get_allowed_focus_slices()
        patch_registry = self._build_patch_target_validation_registry()
        patch_safety = self._build_patch_safety_map(risk_meta)
        state_mutation_map = self._build_state_mutation_map()
        
        # --- HEADER ---
        header = [
            f"# Workspace Bundle: {self.project_name}",
            f"# Generated: {self.timestamp}",
            "# Estimated Tokens: [COMPUTED]",
            f"# Bundle Schema Version: {BUNDLE_SCHEMA_VERSION}",
            f"# System Purpose: {self.system_purpose}",
            f"# Research Target: {self.research_target}",
            ""
        ]
        if self.agent_role or self.agent_task:
            header.extend(["# AGENT_CONTEXT", f"# ROLE: {self.agent_role or 'any'}", f"# TASK: {self.agent_task or 'any'}", ""])

        if self.focus_target:
            header.append(f"# FOCUS MODE: Target '{self.focus_target}' (Depth: {self.depth})")
            header.append("")

        if self.syntax_errors:
            lines.extend(["[SYNTAX VALIDITY WARNINGS]", "==================================================================", "The following files contain syntax errors:", ""])
            for err in self.syntax_errors: lines.append(f"  • {err}")
            lines.append("")

        # --- TOPOLOGY ---
        lines.extend(["==================================================================", "LAYER 1: SYSTEM TOPOLOGY", "=================================================================="])
        lines.extend(self._generate_tree_map().splitlines())
        lines.append("")

        # --- SYSTEM ARCHITECTURE OVERVIEW ---
        lines.extend(["==================================================================", "LAYER 1.5: ARCHITECTURE OVERVIEW", "=================================================================="])
        lines.append("--- External Module Dependencies ---")
        arch_deps = arch_context.get("external_dependencies", {})
        if arch_deps.get("stdlib"):
            lines.append("  Standard Library:")
            lines.extend(f"    - {d}" for d in arch_deps["stdlib"])
        if arch_deps.get("third_party"):
            lines.append("  Third Party:")
            lines.extend(f"    - {d}" for d in arch_deps["third_party"])
        if not arch_deps.get("stdlib") and not arch_deps.get("third_party"):
            lines.append("  (None clearly detected)")
        
        lines.append("\n--- System Contracts & Invariants ---")
        lines.extend(f"  - {c}" for c in arch_context["system_contracts"]) if arch_context["system_contracts"] else lines.append("  (None clearly detected)")
        
        lines.append("\n--- Failure Conditions & Guardrails ---")
        lines.extend(f"  - {f}" for f in arch_context["failure_conditions"]) if arch_context["failure_conditions"] else lines.append("  (None clearly detected)")
        
        lines.append("\n--- Observability & Telemetry ---")
        lines.extend(f"  - {o}" for o in arch_context["observability_telemetry"]) if arch_context["observability_telemetry"] else lines.append("  (None clearly detected)")
        lines.append("")

        # --- IMPORT GRAPH (Restored Layer 1.7) ---
        lines.extend(["==================================================================", "LAYER 1.7: IMPORT GRAPH", "=================================================================="])
        for f in self.files_registry:
            imps = (f.get('summary') or {}).get("import_graph", [])
            if imps: lines.append(f"{f['path']}: {', '.join(imps)}")
        lines.append("")

        # --- ENTRY POINTS (Restored Layer 1.8) ---
        lines.extend(["==================================================================", "LAYER 1.8: ENTRY POINT DETECTION", "=================================================================="])
        entries = [f['path'] for f in self.files_registry if (f.get('summary') or {}).get("is_entry_point")]
        if entries: lines.extend(f"  • {e}" for e in entries)
        else: lines.append("  (No explicit entry points detected)")
        lines.append("")

        # --- CODE INTELLIGENCE ---
        lines.extend(["==================================================================", "LAYER 2: CODE INTELLIGENCE & SLICES", "=================================================================="])
        
        layer_2_intelligence_for_motifs = [] # Construct for _detect_structural_motifs in text mode
        for f in self.files_registry:
            summary = f.get('summary') or {}
            slices = summary.get("slices", [])
            
            if self.focus_target:
                slices = [s for s in slices if s.get('name', '').lower() in allowed_slices]
                if not slices: continue 
                
            layer_2_intelligence_for_motifs.append({"slices": slices})
                
            if slices:
                lines.append(f"--- Intelligence: {f['path']} ---")
                for s in slices:
                    sig = s.get("signature", {})
                    comp = s.get("component", {})
                    lines.extend([
                        f"\n[SLICE: {s['name']} | Type: {s['type']} | Component: {comp.get('type', 'generic')} (Confidence: {comp.get('confidence', 'LOW')}) | Lines {s['start_line']}-{s['end_line']}]",
                        f"SLICE_ID: {s.get('slice_id', 'unknown')}",
                        f"Signature Args: {', '.join(sig.get('args', [])) or 'none'}",
                        f"Returns: {sig.get('returns', 'unknown')}",
                        f"Side Effects: {', '.join(sig.get('side_effects', ['none']))}",
                        self._process_content(s['code'])
                    ])
                lines.append("\n" + ("-" * 40))

        # --- SEMANTIC DENSITY MAP ---
        lines.extend(["", "==================================================================", "LAYER 2.2: SEMANTIC DENSITY MAP", "=================================================================="])
        for f in self.files_registry:
            summary = f.get('summary') or {}
            slices = summary.get("slices", [])
            if self.focus_target:
                slices = [s for s in slices if s.get('name', '').lower() in allowed_slices]
            if slices:
                lines.append(f"--- File: {f['path']} ---")
                for s in slices:
                    d = s.get("density", {})
                    lines.append(f"Slice: {s['name']} | Density: {d.get('level')} | LOC: {d.get('loc')} | Calls: {len(s.get('calls', []))} | Muts: {len(s.get('mutations', []))} | Effects: {len(s.get('signature', {}).get('side_effects', []))} | Cyclomatic Proxy: {d.get('cyclomatic_proxy')}")

        # --- SLICE DEPENDENCY GRAPH ---
        lines.extend(["", "==================================================================", "LAYER 2.5: SLICE DEPENDENCY GRAPH", "=================================================================="])
        edge_count = 0
        for src, targets in sorted(slice_graph.items()):
            if targets:
                edge_count += len(targets)
                lines.append(src)
                for t in targets[:20]: lines.append(f"  ├── depends_on -> {t}")
        if not edge_count: lines.append("(No slice dependency edges resolved)")

        # --- EXECUTION FLOW ---
        lines.extend(["", "==================================================================", "LAYER 2.6: EXECUTION FLOW", "=================================================================="])
        flow_found = False
        for src_file, flow_data in sorted(execution_paths.items()):
            path_nodes = flow_data.get("path", [])
            if path_nodes:
                flow_found = True
                trunc_flag = " [TRUNCATED]" if flow_data.get("truncated") else ""
                lines.append(f"{src_file} [Confidence: {flow_data.get('confidence', 'LOW')}]{trunc_flag}")
                for i, node in enumerate(path_nodes[:30]):
                    lines.append(f"  {'└──' if i == len(path_nodes[:30])-1 else '├──'} {node}")
        if not flow_found: lines.append("(No execution flow reconstructed; no entry points found)")

        # --- PATCH COLLISION / RISK METADATA ---
        lines.extend(["", "==================================================================", "LAYER 2.7: PATCH COLLISION & RISK METADATA", "=================================================================="])
        if risk_meta:
            for sid, meta in sorted(risk_meta.items(), key=lambda kv: (-kv[1]["risk_score"], kv[0]))[:80]:
                lines.extend([
                    f"SLICE_ID: {sid}",
                    f"  Risk: {meta['risk_level']} ({meta['risk_score']}/100) | callers={meta['callers_count']} callees={meta['callees_count']}"
                ])
                if meta["callers"]: lines.append(f"  Called by: {', '.join(meta['callers'][:8])}")
                lines.append("")
        else:
            lines.append("(No slice risk metadata available)")

        # --- PATCH TARGET VALIDATION & SAFETY ---
        lines.extend(["", "==================================================================", "LAYER 2.8: PATCH TARGET VALIDATION & SAFETY", "=================================================================="])
        if patch_registry:
            for entry in patch_registry[:100]:
                safety = patch_safety.get(entry["slice_id"], {})
                lines.extend([
                    entry["slice_id"],
                    f"    file: {entry['file']} ({entry['lines']['start']}-{entry['lines']['end']})",
                    f"    fingerprint: {entry['fingerprint']}",
                    f"    safety: {safety.get('risk', 'LOW')} -> {safety.get('recommended', 'manual review')}",
                    ""
                ])
        else:
            lines.append("(No patch target registry entries)")
            
        # --- STRUCTURAL MOTIFS ---
        motifs = self._detect_structural_motifs(layer_2_intelligence_for_motifs)
        if motifs:
            lines.extend(["", "==================================================================", "LAYER 2.9: STRUCTURAL MOTIFS", "=================================================================="])
            for m in motifs:
                lines.append(f"Pattern: {m['pattern']} (Confidence: {m['confidence']})")
                for sid in m['slices'][:5]:
                    lines.append(f"  - {sid}")
                if len(m['slices']) > 5:
                    lines.append(f"  - ... and {len(m['slices']) - 5} more")
            lines.append("")

        # --- STATE MUTATION MAP ---
        lines.extend(["", "==================================================================", "STATE MUTATION MAP", "==================================================================", "Mutation hotspots per file:", ""])
        if state_mutation_map:
            for file_path, vars_map in state_mutation_map.items():
                lines.append(file_path)
                for var_name, mutators in vars_map.items():
                    lines.append(f"  {var_name}")
                    for mutator in mutators[:10]:
                        lines.append(f"    - {mutator}")
                lines.append("")
        else:
            lines.append("(No state mutation signals detected)")

        # --- ANALYSIS UNCERTAINTIES ---
        lines.extend(["", "==================================================================", "LAYER X: ANALYSIS UNCERTAINTIES", "=================================================================="])
        has_uncertainties = False
        if self.syntax_errors:
            has_uncertainties = True
            lines.append("--- Syntax Errors (Missing AST Slices) ---")
            for err in self.syntax_errors: lines.append(f"  • {err}")
            
        for f in self.files_registry:
            uncerts = (f.get('summary') or {}).get("uncertainties", [])
            if uncerts:
                has_uncertainties = True
                lines.append(f"--- Dynamic/Unresolved in {f['path']} ---")
                for u in uncerts: 
                    u_str = f"{u.get('type')} in slice '{u.get('slice')}' ({u.get('detail')})" if isinstance(u, dict) else str(u)
                    lines.append(f"  • {u_str}")
                
        if not has_uncertainties:
            lines.append("(No significant uncertainties detected)")

        # --- AGENT REASONING CHECKLIST ---
        lines.extend(["", "==================================================================", "AGENT REASONING CHECKLIST", "==================================================================", "Before proposing a patch:", ""])
        for idx, item in enumerate(self._agent_reasoning_checklist(), start=1):
            lines.append(f"{idx}. {item}")

        # --- RULES ---
        lines.extend(["", "==================================================================", "SYSTEM MODIFICATION RULES", "=================================================================="])
        for rule in self.custom_rules: lines.append(f"  - {rule}")
        
        if self.append_rules:
            lines.extend([
                "\nPATCH INSTRUCTIONS:",
                "1. DO NOT regenerate entire files unless asked. Output surgical replacements.",
                "2. All code modifications MUST be output in the following strict PATCH format:",
                "\nPATCH_TYPE: FUNCTION_REPLACEMENT",
                "FILE: <filename>\nTARGET: <function or class name>\nLINES: <start>-<end>\nCODE: |\n  <replacement>",
                "\n3. Use exact SLICE_ID from Layer 2.8. Ensure exact line numbers are used."
            ])

        # --- FULL FILES ---
        if not self.focus_target:
            lines.extend(["", "==================================================================", "LAYER 3: FULL FILE CACHE", "=================================================================="])
            for f in self.files_registry:
                lines.extend([f"--- FULL FILE: {f['path']} ---", f"Size: {f['size_bytes']} bytes", "Content: |"])
                lines.extend(f"  {line}" for line in self._process_content(f['content']).splitlines())
                lines.append("")

        final_text = "\n".join(lines)
        estimated_tokens = len(final_text) // 4
        self.bundle_hash = hashlib.sha256(final_text.encode("utf-8")).hexdigest()[:12]
        
        header[2] = f"# Estimated Tokens: ~{estimated_tokens:,} tokens\n# Bundle Hash: {self.bundle_hash}"
        if self.focus_target: header.append(f"# Layer 3 (full files) omitted in focus mode\n")
        return "\n".join(header) + "\n" + final_text

    def print_heatmap(self, risk_meta: Dict[str, Dict[str, Any]]) -> None:
        """Diagnostic: Print architectural bottlenecks to console."""
        print("\n[ARCHITECTURAL HEATMAP]")
        print("="*40)
        sorted_risk = sorted(risk_meta.values(), key=lambda x: x['risk_score'], reverse=True)
        for item in sorted_risk[:15]:
            print(f"[{item['risk_level']}] Score: {item['risk_score']:3d} | {item['slice_id']}")
        print("="*40)

    def print_explanation(self, slice_id: str, risk_meta: Dict[str, Dict[str, Any]]) -> None:
        """Diagnostic: Print detailed lineage of a specific node."""
        if slice_id not in risk_meta:
            print(f"Error: Slice ID '{slice_id}' not found in registry.")
            return
        m = risk_meta[slice_id]
        print(f"\n[X-RAY: {slice_id}]")
        print(f"File: {m['file_path']}")
        print(f"Risk Profile: {m['risk_level']} ({m['risk_score']}/100)")
        print(f"Inbound Calls (Dependency Source): {', '.join(m['callers']) or 'None'}")
        print(f"Outbound Calls (Dependency Sink):   {', '.join(m['callees']) or 'None'}")
        if m['entrypoint_context']: print("Note: This node is part of a primary entry-point execution path.")

    def run(self, format_type: str = "text", heatmap: bool = False, explain: Optional[str] = None) -> str:
        logger.info(f"Scanning {len(self.target_files)} targeted files...")
        analyzer = PolyglotAnalyzer(event_callback=self.event_callback)

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            future_to_path = {executor.submit(self._process_single_file, p, analyzer): p for p in self.target_files}
            for idx, future in enumerate(as_completed(future_to_path), start=1):
                try:
                    res = future.result()
                    if res.get("status") == "bundled":
                        self.files_registry.append(res["entry"])
                        self.scan_stats["bundled"] += 1
                        if res.get("syntax_error"): self.syntax_errors.append(res["syntax_error"])
                    elif res.get("status") == "skipped":
                        self._track_skip(res.get("skip_reason", "binary_or_ext"), res.get("ext", ""))
                    else:
                        self.scan_stats["errors"] += 1
                except Exception:
                    self.scan_stats["errors"] += 1

        self.files_registry.sort(key=lambda x: (x.get('complexity', 0), x.get('path', '')))
        
        logger.info(f"Scan complete - Bundled: {self.scan_stats['bundled']}, Skipped: {self.scan_stats['skipped']}, Errors: {self.scan_stats['errors']}")
        
        # Build graphs for diagnostics
        if heatmap or explain:
            slice_graph = self._build_slice_dependency_graph()
            reverse_graph = self._build_reverse_dependency_graph(slice_graph)
            risk_meta = self._compute_slice_risk_scores(slice_graph, reverse_graph)

            if heatmap: self.print_heatmap(risk_meta)
            if explain: self.print_explanation(explain, risk_meta)

        return self._generate_json_output() if format_type == "json" else self._generate_text_output()


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Agnostic Semantic Slicer v5.6 (Consolidated)")
    parser.add_argument("paths", nargs="*", help="Files or directories to package")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("-o", "--output", help="Output filename")
    parser.add_argument("--base-dir", default=".", help="Base directory")
    parser.add_argument("--manifest", help="Path to CSV or TXT file with file list")
    parser.add_argument("--git-diff", action="store_true", help="Scan Git-changed files")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--focus", help="Only output slices for this exact function/class name")
    parser.add_argument("--depth", type=int, default=0, help="Dependency resolution depth")
    parser.add_argument("--append-rules", action="store_true", help="Add LLM modification rules")
    parser.add_argument("--deterministic", action="store_true", help="Omit timestamp")
    parser.add_argument("--no-redaction", action="store_true", help="Disable secret/entropy redaction")
    parser.add_argument("--system-purpose", default=DEFAULT_SYSTEM_PURPOSE, help="Override system purpose description")
    parser.add_argument("--research-target", default=DEFAULT_RESEARCH_TARGET, help="Override agent task target")
    parser.add_argument("--rules", nargs="*", help="List of custom modification rules")
    parser.add_argument("--agent-role", help="Specify agent role context")
    parser.add_argument("--agent-task", help="Specify specific agent task (Research Target alias)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS_DEFAULT)
    parser.add_argument("--heatmap", action="store_true", help="Print bottleneck diagnostics")
    parser.add_argument("--explain", help="Explain specific Slice ID lineage")
    parser.add_argument("--ignore-dirs", nargs="*", default=DEFAULT_IGNORE_DIRS)
    parser.add_argument("--ignore-exts", nargs="*", default=DEFAULT_IGNORE_EXTENSIONS)
    
    args = parser.parse_args()
    setup_logging(args.verbose)

    # Alias Handling
    research_target = args.agent_task if args.agent_task else args.research_target

    base_path = pathlib.Path(args.base_dir).resolve()
    target_files = []

    def add_target_file(candidate: pathlib.Path) -> None:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(base_path)
            target_files.append(resolved)
        except ValueError:
            logger.warning(f"Skipping outside base-dir: {resolved}")

    # Standard Path Processing
    if args.paths:
        for p_str in args.paths:
            p = pathlib.Path(p_str).resolve()
            if p.is_file(): add_target_file(p)
            elif p.is_dir():
                for root, dirs, files in os.walk(p):
                    dirs[:] = [d for d in dirs if d not in args.ignore_dirs]
                    for f in files: add_target_file(pathlib.Path(root) / f)

    # Manifest Processing
    if args.manifest:
        manifest_path = pathlib.Path(args.manifest)
        if manifest_path.exists():
            try:
                if manifest_path.suffix.lower() == '.csv':
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        for row in csv.DictReader(f):
                            if 'abs_path' in row and row['abs_path']:
                                p = pathlib.Path(row['abs_path'])
                                if p.is_file(): add_target_file(p)
                else:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            clean_line = line.strip()
                            if clean_line and not clean_line.startswith('#'):
                                p = pathlib.Path(clean_line)
                                if p.is_file(): add_target_file(p)
            except Exception as e:
                logger.error(f"Cannot read manifest: {e}")

    # Git-aware diff scanning
    if args.git_diff:
        logger.info("Scanning for Git changes...")
        try:
            git_ls_files = subprocess.check_output(
                ['git', 'ls-files', '--modified', '--others', '--exclude-standard'],
                cwd=base_path, text=True, timeout=30
            ).splitlines()
            git_diff_cached = subprocess.check_output(
                ['git', 'diff', '--cached', '--name-only'],
                cwd=base_path, text=True, timeout=30
            ).splitlines()
            
            changed_files = set(git_ls_files + git_diff_cached)
            for changed_file in sorted(changed_files):
                full_path = (base_path / changed_file).resolve()
                if full_path.is_file(): add_target_file(full_path)
        except Exception as e:
            logger.warning(f"Failed to retrieve Git diff: {e}")

    target_files = sorted(set(target_files), key=lambda x: x.as_posix())

    if not target_files:
        sys.exit("No valid files found.")

    packager = WorkspacePackager(
        target_files=target_files,
        base_path=base_path,
        ignore_exts=args.ignore_exts,
        enable_redaction=not args.no_redaction,
        project_name=base_path.name,
        focus_target=args.focus,
        depth=args.depth,
        append_rules=args.append_rules,
        system_purpose=args.system_purpose,
        research_target=research_target,
        custom_rules=args.rules,
        agent_role=args.agent_role,
        agent_task=args.agent_task,
        workers=args.workers,
        deterministic=args.deterministic
    )

    bundle = packager.run(args.format, heatmap=args.heatmap, explain=args.explain)
    
    out_path = pathlib.Path(args.output) if args.output else base_path / f"{base_path.name}_bundle.{'json' if args.format == 'json' else 'txt'}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(bundle)

    print(f"Bundle generated: {out_path}")

if __name__ == "__main__":
    main()