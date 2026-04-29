"""
Jupyter Notebook Packager (Self-Extracting Colab Bundle) v3
Purpose: Package a directory into a single .ipynb file using %%writefile, 
         with automatic dependency installation and smart size limits.
"""

import argparse
import datetime
import json
import math
import mimetypes
import os
import pathlib
import re
import sys
from typing import Any, Dict, List

MAX_FILE_SIZE_BYTES = 1_500_000 # 1.5 MB limit for JSON/text files
IGNORE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff", ".zip",
    ".gz", ".tar", ".tgz", ".bz2", ".xz", ".exe", ".dll", ".so",
    ".dylib", ".pdf", ".bin", ".class", ".pyc"
}
IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", "dist", "build", ".mypy_cache"
}

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
            content = pattern.sub("[REDACTED_SENSITIVE_PATTERN]", content)

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
# NOTEBOOK BUILDER
# ============================================================================
class NotebookPackager:
    def __init__(self, target_dir: pathlib.Path):
        self.target_dir = target_dir.resolve()
        self.files_registry: List[Dict[str, Any]] = []
        self.directories_registry: set = set()
        
    def _create_markdown_cell(self, text: str) -> Dict[str, Any]:
        return {
            "cell_type": "markdown",
            "metadata": {},
            "source": [line + "\n" for line in text.splitlines()]
        }

    def _create_code_cell(self, text: str) -> Dict[str, Any]:
        return {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [line + "\n" for line in text.splitlines()]
        }

    def traverse_and_read(self):
        for root, dirs, files in os.walk(self.target_dir):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
            
            for file_name in files:
                if file_name.startswith('.'):
                    continue
                    
                path_obj = pathlib.Path(root) / file_name
                rel_path = path_obj.relative_to(self.target_dir).as_posix()
                
                parent_dir = path_obj.parent.relative_to(self.target_dir).as_posix()
                if parent_dir != ".":
                    self.directories_registry.add(parent_dir)

                if SecurityKernel.is_binary(str(path_obj)):
                    continue
                    
                size = path_obj.stat().st_size
                is_python = path_obj.suffix.lower() == '.py'
                
                # Smart Size Limiter: Unlimited for .py files, limited for .json and others
                if not is_python and size > MAX_FILE_SIZE_BYTES:
                    print(f"Skipping {rel_path} due to size limit ({size / 1_000_000:.2f} MB)")
                    continue
                    
                try:
                    with open(path_obj, "r", encoding="utf-8", errors="ignore") as handle:
                        raw = handle.read()
                        
                    self.files_registry.append({
                        "path": rel_path,
                        "size_bytes": size,
                        "content": SecurityKernel.sanitize_content(raw)
                    })
                except Exception as e:
                    print(f"Skipping {rel_path} due to read error: {e}")

    def generate_notebook(self) -> str:
        cells = []
        
        # 1. Title & Instruction Markdown
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        intro_text = (
            f"# Self-Extracting Workspace Bundle: `{self.target_dir.name}`\n"
            f"**Generated:** {timestamp}\n\n"
            "**Instructions:**\n"
            "Run all cells in this notebook to recreate the directory structure "
            "and files in your current Colab/Jupyter environment. "
        )
        cells.append(self._create_markdown_cell(intro_text))
        
        # 2. Directory Setup Code Cell
        if self.directories_registry:
            dirs_list = ",\n    ".join([f'"{d}"' for d in sorted(self.directories_registry)])
            setup_code = (
                "import os\n\n"
                "directories_to_create = [\n"
                f"    {dirs_list}\n"
                "]\n\n"
                "for d in directories_to_create:\n"
                "    os.makedirs(d, exist_ok=True)\n"
                "    print(f'Created directory: {d}')\n"
                "print('Directory setup complete!')"
            )
            cells.append(self._create_markdown_cell("### Step 1: Create Directory Tree"))
            cells.append(self._create_code_cell(setup_code))

        # 3. File Write Cells
        cells.append(self._create_markdown_cell("### Step 2: Extract Files"))
        
        has_requirements = False
        for file_meta in sorted(self.files_registry, key=lambda x: x['path']):
            # Check if this file is requirements.txt (in the root folder)
            if file_meta['path'].lower() == 'requirements.txt':
                has_requirements = True
                
            write_code = f"%%writefile {file_meta['path']}\n{file_meta['content']}"
            cells.append(self._create_markdown_cell(f"**Extracting:** `{file_meta['path']}` ({file_meta['size_bytes']} bytes)"))
            cells.append(self._create_code_cell(write_code))

        # 4. Install Dependencies (If requirements.txt exists)
        if has_requirements:
            cells.append(self._create_markdown_cell("### Step 3: Install Dependencies\nAutomatically installing packages from `requirements.txt`."))
            cells.append(self._create_code_cell("!pip install -r requirements.txt"))

        # Build final Notebook dictionary
        notebook_dict = {
            "cells": cells,
            "metadata": {
                "language_info": {
                    "name": "python"
                }
            },
            "nbformat": 4,
            "nbformat_minor": 5
        }
        
        return json.dumps(notebook_dict, indent=2)

def main():
    parser = argparse.ArgumentParser(description="Jupyter Notebook Packager (Self-Extracting Bundle)")
    parser.add_argument("path", nargs="?", default=".", help="Project directory to package")
    parser.add_argument("-o", "--output", help="Output .ipynb filename")
    args = parser.parse_args()

    target_dir = pathlib.Path(args.path).resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        sys.exit(f"Error: Target must be a valid directory: {target_dir}")

    print(f"Scanning directory: {target_dir}")
    packager = NotebookPackager(target_dir)
    packager.traverse_and_read()
    
    nb_content = packager.generate_notebook()

    if args.output:
        out_path = pathlib.Path(args.output)
        if out_path.suffix != '.ipynb':
            out_path = out_path.with_suffix('.ipynb')
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = target_dir.parent / f"{target_dir.name}_bundle_{ts}.ipynb"

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(nb_content)
        print(f"\n[Success] Bundled {len(packager.files_registry)} files.")
        print(f"Notebook saved to: {out_path}")
    except Exception as e:
        sys.exit(f"Error writing output: {e}")

if __name__ == "__main__":
    main()