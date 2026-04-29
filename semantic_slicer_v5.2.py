"""
Aletheia Workspace Packager (CLI) v5.2 - Research-Grade LLM Context Engine
Purpose: Produce verifiable workspace bundles with deterministic hashing, syntax validation,
         safe traversal, PEM/entropy redaction, polyglot summarization, and AST slicing.

Phase 1 Improvements:
    - Bundle Hash (SHA256) for context verification across agents
    - File Fingerprints (SHA1 + mtime) for stale bundle detection
    - Syntax Validity Tracking with warnings
    - Deterministic output (reproducible bundles)
    - Comprehensive structured logging
    - Enhanced error tracking and reporting

Phase 2 Improvements:
    - Import graph extraction (ast.Import / ast.ImportFrom)
    - Entry-point detection (if __name__ == "__main__")
    - Slice dependency graph (slice -> slice edges)
    - Execution flow reconstruction from entry-point functions
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
# PHASE 1 CONSTANTS
# ============================================================================
MAX_FILE_SIZE_BYTES = 1_500_000
ENABLE_DETERMINISTIC_HASH = True  # Omit timestamp for reproducible bundles
MAX_WORKERS_DEFAULT = 8
MAX_WORKERS_LIMIT = 32
AST_CACHE_VERSION = "stage4-v2"
BUNDLE_SCHEMA_VERSION = "aletheia_bundle_v5.2"
SYSTEM_PURPOSE = (
    "This system simulates nonlinear complex field dynamics with geometry feedback "
    "to test the hypothesis that prime-log spectral locking emerges in a conformal "
    "scalar field system."
)

IGNORE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff", ".zip",
    ".gz", ".tar", ".tgz", ".bz2", ".xz", ".exe", ".dll", ".so",
    ".dylib", ".pdf", ".bin", ".class", ".pyc"
}

IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache", "tests", "test_suite"
}


# ============================================================================
# LOGGING SETUP
# ============================================================================
def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure structured logging with appropriate verbosity."""
    log_level = logging.DEBUG if verbose else logging.INFO
    
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    
    logger = logging.getLogger("semantic_slicer")
    logger.setLevel(log_level)
    logger.handlers.clear()  # Remove defaults
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


class ScanStats(TypedDict):
    bundled: int
    skipped: int
    errors: int
    skipped_details: SkipDetails
    skipped_by_ext: Dict[str, int]


class FileFingerprint(TypedDict):
    """File identity for stale bundle detection."""
    sha1: str
    mtime_iso: str
    size_bytes: int


# ============================================================================
# SECURITY KERNEL (Enhanced Phase 1)
# ============================================================================
class SecurityKernel:
    """Enhanced security with fingerprinting and syntax tracking."""
    
    SENSITIVE_PATTERNS = [
        re.compile(r"-----BEGIN[A-Z0-9 ]+KEY-----.*?-----END[A-Z0-9 ]+KEY-----", re.DOTALL),
        re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL),
        re.compile(r"(api_key|secret_key|auth_token)\s*[:=]\s*['\"][A-Za-z0-9+/=]{20,}['\"]", re.IGNORECASE),
        re.compile(r"(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
    ]

    @staticmethod
    def compute_file_fingerprint(filepath: pathlib.Path) -> FileFingerprint:
        """
        Compute SHA1 hash and mtime for a file.
        
        Args:
            filepath: Path to file
            
        Returns:
            FileFingerprint with sha1, mtime_iso, and size_bytes
        """
        try:
            # Compute SHA1 of file content
            sha1_hash = hashlib.sha1()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    sha1_hash.update(chunk)
            
            sha1_str = sha1_hash.hexdigest()[:8]
            
            # Get modification time
            mtime = os.path.getmtime(filepath)
            mtime_iso = datetime.datetime.fromtimestamp(mtime).isoformat()
            
            # Get file size
            size_bytes = filepath.stat().st_size
            
            return {
                "sha1": sha1_str,
                "mtime_iso": mtime_iso,
                "size_bytes": size_bytes,
            }
        except Exception as e:
            logger.warning(f"Cannot compute fingerprint for {filepath}: {e}")
            return {
                "sha1": "unknown",
                "mtime_iso": "unknown",
                "size_bytes": 0,
            }

    @staticmethod
    def is_binary(filepath: str, scan_bytes: int = 2048) -> bool:
        """Safely determine if a file is binary."""
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
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x7F)))
            nontext = sum(byte not in text_chars for byte in chunk)
            return (nontext / len(chunk)) > 0.40
        except Exception:
            return True

    @staticmethod
    def calculate_entropy(text: str) -> float:
        """Calculate Shannon entropy of text."""
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
        """Sanitize content by redacting sensitive patterns."""
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
    """Filesystem cache for Python AST analysis results."""

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
        if not self.enabled:
            return None
        path: Optional[pathlib.Path] = None
        try:
            key = self._cache_key(file_path, content)
            path = self._cache_path(key)
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            logger.debug(f"AST cache contained invalid JSON for {file_path}; cache entry removed")
            return None
        except Exception as e:
            logger.debug(f"AST cache read miss/error for {file_path}: {e}")
            return None

    def set(self, file_path: pathlib.Path, content: str, analysis: Dict[str, Any]) -> None:
        if not self.enabled:
            return
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
                logger.debug(f"AST cache replace skipped due to Windows file lock: {path}")
        except Exception as e:
            logger.debug(f"AST cache write error for {file_path}: {e}")
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass


# ============================================================================
# POLYGLOT ANALYZER (Enhanced Phase 1)
# ============================================================================
class CallVisitor(ast.NodeVisitor):
    """Extract function and method calls from AST."""
    
    def __init__(self) -> None:
        self.calls: List[str] = []
        
    def visit_Call(self, node: ast.Call) -> None:
        """Extract called function/method names."""
        try:
            if isinstance(node.func, ast.Name):
                self.calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                self.calls.append(node.func.attr)
        except Exception:
            pass
        self.generic_visit(node)


class ImportVisitor(ast.NodeVisitor):
    """Extract import dependencies from AST."""

    def __init__(self) -> None:
        self.imports: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        try:
            for alias in node.names:
                if alias.name:
                    self.imports.append(alias.name)
        except Exception:
            pass
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        try:
            base_module = node.module or ""
            for alias in node.names:
                if base_module:
                    self.imports.append(f"{base_module}.{alias.name}")
                else:
                    self.imports.append(alias.name)
        except Exception:
            pass
        self.generic_visit(node)


class ASTSliceExtractor(ast.NodeVisitor):
    """Extract semantic code slices from Python AST with syntax tracking."""
    
    def __init__(self, source_code: str, filename: str) -> None:
        self.source_lines = source_code.splitlines()
        self.filename = filename.replace("\\", "/")
        self.slices: List[Dict[str, Any]] = []
        self.call_graph: List[str] = []
        self.syntax_valid = True

    def _canonical_slice_id(self, node_name: str, start_line_no: int, end_line_no: int) -> str:
        return f"{self.filename}::{node_name}@{start_line_no}-{end_line_no}"

    def _expr_to_text(self, node: Optional[ast.AST]) -> str:
        if node is None:
            return "unknown"
        try:
            return ast.unparse(node)
        except Exception:
            return getattr(node, "id", "unknown")

    def _extract_args(self, node: ast.AST) -> List[str]:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return []
        args: List[str] = []
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            args.append(arg.arg)
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        return args

    def _infer_return_type(self, node: ast.AST, calls: List[str]) -> str:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            return self._expr_to_text(node.returns)
        lowered_calls = {str(c).lower() for c in calls}
        if lowered_calls & {"fft", "ifft", "fftn", "ifftn"}:
            return "complex field"
        if lowered_calls & {"array", "asarray", "zeros", "ones"}:
            return "array"
        return "unknown"

    def _extract_mutations(self, node: ast.AST) -> List[str]:
        mutations: Set[str] = set()
        for child in ast.walk(node):
            target_node: Optional[ast.AST] = None
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    target_node = t
                    if isinstance(target_node, ast.Name):
                        mutations.add(target_node.id)
                    elif isinstance(target_node, ast.Attribute):
                        mutations.add(self._expr_to_text(target_node))
                    elif isinstance(target_node, ast.Subscript):
                        mutations.add(self._expr_to_text(target_node))
            elif isinstance(child, ast.AnnAssign):
                target_node = child.target
            elif isinstance(child, ast.AugAssign):
                target_node = child.target

            if target_node is not None:
                if isinstance(target_node, ast.Name):
                    mutations.add(target_node.id)
                elif isinstance(target_node, (ast.Attribute, ast.Subscript)):
                    mutations.add(self._expr_to_text(target_node))
        return sorted(mutations)

    def _extract_side_effects(self, node: ast.AST, mutations: List[str], calls: List[str]) -> List[str]:
        effects: Set[str] = set()
        lowered_calls = {str(c).lower() for c in calls}
        if mutations:
            effects.add("state_mutation")
        if lowered_calls & {"open", "write", "dump", "save", "to_hdf5", "h5py", "tofile"}:
            effects.add("file_io")
        if lowered_calls & {"print", "logger", "info", "warning", "error", "debug"}:
            effects.add("logging")
        if lowered_calls & {"cuda", "cupy", "fft", "ifft"}:
            effects.add("gpu_compute")
        if isinstance(node, ast.ClassDef):
            effects.add("class_definition")
        return sorted(effects) if effects else ["none"]

    def _classify_operator(self, node_name: str, calls: List[str]) -> str:
        token = node_name.lower()
        call_set = {str(c).lower() for c in calls}
        if "laplac" in token or call_set & {"fft", "ifft", "fftn", "ifftn"}:
            return "spectral differential operator"
        if any(key in token for key in ("nonlinear", "potential", "rhs")):
            return "polynomial potential"
        if any(key in token for key in ("omega", "conformal", "geometry", "metric", "tensor")):
            return "geometry mapping"
        if any(key in token for key in ("validate", "fidelity", "contract")):
            return "validation operator"
        return "general operator"

    def _classify_gpu_profile(self, node_name: str, calls: List[str]) -> Dict[str, str]:
        token = node_name.lower()
        call_set = {str(c).lower() for c in calls}
        if "cupy" in token or call_set & {"cupy", "cuda", "cp", "rawkernel", "elementwisekernel"}:
            return {"device": "GPU", "backend": "CuPy"}
        if call_set & {"fft", "ifft", "fftn", "ifftn", "rfft", "irfft"}:
            return {"device": "GPU", "backend": "FFT"}
        return {"device": "CPU/Unknown", "backend": "generic"}

    def _extract_code(self, node: ast.AST) -> str:
        """Extract source code for a node."""
        try:
            start_line_no = int(getattr(node, "lineno", 1))
            end_line_no = int(getattr(node, "end_lineno", start_line_no))
            start = max(0, start_line_no - 1)
            end = min(len(self.source_lines), end_line_no)
            
            extracted = []
            for i, line in enumerate(self.source_lines[start:end]):
                line_num = start + i + 1
                extracted.append(f"{line_num:4d} | {line}")
            
            return "\n".join(extracted)
        except Exception as e:
            logger.debug(f"Error extracting code for node at line {getattr(node, 'lineno', '?')}: {e}")
            return "[ERROR: Could not extract code]"

    def _process_slice(self, node: ast.AST, slice_type: str) -> None:
        """Process a node as a code slice."""
        try:
            cv = CallVisitor()
            cv.visit(node)
            
            node_name = getattr(node, 'name', '<unknown>')
            start_line_no = int(getattr(node, "lineno", 0))
            end_line_no = int(getattr(node, "end_lineno", start_line_no))
            calls = sorted(set(cv.calls))
            mutations = self._extract_mutations(node)
            signature_args = self._extract_args(node)
            return_type = self._infer_return_type(node, calls)
            side_effects = self._extract_side_effects(node, mutations, calls)
            operator_type = self._classify_operator(str(node_name), calls)
            gpu_profile = self._classify_gpu_profile(str(node_name), calls)
            
            self.slices.append({
                "slice_id": self._canonical_slice_id(str(node_name), start_line_no, end_line_no),
                "type": slice_type,
                "name": node_name,
                "start_line": start_line_no,
                "end_line": end_line_no,
                "code": self._extract_code(node),
                "calls": calls,
                "signature": {
                    "args": signature_args,
                    "returns": return_type,
                    "calls": calls,
                    "side_effects": side_effects,
                },
                "mutations": mutations,
                "operator_type": operator_type,
                "gpu_profile": gpu_profile,
            })
        except Exception as e:
            logger.debug(f"Error processing {slice_type} slice: {e}")
        finally:
            self.generic_visit(node)
        
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Process async function definitions."""
        self._process_slice(node, "async_function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Process class definitions."""
        self._process_slice(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Process function definitions."""
        self._process_slice(node, "function")

    def visit_Call(self, node: ast.Call) -> None:
        """Track global call graph."""
        try:
            if isinstance(node.func, ast.Name):
                self.call_graph.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                self.call_graph.append(node.func.attr)
        except Exception:
            pass
        self.generic_visit(node)


class PolyglotAnalyzer:
    """Analyze files in multiple languages with syntax tracking."""
    
    def __init__(self, event_callback: Optional[Callable[..., None]] = None) -> None:
        self.callback = event_callback

    def _has_main_entrypoint(self, tree: ast.AST) -> bool:
        """Detect if file contains: if __name__ == '__main__'."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if not isinstance(test, ast.Compare):
                continue
            if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
                continue
            if len(test.comparators) != 1:
                continue

            left_is_name = isinstance(test.left, ast.Name) and test.left.id == "__name__"
            right = test.comparators[0]
            right_value = getattr(right, "value", None) if isinstance(right, ast.Constant) else None

            if left_is_name and right_value == "__main__":
                return True
        return False

    def analyze_python(self, content: str, filename: Optional[str] = None) -> Dict[str, Any]:
        """Analyze Python code with syntax validation and AST slicing."""
        try:
            tree = ast.parse(content)
            extractor = ASTSliceExtractor(content, filename or "unknown.py")
            extractor.visit(tree)

            import_visitor = ImportVisitor()
            import_visitor.visit(tree)
            imports = sorted(set(import_visitor.imports))

            is_entry_point = self._has_main_entrypoint(tree)
            
            return {
                "summary": {
                    "slices": extractor.slices,
                    "call_graph": sorted(set(extractor.call_graph)),
                    "import_graph": imports,
                    "is_entry_point": is_entry_point,
                    "syntax_valid": True,
                },
                "complexity": len(extractor.slices)
            }
        except SyntaxError as e:
            logger.warning(f"Syntax error in {filename}: {e}")
            return {
                "summary": {
                    "error": f"Syntax Error at line {e.lineno}: {e.msg}",
                    "syntax_valid": False,
                    "error_line": e.lineno
                },
                "complexity": 999
            }
        except Exception as e:
            logger.warning(f"Error analyzing Python file {filename}: {e}")
            return {"summary": {"syntax_valid": True}, "complexity": 0}

    def analyze_json(self, content: str) -> Dict[str, Any]:
        """Analyze JSON content."""
        try:
            data = json.loads(content)
            keys = list(data.keys()) if isinstance(data, dict) else ["<array>"]
            return {"summary": {"keys": keys[:10], "syntax_valid": True}, "complexity": 0}
        except json.JSONDecodeError as e:
            logger.debug(f"Invalid JSON: {e}")
            return {"summary": {"error": "Invalid JSON", "syntax_valid": False}, "complexity": 0}

    def analyze_markdown(self, content: str) -> Dict[str, Any]:
        """Analyze Markdown content."""
        try:
            headers = re.findall(r'^#{1,3}\s+(.*)', content, re.MULTILINE)
            return {"summary": {"headers": headers[:10], "syntax_valid": True}, "complexity": 0}
        except Exception as e:
            logger.debug(f"Error analyzing Markdown: {e}")
            return {"summary": {"syntax_valid": True}, "complexity": 0}

    def analyze_yaml(self, content: str) -> Dict[str, Any]:
        """Analyze YAML content (heuristic parsing)."""
        keys: List[str] = []
        try:
            for line in content.splitlines()[:100]:
                match = re.match(r'^([a-zA-Z0-9_-]+):', line)
                if match:
                    keys.append(match.group(1))
            return {"summary": {"keys": keys[:15], "syntax_valid": True}, "complexity": 0}
        except Exception as e:
            logger.debug(f"Error analyzing YAML: {e}")
            return {"summary": {"syntax_valid": True}, "complexity": 0}

    def analyze(self, filename: str, content: str) -> Dict[str, Any]:
        """Dispatch to appropriate analyzer based on file extension."""
        try:
            ext = pathlib.Path(filename).suffix.lower()
            
            if ext == '.py':
                return self.analyze_python(content, filename)
            if ext == '.json':
                return self.analyze_json(content)
            if ext in {'.yaml', '.yml'}:
                return self.analyze_yaml(content)
            if ext in {'.md', '.markdown'}:
                return self.analyze_markdown(content)
            
            return {"summary": {"syntax_valid": True}, "complexity": 0}
        except Exception as e:
            logger.warning(f"Error analyzing {filename}: {e}")
            return {"summary": {"syntax_valid": True}, "complexity": 0}


# ============================================================================
# CORE PACKAGER (Phase 1 Enhanced)
# ============================================================================
class WorkspacePackager:
    """Package workspace for LLM processing with Phase 1 reliability features."""
    
    def __init__(
        self,
        target_files: List[pathlib.Path],
        base_path: pathlib.Path,
        project_name: str = "custom_bundle",
        focus_target: Optional[str] = None,
        depth: int = 0,
        append_rules: bool = False,
        deterministic: bool = False,
        agent_role: Optional[str] = None,
        agent_task: Optional[str] = None,
        agent_target: Optional[str] = None,
        workers: int = MAX_WORKERS_DEFAULT,
        ast_cache_enabled: bool = True,
        event_callback: Optional[Callable[..., None]] = None
    ) -> None:
        """Initialize packager with Phase 1 enhancements."""
        self.target_files = target_files
        self.base_path = base_path.resolve()
        self.project_name = project_name
        self.focus_target = focus_target
        self.depth = depth
        self.append_rules = append_rules
        self.deterministic = deterministic
        self.agent_role = agent_role
        self.agent_task = agent_task
        self.agent_target = agent_target
        self.workers = max(1, min(workers, MAX_WORKERS_LIMIT))
        self.ast_cache_enabled = ast_cache_enabled
        self.event_callback = event_callback
        
        self.files_registry: List[Dict[str, Any]] = []
        self.syntax_errors: List[str] = []
        self.bundle_hash: Optional[str] = None
        
        self.scan_stats: ScanStats = {
            "bundled": 0,
            "skipped": 0,
            "errors": 0,
            "skipped_details": {
                "binary_or_ext": 0,
                "oversize": 0,
                "outside_base": 0,
            },
            "skipped_by_ext": {},
        }
        
        if deterministic:
            self.timestamp = "DETERMINISTIC_BUILD"
            logger.info("Deterministic mode enabled - timestamp omitted")
        else:
            self.timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        cache_dir = self.base_path / ".cache" / "aletheia_ast"
        self.ast_cache = ASTCache(cache_dir=cache_dir, enabled=self.ast_cache_enabled)
        if self.ast_cache.enabled:
            logger.info(f"AST cache enabled at: {cache_dir}")
        else:
            logger.info("AST cache disabled")
        
        logger.info(
            f"Packager initialized: {len(target_files)} files, deterministic={deterministic}, "
            f"workers={self.workers}"
        )

    def _analyze_with_cache(
        self,
        analyzer: "PolyglotAnalyzer",
        path_obj: pathlib.Path,
        raw: str,
        analysis_name: str,
    ) -> Dict[str, Any]:
        """Run analysis, using AST cache for Python files when possible."""
        if path_obj.suffix.lower() != ".py":
            return analyzer.analyze(analysis_name, raw)

        cached = self.ast_cache.get(path_obj, raw)
        if cached is not None:
            return cached

        analysis = analyzer.analyze(analysis_name, raw)
        self.ast_cache.set(path_obj, raw, analysis)
        return analysis

    def _process_single_file(self, path_obj: pathlib.Path, analyzer: "PolyglotAnalyzer") -> Dict[str, Any]:
        """Process a single file and return normalized result for aggregation."""
        try:
            if not path_obj.exists() or not path_obj.is_file():
                return {"status": "error", "reason": "missing_or_not_file"}

            if SecurityKernel.is_binary(str(path_obj)):
                return {"status": "skipped", "skip_reason": "binary_or_ext", "ext": path_obj.suffix}

            size = path_obj.stat().st_size
            if size > MAX_FILE_SIZE_BYTES:
                return {"status": "skipped", "skip_reason": "oversize", "ext": path_obj.suffix}

            try:
                with open(path_obj, "r", encoding="utf-8", errors="ignore") as handle:
                    raw = handle.read()
            except OSError:
                return {"status": "error", "reason": "read_error"}

            try:
                rel_path = path_obj.relative_to(self.base_path).as_posix()
            except ValueError:
                return {"status": "skipped", "skip_reason": "outside_base", "ext": path_obj.suffix}

            analysis = self._analyze_with_cache(analyzer, path_obj, raw, rel_path)

            fingerprint = SecurityKernel.compute_file_fingerprint(path_obj)
            summary = analysis.get("summary") or {}

            syntax_error = None
            if not summary.get("syntax_valid", True):
                error_msg = summary.get("error", "Unknown syntax error")
                error_line = summary.get("error_line", "?")
                syntax_error = f"{rel_path} (Line {error_line}: {error_msg})"

            return {
                "status": "bundled",
                "entry": {
                    "path": rel_path,
                    "size_bytes": size,
                    "content": raw,
                    "summary": summary,
                    "complexity": analysis.get("complexity", 0),
                    "fingerprint": fingerprint,
                },
                "syntax_error": syntax_error,
            }

        except OSError:
            return {"status": "error", "reason": "os_error"}
        except Exception as e:
            logger.error(f"Unexpected error processing {path_obj}: {e}")
            return {"status": "error", "reason": "unexpected"}

    def _build_slice_maps(self) -> Dict[str, Any]:
        """Build helper maps for slice dependency and collision analysis."""
        slice_by_id: Dict[str, Dict[str, Any]] = {}
        function_name_to_slice_ids: Dict[str, List[str]] = {}

        for file_meta in self.files_registry:
            summary = file_meta.get("summary") or {}
            for slice_meta in summary.get("slices", []):
                slice_id = slice_meta.get("slice_id")
                if not slice_id:
                    continue
                slice_by_id[slice_id] = {
                    **slice_meta,
                    "file_path": file_meta.get("path", "unknown"),
                    "is_entry_point_file": summary.get("is_entry_point", False),
                }
                name = str(slice_meta.get("name", "")).lower()
                if name:
                    function_name_to_slice_ids.setdefault(name, []).append(slice_id)

        return {
            "slice_by_id": slice_by_id,
            "function_name_to_slice_ids": function_name_to_slice_ids,
        }

    def _build_slice_dependency_graph(self) -> Dict[str, List[str]]:
        """Build graph of slice_id -> dependent slice_ids using function call names."""
        maps = self._build_slice_maps()
        name_to_slice_ids: Dict[str, List[str]] = maps["function_name_to_slice_ids"]
        graph: Dict[str, List[str]] = {}

        for source_slice_id in maps["slice_by_id"].keys():
            graph.setdefault(source_slice_id, [])

        for file_meta in self.files_registry:
            summary = file_meta.get("summary") or {}
            for s in summary.get("slices", []):
                source_slice_id = s.get("slice_id")
                if not source_slice_id:
                    continue
                targets: Set[str] = set()
                for called_name in s.get("calls", []):
                    called_lower = str(called_name).lower()
                    for target_slice_id in name_to_slice_ids.get(called_lower, []):
                        if target_slice_id != source_slice_id:
                            targets.add(target_slice_id)
                graph[source_slice_id] = sorted(targets)

        return graph

    def _build_reverse_dependency_graph(self, slice_graph: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Build reverse edges to detect likely patch collisions."""
        reverse_graph: Dict[str, List[str]] = {slice_id: [] for slice_id in slice_graph.keys()}
        for src, targets in slice_graph.items():
            for target in targets:
                reverse_graph.setdefault(target, []).append(src)
        for target in reverse_graph:
            reverse_graph[target] = sorted(set(reverse_graph[target]))
        return reverse_graph

    def _compute_slice_risk_scores(
        self,
        slice_graph: Dict[str, List[str]],
        reverse_graph: Dict[str, List[str]],
    ) -> Dict[str, Dict[str, Any]]:
        """Compute per-slice risk metadata for multi-agent patch coordination."""
        maps = self._build_slice_maps()
        slice_by_id: Dict[str, Dict[str, Any]] = maps["slice_by_id"]
        risk_meta: Dict[str, Dict[str, Any]] = {}

        for slice_id, slice_meta in slice_by_id.items():
            callers_count = len(reverse_graph.get(slice_id, []))
            callees_count = len(slice_graph.get(slice_id, []))
            is_entry_point_file = bool(slice_meta.get("is_entry_point_file", False))

            raw_score = callers_count * 25 + callees_count * 15 + (20 if is_entry_point_file else 0)
            score = min(100, raw_score)

            if score >= 80:
                level = "CRITICAL"
            elif score >= 60:
                level = "HIGH"
            elif score >= 40:
                level = "MEDIUM"
            else:
                level = "LOW"

            risk_meta[slice_id] = {
                "slice_id": slice_id,
                "file_path": slice_meta.get("file_path", "unknown"),
                "slice_name": slice_meta.get("name", "unknown"),
                "callers": reverse_graph.get(slice_id, []),
                "callees": slice_graph.get(slice_id, []),
                "callers_count": callers_count,
                "callees_count": callees_count,
                "entrypoint_context": is_entry_point_file,
                "risk_score": score,
                "risk_level": level,
            }

        return risk_meta

    def _agent_header_lines(self) -> List[str]:
        """Build optional AGENT_CONTEXT header for multi-agent workflows."""
        if not (self.agent_role or self.agent_task or self.agent_target):
            return []

        lines = [
            "# AGENT_CONTEXT",
            f"# ROLE: {self.agent_role or 'unspecified'}",
            f"# TASK: {self.agent_task or 'unspecified'}",
            f"# TARGET: {self.agent_target or self.focus_target or 'unspecified'}",
            "",
        ]
        return lines

    def _build_execution_paths(self, slice_graph: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Reconstruct shallow execution paths starting from likely entry slices."""
        file_entry_slices: Dict[str, List[str]] = {}
        for file_meta in self.files_registry:
            summary = file_meta.get("summary") or {}
            if not summary.get("is_entry_point"):
                continue
            entry_candidates: List[str] = []
            for s in summary.get("slices", []):
                name = str(s.get("name", "")).lower()
                if name in {"main", "run", "start", "cli", "entrypoint"}:
                    entry_candidates.append(s.get("slice_id"))
            if not entry_candidates:
                for s in summary.get("slices", []):
                    if s.get("type") in {"function", "async_function"}:
                        entry_candidates.append(s.get("slice_id"))
            file_entry_slices[file_meta.get("path", "unknown")] = [sid for sid in entry_candidates if sid]

        execution_paths: Dict[str, List[str]] = {}
        for file_path, roots in file_entry_slices.items():
            discovered: List[str] = []
            visited: Set[str] = set()
            frontier = list(roots)
            depth = 0
            max_depth = 6
            while frontier and depth < max_depth:
                next_frontier: List[str] = []
                for node in frontier:
                    if node in visited:
                        continue
                    visited.add(node)
                    discovered.append(node)
                    for child in slice_graph.get(node, []):
                        if child not in visited:
                            next_frontier.append(child)
                frontier = next_frontier
                depth += 1
            execution_paths[file_path] = discovered

        return execution_paths

    def _track_skip(self, reason: Literal["binary_or_ext", "oversize", "outside_base"], ext: str) -> None:
        """Track skipped files."""
        self.scan_stats['skipped'] += 1
        if reason == "binary_or_ext":
            self.scan_stats['skipped_details']["binary_or_ext"] += 1
        elif reason == "outside_base":
            self.scan_stats['skipped_details']["outside_base"] += 1
        else:
            self.scan_stats['skipped_details']["oversize"] += 1
        safe_ext = (ext or "").lower()
        self.scan_stats['skipped_by_ext'][safe_ext] = self.scan_stats['skipped_by_ext'].get(safe_ext, 0) + 1

    @staticmethod
    def _compute_bundle_hash(content: str) -> str:
        """Compute stable short SHA256 bundle hash."""
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:12]

    def _generate_tree_map(self) -> str:
        """Generate directory tree map."""
        lines: List[str] = [f"{self.project_name}/"]
        paths = sorted(pathlib.Path(f['path']) for f in self.files_registry)
        seen_dirs: Set[str] = set()
        
        for p in paths:
            try:
                parts = p.parts
                indent = 0
                
                for i, part in enumerate(parts[:-1]):
                    d = pathlib.Path(*parts[:i + 1])
                    if str(d) not in seen_dirs:
                        lines.append(f"{'  ' * (indent + 1)}|-- {part}/")
                        seen_dirs.add(str(d))
                    indent += 1
                
                lines.append(f"{'  ' * (indent + 1)}|-- {parts[-1]}")
            except Exception as e:
                logger.warning(f"Error processing path {p}: {e}")
                continue
        
        return "\n".join(lines)

    def _extract_system_architecture_context(self) -> Dict[str, Any]:
        """Extract system-level execution and scientific context for agents."""
        relevant_files = [
            f for f in self.files_registry
            if any(token in f.get("path", "").lower() for token in ("worker", "solver", "validation", "gravity", "omega", "pipeline"))
        ]

        execution_graph: Dict[str, List[str]] = {}
        telemetry_map: Dict[str, str] = {}
        scientific_contracts: List[str] = []
        numerical_guarantees: List[str] = []
        failure_conditions: List[str] = []
        gpu_constraints: List[str] = []
        critical_modules: List[Dict[str, str]] = []
        research_targets: List[str] = []

        validation_stages = [
            "artifactloader",
            "spectralfidelityengine",
            "contractenforcer",
            "topologyengine",
            "empiricalbridgeengine",
            "tensorvalidationengine",
            "statisticalvalidationengine",
            "provenanceassembler",
        ]
        discovered_validation_stages: List[str] = []

        telemetry_hints = {
            "c_invariant": "collapse detection",
            "energy": "global energy stability",
            "phase_coherence": "phase ordering",
            "grad_phase_var": "turbulence",
            "gradient_variance": "gradient instability",
            "omega_saturation": "geometric compression",
        }

        critical_hints = {
            "unified_omega": "Conformal geometry mapping",
            "etdrk4solver": "PDE integration engine",
            "spectralfidelityengine": "Scientific claim validation",
            "contractenforcer": "Metric contract enforcement",
            "tensorvalidationengine": "Tensor consistency validation",
        }

        guarantee_patterns = [
            re.compile(r"\b\d+(?:\.\d+)?e[+-]?\d+\s*(?:<=|<|>=|>)\s*[A-Za-z0-9_\^²]+\s*(?:<=|<|>=|>)\s*\d+(?:\.\d+)?e[+-]?\d+"),
            re.compile(r"\b[A-Za-z0-9_\^²]+\s*(?:<=|<|>=|>)\s*\d+(?:\.\d+)?(?:e[+-]?\d+)?"),
            re.compile(r"orszag\s*2/3\s*dealiasing", re.IGNORECASE),
            re.compile(r"phase\s+centering\s+every\s+\d+\s+steps", re.IGNORECASE),
        ]

        contract_patterns = [
            re.compile(r"\b[A-Za-z0-9_]+\s*(?:<=|<|>=|>|==)\s*-?\d+(?:\.\d+)?(?:e[+-]?\d+)?"),
            re.compile(r"\bmust\s+(?:remain|be)\s+[A-Za-z_]+", re.IGNORECASE),
            re.compile(r"\bPASS\b|\bFAIL\b", re.IGNORECASE),
        ]

        failure_patterns = [
            re.compile(r"\bnan\b", re.IGNORECASE),
            re.compile(r"collapse_threshold", re.IGNORECASE),
            re.compile(r"\bsse\b\s*(?:>|>=)\s*\d+(?:\.\d+)?", re.IGNORECASE),
            re.compile(r"unstable simulation", re.IGNORECASE),
            re.compile(r"terminate|abort|stop", re.IGNORECASE),
        ]

        gpu_patterns = [
            re.compile(r"batched\s+fft", re.IGNORECASE),
            re.compile(r"fused\s+kernels?", re.IGNORECASE),
            re.compile(r"prealloc(?:ated)?\s+buffers?", re.IGNORECASE),
            re.compile(r"spectral(?:-space|\s+space)\s+integration", re.IGNORECASE),
            re.compile(r"cupy|cuda|gpu", re.IGNORECASE),
        ]

        for file_meta in relevant_files:
            path = file_meta.get("path", "unknown")
            content = file_meta.get("content", "")
            summary = file_meta.get("summary") or {}
            slices = summary.get("slices", [])

            node_lines: List[str] = []
            class_slices = [s for s in slices if s.get("type") == "class"]
            func_slices = [s for s in slices if s.get("type") in {"function", "async_function"}]

            for c in sorted(class_slices, key=lambda s: str(s.get("name", ""))):
                class_name = str(c.get("name", "<class>"))
                node_lines.append(class_name)
                calls = sorted(set(c.get("calls", [])))[:8]
                for call in calls:
                    node_lines.append(f"{class_name}.{call}()")

            for f_slice in sorted(func_slices, key=lambda s: str(s.get("name", "")))[:20]:
                node_lines.append(f"{f_slice.get('name', '<function>')}()")

            if node_lines:
                execution_graph[path] = node_lines

            lower_content = content.lower()
            for stage in validation_stages:
                if stage in lower_content:
                    discovered_validation_stages.append(stage)

            for metric, meaning in telemetry_hints.items():
                if metric in lower_content and metric not in telemetry_map:
                    telemetry_map[metric] = meaning

            for hint, description in critical_hints.items():
                if hint in lower_content or hint in path.lower():
                    critical_modules.append({"module": path, "component": hint, "reason": description})

            for line in content.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue

                if any(p.search(stripped) for p in contract_patterns):
                    scientific_contracts.append(stripped)
                if any(p.search(stripped) for p in guarantee_patterns):
                    numerical_guarantees.append(stripped)
                if any(p.search(stripped) for p in failure_patterns):
                    failure_conditions.append(stripped)
                if any(p.search(stripped) for p in gpu_patterns):
                    gpu_constraints.append(stripped)

                if "research target" in stripped.lower() or "detect" in stripped.lower() and "spectral" in stripped.lower():
                    research_targets.append(stripped)

        if not research_targets:
            research_targets.append("Detect prime-log spectral locking in nonlinear PDE system")

        unique_critical = []
        seen_critical: Set[str] = set()
        for item in sorted(critical_modules, key=lambda x: (x["module"], x["component"])):
            key = f"{item['module']}::{item['component']}"
            if key in seen_critical:
                continue
            seen_critical.add(key)
            unique_critical.append(item)

        def _dedupe_keep_order(items: List[str], limit: int = 80) -> List[str]:
            out: List[str] = []
            seen: Set[str] = set()
            for item in items:
                normalized = item.strip()
                if not normalized:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                out.append(normalized)
                if len(out) >= limit:
                    break
            return out

        pipeline_graph = []
        for stage in validation_stages:
            if stage in discovered_validation_stages:
                pipeline_graph.append(stage)

        return {
            "system_execution_graph": {k: v for k, v in sorted(execution_graph.items())},
            "critical_physics_modules": unique_critical,
            "scientific_contracts": _dedupe_keep_order(scientific_contracts),
            "numerical_guarantees": _dedupe_keep_order(numerical_guarantees),
            "simulation_telemetry": {k: telemetry_map[k] for k in sorted(telemetry_map.keys())},
            "validation_pipeline": pipeline_graph,
            "solver_modification_rules": [
                "Agents MUST NOT rewrite ETDRK4Solver class",
                "Agents MUST NOT modify FFT planning logic",
                "Agents MUST NOT modify geometry mapping in unified_omega",
            ],
            "failure_conditions": _dedupe_keep_order(failure_conditions),
            "gpu_optimization_constraints": _dedupe_keep_order(gpu_constraints),
            "research_target": _dedupe_keep_order(research_targets, limit=5)[0],
            "system_purpose": SYSTEM_PURPOSE,
        }

    def _build_patch_target_validation_registry(self) -> List[Dict[str, Any]]:
        """Build canonical slice registry for patch target validation."""
        maps = self._build_slice_maps()
        slice_by_id: Dict[str, Dict[str, Any]] = maps["slice_by_id"]
        file_fingerprints = {
            f.get("path", "unknown"): (f.get("fingerprint") or {}).get("sha1", "unknown")
            for f in self.files_registry
        }

        registry: List[Dict[str, Any]] = []
        for slice_id, meta in sorted(slice_by_id.items()):
            file_path = str(meta.get("file_path", "unknown"))
            registry.append({
                "slice_id": slice_id,
                "file": file_path,
                "lines": {
                    "start": int(meta.get("start_line", 0) or 0),
                    "end": int(meta.get("end_line", 0) or 0),
                },
                "fingerprint": file_fingerprints.get(file_path, "unknown"),
            })
        return registry

    def _build_state_mutation_map(self) -> Dict[str, Dict[str, List[str]]]:
        """Build mutation map from semantic slice metadata."""
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

            content = str(file_meta.get("content", ""))
            for line in content.splitlines():
                stripped = line.strip()
                if "rho" in stripped and ("abs(" in stripped or "**2" in stripped):
                    derived = state_map.setdefault(path, {}).setdefault("rho", [])
                    derivation = f"derived from: {stripped}"
                    if derivation not in derived:
                        derived.append(derivation)
        return {k: state_map[k] for k in sorted(state_map.keys())}

    def _build_numerical_operator_registry(self) -> Dict[str, Dict[str, str]]:
        """Build registry of operator names to inferred operator classes."""
        operators: Dict[str, Dict[str, str]] = {}
        for file_meta in self.files_registry:
            summary = file_meta.get("summary") or {}
            for slice_meta in summary.get("slices", []):
                name = str(slice_meta.get("name", "unknown"))
                operator_type = str(slice_meta.get("operator_type", "general operator"))
                key = f"{file_meta.get('path', 'unknown')}::{name}"
                operators[key] = {
                    "type": operator_type,
                    "slice_id": str(slice_meta.get("slice_id", "unknown")),
                }
        return {k: operators[k] for k in sorted(operators.keys())}

    def _build_gpu_kernel_registry(self) -> Dict[str, Dict[str, str]]:
        """Build registry of GPU-critical kernels/functions."""
        gpu_registry: Dict[str, Dict[str, str]] = {}
        for file_meta in self.files_registry:
            summary = file_meta.get("summary") or {}
            for slice_meta in summary.get("slices", []):
                profile = slice_meta.get("gpu_profile") or {}
                device = str(profile.get("device", "CPU/Unknown"))
                backend = str(profile.get("backend", "generic"))
                if device == "CPU/Unknown" and backend == "generic":
                    continue
                key = f"{file_meta.get('path', 'unknown')}::{slice_meta.get('name', 'unknown')}"
                gpu_registry[key] = {
                    "device": device,
                    "backend": backend,
                    "slice_id": str(slice_meta.get("slice_id", "unknown")),
                }
        return {k: gpu_registry[k] for k in sorted(gpu_registry.keys())}

    def _classify_code_role(self, path: str) -> str:
        lowered = path.lower()
        if "/notebooks/" in lowered or lowered.endswith(".ipynb"):
            return "exploratory research"
        if "validation" in lowered or "pipeline" in lowered:
            return "scientific validation"
        if any(token in lowered for token in ("worker", "solver", "gravity", "omega")):
            return "production solver"
        return "general infrastructure"

    def _build_code_classification(self) -> Dict[str, Dict[str, str]]:
        classification: Dict[str, Dict[str, str]] = {}
        for file_meta in self.files_registry:
            path = str(file_meta.get("path", "unknown"))
            classification[path] = {"role": self._classify_code_role(path)}
        return {k: classification[k] for k in sorted(classification.keys())}

    def _build_patch_safety_map(self, risk_meta: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Translate risk metadata into patch guidance."""
        recommendations = {
            "CRITICAL": "review only",
            "HIGH": "minimal patch only",
            "MEDIUM": "bounded surgical edit",
            "LOW": "safe to refactor",
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

    def _build_simulation_execution_model(self, architecture_context: Dict[str, Any]) -> List[str]:
        model = [
            "initialize solver",
            "generate initial field",
            "run ETDRK4 timesteps",
            "write HDF5 artifact",
            "run validation pipeline",
        ]
        if not architecture_context.get("validation_pipeline"):
            return model[:-1]
        return model

    @staticmethod
    def _agent_reasoning_checklist() -> List[str]:
        return [
            "Verify SLICE_ID exists",
            "Check slice risk level",
            "Verify no scientific contract violation",
            "Verify GPU constraints preserved",
            "Verify solver modification rules",
        ]

    def _get_allowed_focus_slices(self) -> Set[str]:
        """Resolve target dependencies based on --depth limit."""
        if not self.focus_target:
            return set()
            
        allowed_names = {self.focus_target.lower()}
        all_slices = []
        
        for f in self.files_registry:
            summary = f.get('summary') or {}
            all_slices.extend(summary.get("slices", []))
            
        current_focus = set(allowed_names)
        for _ in range(self.depth):
            new_calls = set()
            for s in all_slices:
                if s.get('name', '').lower() in current_focus:
                    for c in s.get('calls', []):
                        new_calls.add(c.lower())
            
            allowed_names.update(new_calls)
            current_focus = new_calls
            
        return allowed_names

    def _generate_text_output(self) -> str:
        """Generate human-readable text output with Phase 1 features."""
        lines: List[str] = []
        allowed_slices = self._get_allowed_focus_slices()
        slice_graph = self._build_slice_dependency_graph()
        reverse_graph = self._build_reverse_dependency_graph(slice_graph)
        risk_meta = self._compute_slice_risk_scores(slice_graph, reverse_graph)
        execution_paths = self._build_execution_paths(slice_graph)
        architecture_context = self._extract_system_architecture_context()
        patch_registry = self._build_patch_target_validation_registry()
        state_mutation_map = self._build_state_mutation_map()
        operator_registry = self._build_numerical_operator_registry()
        gpu_kernel_registry = self._build_gpu_kernel_registry()
        code_classification = self._build_code_classification()
        patch_safety = self._build_patch_safety_map(risk_meta)
        execution_model = self._build_simulation_execution_model(architecture_context)
        reasoning_checklist = self._agent_reasoning_checklist()
        
        # --- HEADER WITH BUNDLE HASH ---
        header = [
            f"# Workspace Bundle: {self.project_name}",
        ]
        
        if not self.deterministic:
            header.append(f"# Generated: {self.timestamp}")
        
        # Will be added after computing hash
        estimated_tokens_line = f"# Estimated Tokens: [COMPUTED]"
        header.append(estimated_tokens_line)
        header.append(f"# Bundle Schema Version: {BUNDLE_SCHEMA_VERSION}")
        header.append("# Compliance: Phase 1 - Verifiable context bundles")
        header.append("")
        header.extend(self._agent_header_lines())
        
        if self.focus_target:
            header.append(f"# FOCUS MODE: Target '{self.focus_target}' (Depth: {self.depth})")
            header.append("")

        # --- SYNTAX VALIDITY WARNINGS ---
        if self.syntax_errors:
            lines.append("[SYNTAX VALIDITY WARNINGS]")
            lines.append("==================================================================")
            lines.append("The following files contain syntax errors. Slices may be incomplete:")
            lines.append("")
            for error_msg in self.syntax_errors:
                lines.append(f"  • {error_msg}")
            lines.append("")
        
        # --- FILE FINGERPRINTS ---
        lines.extend([
            "==================================================================",
            "FILE FINGERPRINTS (for stale bundle detection)",
            "==================================================================",
            ""
        ])
        
        for f in self.files_registry:
            fingerprint = f.get('fingerprint') or {}
            lines.append(f"{f['path']}")
            lines.append(f"  SHA1: {fingerprint.get('sha1', 'unknown')}")
            lines.append(f"  Modified: {fingerprint.get('mtime_iso', 'unknown')}")
            lines.append(f"  Size: {fingerprint.get('size_bytes', 0):,} bytes")
            lines.append("")
        
        # --- TOPOLOGY ---
        lines.extend([
            "==================================================================",
            "LAYER 1: TOPOLOGY MAP",
            "==================================================================",
        ])
        lines.extend(self._generate_tree_map().splitlines())
        lines.append("")

        # --- ARCHITECTURE MAP ---
        lines.extend([
            "==================================================================",
            "LAYER 1.5: ARCHITECTURE MAP",
            "==================================================================",
            "System topology and external module calls (Limited to top 15 calls per file):",
            ""
        ])
        arch_map_found = False
        for f in self.files_registry:
            call_graph = f.get('summary', {}).get('call_graph', [])
            if call_graph:
                arch_map_found = True
                lines.append(f"{f['path']}")
                for call in sorted(call_graph)[:15]:
                    lines.append(f"  ├── calls -> {call}")
        if not arch_map_found:
            lines.append("(No AST call graphs detected in scanned files)")
        lines.append("")

        # --- IMPORT GRAPH ---
        lines.extend([
            "==================================================================",
            "LAYER 1.7: IMPORT GRAPH",
            "==================================================================",
            "Module-level dependencies resolved from Python imports:",
            ""
        ])
        import_map_found = False
        for f in self.files_registry:
            imports = f.get('summary', {}).get('import_graph', [])
            if imports:
                import_map_found = True
                lines.append(f"{f['path']}")
                for module_name in imports[:25]:
                    lines.append(f"  ├── imports -> {module_name}")
        if not import_map_found:
            lines.append("(No import graph data detected in scanned files)")
        lines.append("")

        # --- SYSTEM EXECUTION GRAPH ---
        lines.extend([
            "==================================================================",
            "SYSTEM EXECUTION GRAPH",
            "==================================================================",
            "High-level runtime structure inferred from solver/validation modules:",
            ""
        ])
        system_graph = architecture_context.get("system_execution_graph", {})
        if system_graph:
            for file_path, nodes in system_graph.items():
                lines.append(file_path)
                for idx, node in enumerate(nodes[:30]):
                    prefix = "  └──" if idx == len(nodes[:30]) - 1 else "  ├──"
                    lines.append(f"{prefix} {node}")
                lines.append("")
        else:
            lines.append("(No system execution graph candidates detected)")
            lines.append("")

        # --- SIMULATION EXECUTION MODEL ---
        lines.extend([
            "==================================================================",
            "SIMULATION EXECUTION MODEL",
            "==================================================================",
            "High-level runtime sequence:",
            ""
        ])
        if execution_model:
            lines.append(execution_model[0])
            for step in execution_model[1:]:
                lines.append("   ↓")
                lines.append(step)
        else:
            lines.append("(No simulation execution model inferred)")
        lines.append("")

        # --- CRITICAL PHYSICS MODULES ---
        lines.extend([
            "==================================================================",
            "CRITICAL PHYSICS MODULES",
            "==================================================================",
            "High-risk edit zones for numerical and scientific integrity:",
            ""
        ])
        critical_modules = architecture_context.get("critical_physics_modules", [])
        if critical_modules:
            for item in critical_modules[:40]:
                lines.append(f"{item.get('module', 'unknown')}")
                lines.append(
                    f"  {item.get('component', 'component')}: {item.get('reason', 'critical subsystem')}"
                )
                lines.append("")
        else:
            lines.append("(No critical module hints detected)")
            lines.append("")

        # --- SCIENTIFIC CONTRACTS ---
        lines.extend([
            "==================================================================",
            "SCIENTIFIC CONTRACTS",
            "==================================================================",
            "Contracts and invariants that must not be violated by patches:",
            ""
        ])
        scientific_contracts = architecture_context.get("scientific_contracts", [])
        if scientific_contracts:
            for contract in scientific_contracts[:40]:
                lines.append(f"  - {contract}")
        else:
            lines.append("  - (No explicit scientific contracts detected)")
        lines.append("")

        # --- NUMERICAL GUARANTEES ---
        lines.extend([
            "==================================================================",
            "NUMERICAL GUARANTEES",
            "==================================================================",
            "Stability guardrails and numerical bounds observed in code:",
            ""
        ])
        numerical_guarantees = architecture_context.get("numerical_guarantees", [])
        if numerical_guarantees:
            for guarantee in numerical_guarantees[:40]:
                lines.append(f"  - {guarantee}")
        else:
            lines.append("  - (No explicit numerical guarantees detected)")
        lines.append("")

        # --- SIMULATION TELEMETRY ---
        lines.extend([
            "==================================================================",
            "SIMULATION TELEMETRY",
            "==================================================================",
            "Telemetry metrics and operational meaning:",
            ""
        ])
        telemetry_map = architecture_context.get("simulation_telemetry", {})
        if telemetry_map:
            for metric, meaning in telemetry_map.items():
                lines.append(f"  - {metric} -> {meaning}")
        else:
            lines.append("  - (No telemetry map inferred)")
        lines.append("")

        # --- VALIDATION PIPELINE ---
        lines.extend([
            "==================================================================",
            "VALIDATION PIPELINE",
            "==================================================================",
            "Artifact flow through validation engines:",
            ""
        ])
        validation_pipeline = architecture_context.get("validation_pipeline", [])
        if validation_pipeline:
            lines.append("artifact")
            for stage in validation_pipeline:
                lines.append("   ↓")
                lines.append(stage)
        else:
            lines.append("(No validation pipeline stages detected)")
        lines.append("")

        # --- SOLVER MODIFICATION RULES ---
        lines.extend([
            "==================================================================",
            "SOLVER MODIFICATION RULES",
            "==================================================================",
            "Non-negotiable restrictions for solver patch proposals:",
            ""
        ])
        for rule in architecture_context.get("solver_modification_rules", []):
            lines.append(f"  - {rule}")
        lines.append("")

        # --- FAILURE CONDITIONS ---
        lines.extend([
            "==================================================================",
            "FAILURE CONDITIONS",
            "==================================================================",
            "Heuristics and triggers associated with unstable or invalid runs:",
            ""
        ])
        failure_conditions = architecture_context.get("failure_conditions", [])
        if failure_conditions:
            for condition in failure_conditions[:40]:
                lines.append(f"  - {condition}")
        else:
            lines.append("  - (No explicit failure conditions detected)")
        lines.append("")

        # --- GPU OPTIMIZATION CONSTRAINTS ---
        lines.extend([
            "==================================================================",
            "GPU OPTIMIZATION CONSTRAINTS",
            "==================================================================",
            "Performance-sensitive implementation constraints:",
            ""
        ])
        gpu_constraints = architecture_context.get("gpu_optimization_constraints", [])
        if gpu_constraints:
            for constraint in gpu_constraints[:30]:
                lines.append(f"  - {constraint}")
        else:
            lines.append("  - (No explicit GPU optimization constraints detected)")
        lines.append("")

        # --- RESEARCH TARGET ---
        lines.extend([
            "==================================================================",
            "RESEARCH TARGET",
            "==================================================================",
            f"{architecture_context.get('research_target', 'Detect prime-log spectral locking in nonlinear PDE system')}",
            ""
        ])

        # --- SYSTEM PURPOSE ---
        lines.extend([
            "==================================================================",
            "SYSTEM PURPOSE",
            "==================================================================",
            f"{architecture_context.get('system_purpose', SYSTEM_PURPOSE)}",
            ""
        ])

        # --- CODE CLASSIFICATION ---
        lines.extend([
            "==================================================================",
            "CODE CLASSIFICATION",
            "==================================================================",
            "Production vs validation vs exploratory roles:",
            ""
        ])
        for file_path, role_meta in code_classification.items():
            lines.append(file_path)
            lines.append(f"    role: {role_meta.get('role', 'general infrastructure')}")
            lines.append("")

        # --- ENTRY POINTS ---
        lines.extend([
            "==================================================================",
            "LAYER 1.8: ENTRY POINTS",
            "==================================================================",
            "Detected files containing if __name__ == \"__main__\":",
            ""
        ])
        entry_points = [
            f['path']
            for f in self.files_registry
            if (f.get('summary') or {}).get('is_entry_point', False)
        ]
        if entry_points:
            for entry in sorted(entry_points):
                lines.append(f"  ├── {entry}")
        else:
            lines.append("(No entry points detected)")
        lines.append("")

        # --- CHANGE RISK ANALYZER ---
        if self.focus_target:
            lines.extend([
                "==================================================================",
                "LAYER 1.6: CHANGE RISK ANALYSIS (IMPACT)",
                "==================================================================",
                f"Target: '{self.focus_target}'",
                "Called by / Affects:",
            ])
            impact_found = False
            for f in self.files_registry:
                summary = f.get('summary') or {}
                slices = summary.get('slices', [])
                for s in slices:
                    s_calls = [c.lower() for c in s.get('calls', [])]
                    if self.focus_target.lower() in s_calls:
                        lines.append(f"  ├── {f['path']} :: {s['name']}")
                        impact_found = True
            if not impact_found:
                lines.append("  └── (No internal callers found within the scanned files)")
            lines.append("")

        # --- CODE INTELLIGENCE ---
        lines.extend([
            "==================================================================",
            "LAYER 2: CODE INTELLIGENCE (SLICES & CALL GRAPHS)",
            "==================================================================",
            "Agents: Use these slices to understand logic and call chains without reading full files.",
            ""
        ])
        
        for f in self.files_registry:
            summary = f.get('summary') or {}
            slices = summary.get("slices", [])
            call_graph = summary.get("call_graph", [])
            
            if self.focus_target:
                slices = [s for s in slices if s.get('name', '').lower() in allowed_slices]
                if not slices:
                    continue 
            
            if slices or call_graph:
                lines.append(f"--- Intelligence: {f['path']} ---")
                if call_graph:
                    lines.append(f"Global File Dependencies: {', '.join(sorted(set(call_graph)))}")
                for s in slices:
                    target_calls = sorted(set(s.get('calls', [])))
                    signature = s.get("signature") or {}
                    lines.extend([
                        f"\n[SLICE: {s['name']} | Type: {s['type']} | Lines {s['start_line']}-{s['end_line']}]",
                        f"SLICE_ID: {s.get('slice_id', 'unknown')}",
                        f"Signature Args: {', '.join(signature.get('args', [])) or 'none'}",
                        f"Returns: {signature.get('returns', 'unknown')}",
                        f"Side Effects: {', '.join(signature.get('side_effects', ['none']))}",
                        f"Target Calls: {', '.join(target_calls)}",
                        SecurityKernel.sanitize_content(s['code'])
                    ])
                lines.append("\n" + ("-" * 40))
            elif summary and not slices and not self.focus_target:
                lines.extend([
                    f"--- Intelligence: {f['path']} ---",
                    f"Metadata: {summary}",
                    "\n" + ("-" * 40)
                ])

        # --- SLICE DEPENDENCY GRAPH ---
        lines.extend([
            "",
            "==================================================================",
            "LAYER 2.5: SLICE DEPENDENCY GRAPH",
            "==================================================================",
            "Cross-slice dependencies inferred from call sites:",
            ""
        ])
        edge_count = 0
        for source_slice, targets in sorted(slice_graph.items()):
            if not targets:
                continue
            edge_count += len(targets)
            lines.append(source_slice)
            for target in targets[:20]:
                lines.append(f"  ├── depends_on -> {target}")
        if edge_count == 0:
            lines.append("(No slice dependency edges resolved)")

        # --- STATE MUTATION MAP ---
        lines.extend([
            "",
            "==================================================================",
            "STATE MUTATION MAP",
            "==================================================================",
            "Mutation and derivation hotspots per file:",
            ""
        ])
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

        # --- NUMERICAL OPERATORS ---
        lines.extend([
            "",
            "==================================================================",
            "NUMERICAL OPERATORS",
            "==================================================================",
            "Operator roles inferred from slice semantics:",
            ""
        ])
        if operator_registry:
            for operator_name, operator_meta in operator_registry.items():
                lines.append(operator_name)
                lines.append(f"    type: {operator_meta.get('type', 'general operator')}")
        else:
            lines.append("(No numerical operators detected)")

        # --- GPU KERNEL REGISTRY ---
        lines.extend([
            "",
            "==================================================================",
            "GPU KERNEL REGISTRY",
            "==================================================================",
            "GPU-critical execution paths:",
            ""
        ])
        if gpu_kernel_registry:
            for kernel_name, kernel_meta in gpu_kernel_registry.items():
                lines.append(kernel_name)
                lines.append(f"    device: {kernel_meta.get('device', 'CPU/Unknown')}")
                lines.append(f"    backend: {kernel_meta.get('backend', 'generic')}")
        else:
            lines.append("(No GPU kernel registry entries detected)")

        # --- PATCH COLLISION / RISK METADATA ---
        lines.extend([
            "",
            "==================================================================",
            "LAYER 2.7: PATCH COLLISION & RISK METADATA",
            "==================================================================",
            "Use SLICE_ID in patch proposals to prevent multi-agent collisions:",
            "",
        ])

        if risk_meta:
            for slice_id, meta in sorted(risk_meta.items(), key=lambda kv: (-kv[1]["risk_score"], kv[0]))[:80]:
                lines.append(f"SLICE_ID: {slice_id}")
                lines.append(
                    f"  Risk: {meta['risk_level']} ({meta['risk_score']}/100) | "
                    f"callers={meta['callers_count']} callees={meta['callees_count']}"
                )
                if meta["callers"]:
                    lines.append(f"  Called by: {', '.join(meta['callers'][:8])}")
                if meta["callees"]:
                    lines.append(f"  Depends on: {', '.join(meta['callees'][:8])}")
                lines.append("")
        else:
            lines.append("(No slice risk metadata available)")

        # --- LAYER 2.8 PATCH TARGET VALIDATION ---
        lines.extend([
            "",
            "==================================================================",
            "LAYER 2.8: PATCH TARGET VALIDATION",
            "==================================================================",
            "SLICE_ID REGISTRY",
            ""
        ])
        if patch_registry:
            for entry in patch_registry[:200]:
                lines.append(entry["slice_id"])
                lines.append(f"    file: {entry['file']}")
                lines.append(f"    lines: {entry['lines']['start']}-{entry['lines']['end']}")
                lines.append(f"    fingerprint: {entry['fingerprint']}")
                lines.append("")
        else:
            lines.append("(No patch target registry entries)")

        # --- PATCH SAFETY ---
        lines.extend([
            "",
            "==================================================================",
            "PATCH SAFETY",
            "==================================================================",
            "Recommended change scope by slice risk:",
            ""
        ])
        if patch_safety:
            for slice_id, meta in sorted(patch_safety.items(), key=lambda x: (x[1].get("risk", "LOW"), x[0]))[:120]:
                lines.append(slice_id)
                lines.append(f"    risk: {meta.get('risk', 'LOW')}")
                lines.append(f"    recommended: {meta.get('recommended', 'manual review')}")
        else:
            lines.append("(No patch safety guidance available)")

        # --- EXECUTION FLOW ---
        lines.extend([
            "",
            "==================================================================",
            "LAYER 2.6: EXECUTION FLOW",
            "==================================================================",
            "Entry-point seeded flow reconstruction:",
            ""
        ])
        flow_found = False
        for source_file, path_nodes in sorted(execution_paths.items()):
            if not path_nodes:
                continue
            flow_found = True
            lines.append(source_file)
            for idx, node in enumerate(path_nodes[:30]):
                prefix = "  └──" if idx == len(path_nodes[:30]) - 1 else "  ├──"
                lines.append(f"{prefix} {node}")
        if not flow_found:
            lines.append("(No execution flow reconstructed; no entry points found)")

        # --- FULL FILE CACHE ---
        if not self.focus_target:
            lines.extend([
                "",
                "==================================================================",
                "LAYER 3: FULL FILE CACHE",
                "==================================================================",
            ])
            for f in self.files_registry:
                lines.extend([
                    f"--- FULL FILE: {f['path']} ---",
                    f"Size: {f['size_bytes']} bytes",
                    "Content: |"
                ])
                lines.extend(f"  {line}" for line in SecurityKernel.sanitize_content(f['content']).splitlines())
                lines.append("")

        # --- STRICT PATCH APPENDER ---
        if self.append_rules:
            lines.extend([
                "",
                "==================================================================",
                "STRICT OPERATIONAL GUARDRAILS",
                "==================================================================",
                "RULES:",
                "1. DO NOT regenerate or rewrite entire files. Only output surgical replacements.",
                "2. All code modifications MUST be output in the following strict PATCH format:",
                "",
                "PATCH_TYPE: FUNCTION_REPLACEMENT",
                "FILE: <filename>",
                "TARGET: <function or class name>",
                "LINES: <start_line>-<end_line>",
                "CODE: |",
                "  <your complete replacement code here>",
                "",
                "3. You must use the EXACT line numbers provided in the left margin of the code slices above.",
                "4. Every patch must include a valid SLICE_ID from LAYER 2.8.",
                "=================================================================="
            ])

        # --- AGENT REASONING CHECKLIST ---
        lines.extend([
            "",
            "==================================================================",
            "AGENT REASONING CHECKLIST",
            "==================================================================",
            "Before proposing a patch:",
            ""
        ])
        for idx, item in enumerate(reasoning_checklist, start=1):
            lines.append(f"{idx}. {item}")
        
        # Compute final text and hash
        final_text = "\n".join(lines)
        estimated_tokens = len(final_text) // 4
        
        # Compute SHA256 bundle hash
        self.bundle_hash = self._compute_bundle_hash(final_text)
        
        # Update header with actual values
        estimate_idx = header.index(estimated_tokens_line)
        header[estimate_idx] = f"# Estimated Tokens: ~{estimated_tokens:,} tokens"
        header.insert(estimate_idx + 1, f"# Bundle Hash: {self.bundle_hash}")
        
        if self.focus_target:
            header.append(f"# Layer 3 (full files) omitted in focus mode\n")
        
        return "\n".join(header) + "\n" + final_text

    def _generate_json_output(self) -> str:
        """Generate JSON output with Phase 1 features."""
        try:
            slice_graph = self._build_slice_dependency_graph()
            reverse_graph = self._build_reverse_dependency_graph(slice_graph)
            risk_meta = self._compute_slice_risk_scores(slice_graph, reverse_graph)
            execution_paths = self._build_execution_paths(slice_graph)
            allowed_slices = self._get_allowed_focus_slices()
            architecture_context = self._extract_system_architecture_context()
            patch_registry = self._build_patch_target_validation_registry()
            state_mutation_map = self._build_state_mutation_map()
            operator_registry = self._build_numerical_operator_registry()
            gpu_kernel_registry = self._build_gpu_kernel_registry()
            code_classification = self._build_code_classification()
            patch_safety = self._build_patch_safety_map(risk_meta)
            execution_model = self._build_simulation_execution_model(architecture_context)
            reasoning_checklist = self._agent_reasoning_checklist()

            layer_2_intelligence: List[Dict[str, Any]] = []
            for f in self.files_registry:
                summary = f.get('summary')
                if not summary:
                    continue

                raw_slices = summary.get("slices", [])
                if self.focus_target:
                    raw_slices = [
                        s for s in raw_slices
                        if str(s.get('name', '')).lower() in allowed_slices
                    ]

                sanitized_slices = [
                    {
                        **s,
                        "calls": sorted(set(s.get("calls", []))),
                        "code": SecurityKernel.sanitize_content(str(s.get("code", ""))),
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
                    "system_purpose": architecture_context.get("system_purpose", SYSTEM_PURPOSE),
                    "generated_at": self.timestamp if not self.deterministic else None,
                    "deterministic": self.deterministic,
                    "stats": self.scan_stats,
                    "agent_context": {
                        "role": self.agent_role,
                        "task": self.agent_task,
                        "target": self.agent_target or self.focus_target,
                    },
                },
                "verification": {
                    "bundle_hash": None,
                    "estimated_tokens": None,
                    "syntax_errors": self.syntax_errors,
                    "files_with_syntax_issues": len(self.syntax_errors),
                },
                "layer_1_topology": self._generate_tree_map(),
                "layer_1_7_import_graph": [
                    {
                        "path": f['path'],
                        "imports": f['summary'].get("import_graph", []) if f.get('summary') else [],
                    }
                    for f in self.files_registry
                    if (f.get('summary') or {}).get("import_graph")
                ],
                "layer_1_8_entry_points": [
                    f['path']
                    for f in self.files_registry
                    if (f.get('summary') or {}).get("is_entry_point", False)
                ],
                "layer_2_intelligence": layer_2_intelligence,
                "layer_2_5_slice_dependency_graph": slice_graph,
                "layer_2_6_execution_flow": execution_paths,
                "layer_2_7_patch_collision_risk": risk_meta,
                "layer_2_8_patch_target_validation": patch_registry,
                "layer_3_full_files": [] if self.focus_target else [
                    {
                        "path": f['path'],
                        "size_bytes": f['size_bytes'],
                        "complexity": f['complexity'],
                        "fingerprint": f.get('fingerprint', {}),
                        "content": SecurityKernel.sanitize_content(f['content']),
                    }
                    for f in self.files_registry
                ],
                "system_architecture_graph": architecture_context.get("system_execution_graph", {}),
                "critical_physics_modules": architecture_context.get("critical_physics_modules", []),
                "scientific_contracts": architecture_context.get("scientific_contracts", []),
                "numerical_guarantees": architecture_context.get("numerical_guarantees", []),
                "simulation_telemetry": architecture_context.get("simulation_telemetry", {}),
                "validation_pipeline": architecture_context.get("validation_pipeline", []),
                "solver_modification_rules": architecture_context.get("solver_modification_rules", []),
                "failure_conditions": architecture_context.get("failure_conditions", []),
                "gpu_optimization_constraints": architecture_context.get("gpu_optimization_constraints", []),
                "research_target": architecture_context.get("research_target", "Detect prime-log spectral locking in nonlinear PDE system"),
                "system_purpose": architecture_context.get("system_purpose", SYSTEM_PURPOSE),
                "state_mutation_map": state_mutation_map,
                "numerical_operators": operator_registry,
                "gpu_kernel_registry": gpu_kernel_registry,
                "code_classification": code_classification,
                "patch_safety": patch_safety,
                "simulation_execution_model": execution_model,
                "agent_reasoning_checklist": reasoning_checklist,
            }
            interim = json.dumps(payload, indent=2, sort_keys=True, default=str)
            estimated_tokens = len(interim) // 4
            self.bundle_hash = self._compute_bundle_hash(interim)
            verification = payload.get("verification")
            if isinstance(verification, dict):
                verification["bundle_hash"] = self.bundle_hash
                verification["estimated_tokens"] = estimated_tokens
            return json.dumps(payload, indent=2, sort_keys=True, default=str)
        except Exception as e:
            logger.error(f"Error generating JSON output: {e}")
            return json.dumps({"error": str(e)}, indent=2)

    def run(self, format_type: str = "text") -> str:
        """
        Scan files and generate bundle.
        
        Args:
            format_type: Output format (text, json)
            
        Returns:
            Bundle content as string
        """
        logger.info(f"Scanning {len(self.target_files)} targeted files with {self.workers} workers...")
        analyzer = PolyglotAnalyzer(event_callback=self.event_callback)

        future_to_path: Dict[Any, pathlib.Path] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            for path_obj in self.target_files:
                future = executor.submit(self._process_single_file, path_obj, analyzer)
                future_to_path[future] = path_obj

            for idx, future in enumerate(as_completed(future_to_path), start=1):
                if idx % 100 == 0:
                    logger.info(f"Processed {idx}/{len(self.target_files)} files...")

                path_obj = future_to_path[future]
                try:
                    file_result = future.result()
                except Exception as e:
                    logger.error(f"Worker failure for {path_obj}: {e}")
                    self.scan_stats["errors"] += 1
                    continue

                status = file_result.get("status")
                if status == "bundled":
                    self.files_registry.append(file_result["entry"])
                    self.scan_stats["bundled"] += 1
                    syntax_error = file_result.get("syntax_error")
                    if syntax_error:
                        self.syntax_errors.append(syntax_error)
                elif status == "skipped":
                    self._track_skip(file_result.get("skip_reason", "binary_or_ext"), file_result.get("ext", ""))
                else:
                    self.scan_stats["errors"] += 1

        self.files_registry.sort(key=lambda x: (x.get('complexity', 0), x.get('path', '')))
        
        logger.info(
            f"Scan complete - Bundled: {self.scan_stats['bundled']}, "
            f"Skipped: {self.scan_stats['skipped']}, Errors: {self.scan_stats['errors']}"
        )
        
        if self.syntax_errors:
            logger.warning(
                f"Found {len(self.syntax_errors)} files with syntax errors"
            )
        
        if format_type == "json":
            output_content = self._generate_json_output()
        else:
            output_content = self._generate_text_output()
        
        return output_content


# ============================================================================
# MAIN & CLI
# ============================================================================
def main() -> None:
    """Main entry point with Phase 1 + Phase 2 + Phase 3 enhancements."""
    
    parser = argparse.ArgumentParser(
        description="Aletheia Workspace Packager v5.2 (Research-Grade Context Engine)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
    Phase 1 Features:
  - Bundle Hash for context verification (SHA256)
  - File Fingerprints (SHA1 + mtime) for stale detection
  - Syntax Validity Tracking with detailed warnings
  - Deterministic output for reproducible bundles
  - Comprehensive structured logging

    Phase 2 Features:
      - Import graph extraction (ast.Import / ast.ImportFrom)
      - Entry-point detection (__name__ == "__main__")
      - Slice dependency graph
      - Execution flow reconstruction

    Phase 3 Features:
        - AGENT_CONTEXT tagging (--agent-role, --agent-task, --agent-target)
        - Patch-collision metadata via SLICE_ID mapping
        - Per-slice edit risk scoring for multi-agent safety

    Stage 4 Features:
        - Parallel file scanning (--workers)
        - AST analysis cache for unchanged Python files (--no-ast-cache)

Examples:
  python semantic_slicer_v5.2.py file1.py file2.py --format text
  python semantic_slicer_v5.2.py --manifest files.csv --deterministic
  python semantic_slicer_v5.2.py --git-diff --focus my_func --verbose
        """
    )
    
    parser.add_argument("paths", nargs="*", help="Specific files or directories to package")
    parser.add_argument("--manifest", help="Path to CSV or TXT file with file list")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("-o", "--output", help="Output filename")
    parser.add_argument("--base-dir", default=".", help="Base directory for relative paths")
    parser.add_argument("--focus", help="Only output slices for this exact function/class name")
    parser.add_argument("--depth", type=int, default=0, help="Dependency resolution depth")
    parser.add_argument("--append-rules", action="store_true", help="Add LLM modification rules")
    parser.add_argument("--git-diff", action="store_true", help="Scan Git-changed files")
    parser.add_argument("--deterministic", action="store_true", help="Omit timestamp for reproducibility")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS_DEFAULT, help=f"Parallel workers (1-{MAX_WORKERS_LIMIT})")
    parser.add_argument("--no-ast-cache", action="store_true", help="Disable AST cache for Python analysis")
    parser.add_argument(
        "--agent-role",
        choices=["review", "patch", "architecture", "optimize", "validate"],
        help="Agent role for context shaping"
    )
    parser.add_argument("--agent-task", help="Short task label for the agent")
    parser.add_argument("--agent-target", help="Primary code target for the agent")
    
    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)
    logger.info("=== Semantic Slicer v5.2 (Phase 1+2+3+4: Reliability + Architecture + Multi-Agent + Performance) ===")

    target_files: List[pathlib.Path] = []
    base_path = pathlib.Path(args.base_dir).resolve()

    # Validate base path
    if not base_path.exists():
        logger.error(f"Base directory does not exist: {base_path}")
        sys.exit(1)

    if not base_path.is_dir():
        logger.error(f"Base path is not a directory: {base_path}")
        sys.exit(1)

    logger.info(f"Using base directory: {base_path}")

    def add_target_file(candidate: pathlib.Path) -> None:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(base_path)
        except ValueError:
            logger.warning(f"Skipping outside base-dir: {resolved}")
            return
        target_files.append(resolved)

    # 1. Parse manual paths
    if args.paths:
        logger.info(f"Processing {len(args.paths)} command-line path arguments")
        for p_str in args.paths:
            try:
                p = pathlib.Path(p_str).resolve()
                if p.is_file():
                    add_target_file(p)
                    logger.debug(f"Added file: {p}")
                elif p.is_dir():
                    try:
                        p.relative_to(base_path)
                    except ValueError:
                        logger.warning(f"Skipping directory outside base-dir: {p}")
                        continue
                    logger.info(f"Scanning directory: {p}")
                    for root, dirs, files in os.walk(p):
                        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
                        files = sorted(files)
                        for file_name in files:
                            add_target_file(pathlib.Path(root) / file_name)
                else:
                    logger.warning(f"Path does not exist: {p_str}")
            except Exception as e:
                logger.warning(f"Error processing path {p_str}: {e}")

    # 2. Parse Manifest
    if args.manifest:
        manifest_path = pathlib.Path(args.manifest)
        
        if not manifest_path.exists():
            logger.error(f"Manifest file does not exist: {manifest_path}")
            sys.exit(1)
        
        logger.info(f"Reading manifest: {manifest_path}")
        
        try:
            if manifest_path.suffix.lower() == '.csv':
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    line_count = 0
                    for row in reader:
                        line_count += 1
                        if 'abs_path' in row and row['abs_path']:
                            add_target_file(pathlib.Path(row['abs_path']))
                    logger.info(f"Loaded {line_count} entries from CSV manifest")
            else:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    line_count = 0
                    for line in f:
                        line_count += 1
                        clean_line = line.strip()
                        if clean_line and not clean_line.startswith('#'):
                            add_target_file(pathlib.Path(clean_line))
                    logger.info(f"Loaded {line_count} entries from text manifest")
        except OSError as e:
            logger.error(f"Cannot read manifest: {e}")
            sys.exit(1)

    # 3. Git-Aware Scanning
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
            logger.info(f"Detected {len(changed_files)} Git changes")
            
            for changed_file in sorted(changed_files):
                try:
                    full_path = (base_path / changed_file).resolve()
                    if full_path.is_file():
                        add_target_file(full_path)
                except Exception as e:
                    logger.warning(f"Error processing changed file {changed_file}: {e}")
        except subprocess.TimeoutExpired:
            logger.warning("Git command timed out")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git command failed: {e}")
        except Exception as e:
            logger.warning(f"Failed to retrieve Git diff: {e}")

    # Deduplicate files
    target_files = sorted(set(target_files), key=lambda p: p.as_posix().lower())

    if not target_files:
        logger.error("No valid files found to package")
        sys.exit(1)

    logger.info(f"Final target file count: {len(target_files)}")

    if args.workers < 1 or args.workers > MAX_WORKERS_LIMIT:
        logger.error(f"--workers must be between 1 and {MAX_WORKERS_LIMIT}")
        sys.exit(1)

    # Create packager
    project_name = base_path.name or "workspace"
    
    try:
        packager = WorkspacePackager(
            target_files,
            base_path,
            project_name=project_name,
            focus_target=args.focus,
            depth=args.depth,
            append_rules=args.append_rules,
            deterministic=args.deterministic,
            agent_role=args.agent_role,
            agent_task=args.agent_task,
            agent_target=args.agent_target,
            workers=args.workers,
            ast_cache_enabled=(not args.no_ast_cache),
        )
    except Exception as e:
        logger.error(f"Cannot create packager: {e}")
        sys.exit(1)
    
    # Generate bundle
    logger.info("Generating bundle...")
    try:
        bundle_content = packager.run(args.format)
    except Exception as e:
        logger.error(f"Error generating bundle: {e}")
        sys.exit(1)

    # Determine output path
    if args.output:
        out_path = pathlib.Path(args.output)
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = {"text": "txt", "json": "json"}.get(args.format, "txt")
        out_dir = base_path / f"{project_name}_bundle_{ts}"
        out_path = out_dir / f"{project_name}_bundle_{ts}.{ext}"

    # Write output
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path_resolved = out_path.resolve()
        
        with open(out_path_resolved, "w", encoding="utf-8") as handle:
            handle.write(bundle_content)
        
        logger.info(f"Bundle saved to: {out_path_resolved}")
        
        if packager.bundle_hash:
            print(f"[OK] Bundle saved: {out_path_resolved}")
            print(f"  Bundle Hash: {packager.bundle_hash}")
            print(f"  Files: {packager.scan_stats['bundled']} bundled, {packager.scan_stats['skipped']} skipped")
            if packager.syntax_errors:
                print(f"  [WARNING] Syntax Issues: {len(packager.syntax_errors)} files")
        
    except OSError as e:
        logger.error(f"Error writing output: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error writing output: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
        sys.exit(1)
