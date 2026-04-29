import argparse
import csv
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from aletheia_tool_core.manifest import DEFAULT_SUSPICIOUS_DIRECTORIES
from aletheia_tool_core.reports import write_json_report

# --- CONFIGURATION ---
DEFAULT_EXTS = [
    ".py", ".ps1", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".yaml", ".yml",
    ".md", ".txt", ".html", ".css", ".ini", ".toml", ".cfg", ".sh", ".bat", ".sql"
]

DEFAULT_EXCLUDES = [
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode", "dist", "build", ".next"
]

CSV_FIELDNAMES = ["root", "rel_path", "abs_path", "ext", "size", "mtime_iso", "sha1"]
DEFAULT_HASH_SIZE_LIMIT = 50_000_000
DEFAULT_MAX_FILE_SIZE = 0

BUILTIN_PROFILES = {
    "default": {
        "include_exts": DEFAULT_EXTS,
        "exclude_dirs": DEFAULT_EXCLUDES,
        "max_file_size": DEFAULT_MAX_FILE_SIZE,
        "suspicious_dirs": DEFAULT_SUSPICIOUS_DIRECTORIES,
    },
    "safe": {
        "include_exts": DEFAULT_EXTS,
        "exclude_dirs": DEFAULT_EXCLUDES + ["output", "runs", "tmp", "temp"],
        "max_file_size": DEFAULT_MAX_FILE_SIZE,
        "suspicious_dirs": DEFAULT_SUSPICIOUS_DIRECTORIES,
    },
    "python": {
        "include_exts": [".py", ".toml", ".md", ".json", ".yaml", ".yml"],
        "exclude_dirs": DEFAULT_EXCLUDES,
        "max_file_size": DEFAULT_MAX_FILE_SIZE,
        "suspicious_dirs": DEFAULT_SUSPICIOUS_DIRECTORIES,
    },
}


def report_warning(message: str) -> None:
    print(f"[WARN] {message}", file=sys.stderr)


def normalize_extensions(values: List[str]) -> List[str]:
    normalized = []
    for item in values:
        trimmed = item.strip()
        if not trimmed:
            continue
        if not trimmed.startswith("."):
            trimmed = "." + trimmed
        normalized.append(trimmed.lower())
    return normalized


def sha1_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha1()
    try:
        with path.open("rb") as handle:
            while True:
                chunk_bytes = handle.read(chunk)
                if not chunk_bytes:
                    break
                h.update(chunk_bytes)
        return h.hexdigest()
    except PermissionError:
        return ""


def should_skip_dir(name: str, excludes: List[str]) -> bool:
    return any(name.lower() == candidate.lower() for candidate in excludes)


def is_suspicious_path(abs_path: str, suspicious_dirs: List[str]) -> bool:
    normalized = abs_path.replace("\\", "/").lower()
    return any(
        normalized.endswith(f"/{entry.lower()}") or f"/{entry.lower()}/" in normalized
        for entry in suspicious_dirs
    )


def parse_list(value: Optional[str], default: List[str]) -> List[str]:
    if value is None:
        return default
    return normalize_extensions([item.strip() for item in value.split(",") if item.strip()])


def parse_dirs(value: Optional[str], default: List[str]) -> List[str]:
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def get_profile(name: str) -> Dict[str, Any]:
    if name not in BUILTIN_PROFILES:
        raise ValueError(f"Unknown profile: {name}")
    return BUILTIN_PROFILES[name]


def scan_root(
    root: Path,
    include_exts: List[str],
    excludes: List[str],
    do_hash: bool,
    max_file_size: int,
    suspicious_dirs: List[str],
    stats: Dict[str, Any],
) -> Iterable[Dict[str, Any]]:
    root = root.resolve()
    print(f"Scanning root: {root}")

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d, excludes)]

        for fn in filenames:
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            if include_exts and ext not in include_exts:
                stats["skipped_by_ext"][ext] = stats["skipped_by_ext"].get(ext, 0) + 1
                stats["skipped"] += 1
                continue

            try:
                stat = p.stat()
            except Exception:
                stats["errors"] += 1
                continue

            if max_file_size and stat.st_size > max_file_size:
                stats["skipped"] += 1
                stats["skipped_by_ext"][ext] = stats["skipped_by_ext"].get(ext, 0) + 1
                stats["skipped_details"]["oversize"] += 1
                continue

            record = {
                "root": str(root),
                "rel_path": str(p.relative_to(root)),
                "abs_path": str(p),
                "ext": ext,
                "size": stat.st_size,
                "mtime_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }

            if do_hash and stat.st_size <= DEFAULT_HASH_SIZE_LIMIT:
                record["sha1"] = sha1_file(p)
            else:
                record["sha1"] = ""

            stats["bundled"] += 1
            if is_suspicious_path(str(p), suspicious_dirs):
                stats["polluted_files"].append(str(p))
            yield record


def build_health_report(
    roots: List[str],
    stats: Dict[str, Any],
    profile_name: str,
    profile_settings: Dict[str, Any],
    out_path: str,
    fail_on_pollution: bool,
) -> Dict[str, Any]:
    report = {
        "profile": profile_name,
        "output": out_path,
        "roots": roots,
        "row_count": stats["bundled"],
        "skipped": stats["skipped"],
        "errors": stats["errors"],
        "skipped_details": stats["skipped_details"],
        "skipped_by_ext": stats["skipped_by_ext"],
        "polluted_files": stats["polluted_files"],
        "pollution_count": len(stats["polluted_files"]),
        "profile_settings": profile_settings,
        "max_file_size": profile_settings.get("max_file_size", 0),
        "fail_on_pollution": fail_on_pollution,
    }
    if report["pollution_count"] > 0:
        report["status"] = "WARN" if not fail_on_pollution else "FAIL"
    else:
        report["status"] = "PASS"
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a unified file map CSV.")
    parser.add_argument("--roots", nargs="+", help="Directory roots to scan. Defaults to current directory.")
    parser.add_argument("-o", "--out", help="CSV path to write. Defaults to 'file_map.csv'.")
    parser.add_argument("--include-exts", default=None, help="Comma-separated extensions.")
    parser.add_argument("--exclude-dirs", default=None, help="Comma-separated directories to exclude.")
    parser.add_argument("--hash", action="store_true", help="Compute SHA1 for files <=50MB.")
    parser.add_argument("--profile", default="default", help="Built-in scan profile to use.")
    parser.add_argument("--health-report", help="Optional JSON health report path.")
    parser.add_argument("--max-file-size", type=int, default=DEFAULT_MAX_FILE_SIZE, help="Maximum file size in bytes to include. 0 means no limit.")
    parser.add_argument("--fail-on-pollution", action="store_true", help="Exit non-zero if suspicious paths are detected.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if "-o" in sys.argv and "--out" not in sys.argv:
        report_warning("Using -o as an alias for --out. Prefer --out for compatibility.")

    roots_input = args.roots if args.roots else ["."]
    out_path = args.out if args.out else "file_map.csv"

    try:
        profile_settings = get_profile(args.profile)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    include_exts = parse_list(args.include_exts, profile_settings["include_exts"])
    exclude_dirs = parse_dirs(args.exclude_dirs, profile_settings["exclude_dirs"])
    max_file_size = args.max_file_size or profile_settings.get("max_file_size", DEFAULT_MAX_FILE_SIZE)
    suspicious_dirs = profile_settings.get("suspicious_dirs", DEFAULT_SUSPICIOUS_DIRECTORIES)

    rows: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "bundled": 0,
        "skipped": 0,
        "errors": 0,
        "skipped_details": {"oversize": 0},
        "skipped_by_ext": {},
        "polluted_files": [],
    }

    print("Starting scan...")
    print(f"Target Output: {Path(out_path).resolve()}")

    for root_value in roots_input:
        root = Path(root_value).expanduser()
        if not root.exists():
            report_warning(f"Root not found: {root}")
            continue
        for rec in scan_root(root, include_exts, exclude_dirs, args.hash, max_file_size, suspicious_dirs, stats):
            rows.append(rec)

    out_p = Path(out_path)
    if out_p.parent.name:
        out_p.parent.mkdir(parents=True, exist_ok=True)

    try:
        with out_p.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    except PermissionError:
        print(f"[ERROR] Could not write to {out_path}. Is the file open in Excel?", file=sys.stderr)
        return 1

    if args.health_report:
        health_report = build_health_report(
            roots_input,
            stats,
            args.profile,
            {
                "include_exts": include_exts,
                "exclude_dirs": exclude_dirs,
                "max_file_size": max_file_size,
                "suspicious_dirs": suspicious_dirs,
            },
            out_path,
            args.fail_on_pollution,
        )
        write_json_report(health_report, Path(args.health_report))
        print(f"Health report written to: {args.health_report}")

    print(f"Success! Wrote {len(rows)} rows to {out_path}")
    if stats["polluted_files"]:
        report_warning(f"Detected {len(stats['polluted_files'])} suspicious path(s) in the manifest.")

    if args.health_report and stats["polluted_files"] and args.fail_on_pollution:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
