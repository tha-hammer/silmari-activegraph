"""Canonical validators shared by Pack construction and manifests."""

from __future__ import annotations

import re
from typing import Any


_PACK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_pack_name(value: Any, *, field: str) -> str:
    """Return an unchanged canonical 1–64 character Pack identity."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if _PACK_NAME_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field} must match ^[a-z][a-z0-9_]{{0,63}}$ (1–64 characters)"
        )
    return value


__all__ = ["validate_pack_name"]
