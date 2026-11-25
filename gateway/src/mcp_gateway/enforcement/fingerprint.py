"""Canonical fingerprints of tool calls (loop detection, approval de-duplication)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _normalise(value: Any) -> Any:
    """Make semantically equal JSON compare equal: integral floats become ints."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_normalise(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def call_fingerprint(tool: str, arguments: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical ``{tool, arguments}`` document: key order, whitespace and
    1 vs 1.0 cannot make a repeated call look new."""
    return hashlib.sha256(
        canonical_json({"tool": tool, "arguments": arguments}).encode()
    ).hexdigest()
