import datetime
import hashlib
import math
import mimetypes
import os
import pathlib
import re
from typing import Dict, Iterable, List, Pattern

DEFAULT_IGNORE_EXTENSIONS = [
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff",
    ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".exe",
    ".dll", ".so", ".dylib", ".pdf", ".bin", ".class", ".pyc",
]

SENSITIVE_PATTERNS: List[Pattern[str]] = [
    re.compile(r"-----BEGIN[A-Z0-9 ]+KEY-----.*?-----END[A-Z0-9 ]+KEY-----", re.DOTALL),
    re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL),
    re.compile(r"(api_key|secret_key|auth_token)\s*[:=]\s*['\"][A-Za-z0-9+/=]{20,}['\"]", re.IGNORECASE),
    re.compile(r"(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
]


def is_ignored_dir(dirname: str, ignore_dirs: List[str]) -> bool:
    dirname_lower = dirname.lower()
    return any(dirname_lower == candidate.lower() for candidate in ignore_dirs)


def is_binary_file(filepath: str, ignore_exts: List[str] = DEFAULT_IGNORE_EXTENSIONS, scan_bytes: int = 2048) -> bool:
    if any(filepath.lower().endswith(ext) for ext in ignore_exts):
        return True

    try:
        with open(filepath, "rb") as handle:
            chunk = handle.read(scan_bytes)
        if not chunk:
            return False
        if b"\x00" in chunk:
            return True

        guess, _ = mimetypes.guess_type(filepath)
        if guess and not guess.startswith(("text", "application")):
            return True

        text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x7F)))
        nontext = sum(byte not in text_chars for byte in chunk)
        return (nontext / len(chunk)) > 0.40
    except Exception:
        return True


def calculate_entropy(text: str) -> float:
    if not text:
        return 0.0
    entropy = 0.0
    for idx in range(256):
        char = chr(idx)
        count = text.count(char)
        if count == 0:
            continue
        p_x = count / len(text)
        entropy -= p_x * math.log(p_x, 2)
    return entropy


def sanitize_content(content: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        content = pattern.sub("[REDACTED_SENSITIVE_PATTERN]", content)

    sanitized_lines: List[str] = []
    for line in content.splitlines():
        lower_line = line.lower()
        if any(keyword in lower_line for keyword in ["api", "key", "secret", "token", "auth", "password"]):
            if calculate_entropy(line) > 4.5:
                parts = line.split("=", 1)
                prefix = parts[0] if len(parts) == 2 else line.split(":", 1)[0]
                sanitized_lines.append(f"{prefix}= [REDACTED_HIGH_ENTROPY]")
                continue
        sanitized_lines.append(line)
    return "\n".join(sanitized_lines)


def compute_file_fingerprint(filepath: pathlib.Path, hash_size: int = 8192) -> Dict[str, str]:
    sha1_hash = hashlib.sha1()
    size_bytes = 0

    with open(filepath, "rb") as handle:
        while True:
            chunk = handle.read(hash_size)
            if not chunk:
                break
            sha1_hash.update(chunk)
            size_bytes += len(chunk)

    mtime = filepath.stat().st_mtime
    return {
        "sha1": sha1_hash.hexdigest(),
        "mtime_iso": datetime.datetime.fromtimestamp(mtime).isoformat(),
        "size_bytes": str(size_bytes),
    }


class SecurityKernel:
    sensitive_patterns = SENSITIVE_PATTERNS

    @classmethod
    def sanitize(cls, content: str) -> str:
        return sanitize_content(content)

    @classmethod
    def entropy(cls, text: str) -> float:
        return calculate_entropy(text)
