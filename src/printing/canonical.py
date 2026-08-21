"""Deterministic JSON serialization and SHA-256 helpers for printing data."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel


def canonicalize(value: Any) -> Any:
    """Return a JSON-safe value with mapping keys normalized to strings."""
    if isinstance(value, BaseModel):
        return canonicalize(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize without whitespace or volatile formatting differences."""
    return json.dumps(
        canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 of :func:`canonical_json_bytes`."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
