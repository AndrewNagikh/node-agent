"""Minimal YAML loader for benchmark configs (no PyYAML required)."""

from __future__ import annotations

import re
from typing import Any


def _strip_comment(line: str) -> str:
    if "#" in line:
        # ignore # inside quotes — configs don't use quoted hashes
        return line.split("#", 1)[0].rstrip()
    return line.rstrip()


def load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        return _parse_mapping(text.splitlines(), 0)[0]


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if not raw or raw in ("null", "~"):
        return None
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _parse_mapping(lines: list[str], idx: int, indent: int = 0) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while idx < len(lines):
        line = _strip_comment(lines[idx])
        idx += 1
        if not line.strip():
            continue
        cur_indent = len(line) - len(line.lstrip(" "))
        if cur_indent < indent:
            idx -= 1
            break
        if cur_indent > indent:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            idx -= 1
            break
        if ":" not in stripped:
            continue
        key, rest = stripped.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest:
            result[key] = _parse_value(rest)
        else:
            nested, idx = _parse_block(lines, idx, indent + 2)
            result[key] = nested
    return result, idx


def _parse_block(lines: list[str], idx: int, indent: int) -> tuple[Any, int]:
    while idx < len(lines):
        nxt = _strip_comment(lines[idx])
        if not nxt.strip():
            idx += 1
            continue
        nxt_indent = len(nxt) - len(nxt.lstrip(" "))
        if nxt_indent < indent:
            return {}, idx
        if nxt.lstrip().startswith("- "):
            return _parse_list(lines, idx, indent)
        return _parse_mapping(lines, idx, indent)
    return {}, idx


def _parse_list(lines: list[str], idx: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while idx < len(lines):
        line = _strip_comment(lines[idx])
        if not line.strip():
            idx += 1
            continue
        cur_indent = len(line) - len(line.lstrip(" "))
        if cur_indent < indent:
            break
        stripped = line.strip()
        if not stripped.startswith("- "):
            break
        payload = stripped[2:].strip()
        idx += 1
        if payload:
            items.append(_parse_value(payload))
        else:
            nested, idx = _parse_block(lines, idx, indent + 2)
            items.append(nested)
    return items, idx


def load_yaml_file(path) -> dict[str, Any]:
    return load_yaml(path.read_text(encoding="utf-8"))
