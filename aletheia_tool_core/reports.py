import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union


def write_json_report(data: Any, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return out_path


def format_markdown_section(title: str, body: Union[str, Dict[str, Any], List[Any]]) -> str:
    lines: List[str] = [f"## {title}", ""]
    if isinstance(body, str):
        lines.append(body)
    elif isinstance(body, dict):
        for key, value in body.items():
            lines.append(f"- **{key}**: {value}")
    elif isinstance(body, list):
        for item in body:
            lines.append(f"- {item}")
    else:
        lines.append(str(body))
    lines.append("")
    return "\n".join(lines)


def write_markdown_report(title: str, sections: Dict[str, Union[str, Dict[str, Any], List[Any]]], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = [f"# {title}", ""]
    for section_title, content in sections.items():
        lines.append(format_markdown_section(section_title, content))
    with out_path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    return out_path
