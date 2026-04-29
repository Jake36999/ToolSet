import argparse
import csv
import hashlib
import os
from pathlib import Path
from datetime import datetime
from typing import Iterable, List, Optional, Dict

# --- CONFIGURATION ---
DEFAULT_EXTS = [
    ".py",".ps1",".ts",".tsx",".js",".jsx",".mjs",".json",".yaml",".yml",
    ".md",".txt",".html",".css",".ini",".toml",".cfg",".sh",".bat",".sql"
]

DEFAULT_EXCLUDES = [
    ".git","node_modules",".venv","venv","__pycache__",".pytest_cache",".idea",".vscode","dist","build",".next"
]

def sha1_file(p: Path, chunk: int = 1024*1024) -> str:
    h = hashlib.sha1()
    try:
        with p.open("rb") as f:
            while True:
                b = f.read(chunk)
                if not b: break
                h.update(b)
        return h.hexdigest()
    except PermissionError:
        return ""  # Skip files we can't read

def should_skip_dir(name: str, excludes: List[str]) -> bool:
    name_lower = name.lower()
    return any(name_lower == e.lower() for e in excludes)

def scan_root(root: Path, include_exts: List[str], excludes: List[str], do_hash: bool) -> Iterable[Dict]:
    root = root.resolve()
    print(f"Scanning root: {root}")
    
    for dirpath, dirnames, filenames in os.walk(root):
        # prune excluded dirs in-place
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d, excludes)]
        
        for fn in filenames:
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            
            # Extension filtering
            if include_exts and ext not in include_exts:
                continue
            
            try:
                stat = p.stat()
            except Exception:
                continue
                
            rec = {
                "root": str(root),
                "rel_path": str(p.relative_to(root)),
                "abs_path": str(p),
                "ext": ext,
                "size": stat.st_size,
                "mtime_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
            
            # Hashing logic
            if do_hash and stat.st_size <= 50_000_000:  # avoid hashing giant files > 50MB
                try:
                    rec["sha1"] = sha1_file(p)
                except Exception:
                    rec["sha1"] = ""
            else:
                rec["sha1"] = ""
                
            yield rec

def main():
    ap = argparse.ArgumentParser(description="Create a unified file map CSV.")
    # UPDATED: Arguments are no longer required.
    ap.add_argument("--roots", nargs="+", help="Directory roots to scan. Defaults to current directory.")
    ap.add_argument("--out", help="CSV path to write. Defaults to 'file_map.csv'.")
    ap.add_argument("--include-exts", default=",".join(DEFAULT_EXTS), help="Comma-separated extensions.")
    ap.add_argument("--exclude-dirs", default=",".join(DEFAULT_EXCLUDES), help="Comma-separated directories to exclude.")
    ap.add_argument("--hash", action="store_true", help="Compute SHA1 for files <=50MB.")
    
    args = ap.parse_args()

    # --- APPLY DEFAULTS ---
    # If no root provided, use current directory "."
    roots_input = args.roots if args.roots else ["."]
    # If no output provided, use "file_map.csv"
    out_path = args.out if args.out else "file_map.csv"

    include_exts = [e.strip() for e in args.include_exts.split(",") if e.strip()]
    include_exts = [e if e.startswith(".") else "."+e for e in include_exts]
    excludes = [e.strip() for e in args.exclude_dirs.split(",") if e.strip()]

    rows = []
    print(f"Starting scan...")
    print(f"Target Output: {Path(out_path).resolve()}")

    for r in roots_input:
        root = Path(r).expanduser()
        if not root.exists():
            print(f"[WARN] Root not found: {root}")
            continue
        for rec in scan_root(root, include_exts, excludes, args.hash):
            rows.append(rec)

    # Write to CSV
    try:
        out_p = Path(out_path)
        if out_p.parent.name: # Only try making directories if there is a parent path
            out_p.parent.mkdir(parents=True, exist_ok=True)
            
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["root","rel_path","abs_path","ext","size","mtime_iso","sha1"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)

        print(f"Success! Wrote {len(rows)} rows to {out_path}")
        
    except PermissionError:
        print(f"[ERROR] Could not write to {out_path}. Is the file open in Excel?")

if __name__ == "__main__":
    main()