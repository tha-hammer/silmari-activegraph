"""Canonical prompt identity payloads and hashes."""

from __future__ import annotations

import hashlib
import json
from typing import Any


_OMIT_TOOLS = object()


def _message_dict(message: Any) -> dict[str, Any]:
    if hasattr(message, "to_dict"):
        return message.to_dict()
    return dict(message)


def build_prompt_identity_payload(
    *,
    model: str,
    system: str,
    messages: list[Any],
    output_schema_name: str | None,
    output_schema_json: dict[str, Any] | None,
    max_tokens: int,
    temperature: float,
    top_p: float,
    deterministic: bool,
    tools: Any = _OMIT_TOOLS,
    structured_output_mode: str = "prompt",
) -> dict[str, Any]:
    """Build one normalized prompt identity payload.

    Omitting ``tools`` preserves the public ``AssembledPrompt`` identity
    domain. Passing either ``None`` or an empty collection selects the
    per-turn/fixture domain and emits ``"tools": null``.
    """

    payload: dict[str, Any] = {
        "model": model,
        "system": system,
        "messages": [_message_dict(message) for message in messages],
        "output_schema_name": output_schema_name,
        "output_schema_json": output_schema_json,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "top_p": float(top_p),
        "deterministic": bool(deterministic),
    }
    if tools is not _OMIT_TOOLS:
        payload["tools"] = list(tools) if tools else None
    if structured_output_mode == "native":
        payload["structured_output_mode"] = "native"
    return payload


def canonical_prompt_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def hash_prompt_payload(payload: dict[str, Any]) -> str:
    canonical = canonical_prompt_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
