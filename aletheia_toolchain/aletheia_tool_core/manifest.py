import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MANIFEST_COLUMNS = [
    "root",
    "rel_path",
    "abs_path",
    "ext",
    "size",
    "mtime_iso",
    "sha1",
]


def validate_manifest_headers(headers: Optional[Iterable[str]]) -> None:
    if headers is None:
        raise ValueError("Manifest CSV is missing headers")
    missing = [col for col in MANIFEST_COLUMNS if col not in headers]
    if missing:
        raise ValueError(f"Manifest CSV missing required columns: {', '.join(missing)}")


def load_manifest_csv(path: Path) -> List[Dict[str, str]]:
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_manifest_headers(reader.fieldnames)
        rows: List[Dict[str, str]] = [row for row in reader]
    return rows


DEFAULT_SUSPICIOUS_DIRECTORIES = [
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    "failed_workspaces",
]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lower()


def is_suspicious_manifest_path(path: str, suspicious_directories: List[str] = None) -> bool:
    if suspicious_directories is None:
        suspicious_directories = DEFAULT_SUSPICIOUS_DIRECTORIES
    normalized = _normalize_path(path)
    return any(f"/{entry.lower()}/" in normalized or normalized.endswith(f"/{entry.lower()}") for entry in suspicious_directories)


def analyze_manifest_rows(rows: List[Dict[str, str]], suspicious_directories: List[str] = None) -> Dict[str, Any]:
    if suspicious_directories is None:
        suspicious_directories = DEFAULT_SUSPICIOUS_DIRECTORIES

    abs_seen: set[str] = set()
    rel_seen: set[str] = set()
    duplicate_abs_paths: List[str] = []
    duplicate_rel_paths: List[str] = []
    missing_paths: List[str] = []
    suspicious_paths: List[str] = []
    root_paths: set[str] = set()

    for row in rows:
        abs_path = row.get("abs_path", "").strip()
        rel_path = row.get("rel_path", "").strip()
        root = row.get("root", "").strip()

        root_paths.add(root)

        if abs_path in abs_seen:
            duplicate_abs_paths.append(abs_path)
        else:
            abs_seen.add(abs_path)

        if rel_path in rel_seen:
            duplicate_rel_paths.append(rel_path)
        else:
            rel_seen.add(rel_path)

        if abs_path:
            if not Path(abs_path).exists():
                missing_paths.append(abs_path)
            if is_suspicious_manifest_path(abs_path, suspicious_directories):
                suspicious_paths.append(abs_path)

    status = "PASS"
    if missing_paths or duplicate_abs_paths or duplicate_rel_paths:
        status = "FAIL"
    elif suspicious_paths:
        status = "WARN"

    return {
        "status": status,
        "summary": {
            "row_count": len(rows),
            "root_count": len(root_paths),
            "missing_files": len(missing_paths),
            "duplicate_abs_paths": len(duplicate_abs_paths),
            "duplicate_rel_paths": len(duplicate_rel_paths),
            "suspicious_paths": len(suspicious_paths),
        },
        "details": {
            "missing_files": missing_paths,
            "duplicate_abs_paths": duplicate_abs_paths,
            "duplicate_rel_paths": duplicate_rel_paths,
            "suspicious_paths": suspicious_paths,
        },
    }


def manifest_row_count(path: Path) -> int:
    rows = load_manifest_csv(path)
    return len(rows)
