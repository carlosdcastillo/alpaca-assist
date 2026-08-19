"""Durable descriptors for locally rendered interactive artifacts."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

SENTINEL = "@@ALPACA_ARTIFACT_RESULT@@"
ARTIFACT_VERSION = 1
ARTIFACT_ID_RE = re.compile(r"^art_[0-9a-f]{8}$")


def validate_manifest(value: Any) -> dict[str, Any] | None:
    """Return a normalized v1 manifest, or None for unsupported input."""
    if not isinstance(value, dict):
        return None
    required = {
        "version": int,
        "artifact_id": str,
        "kind": str,
        "title": str,
        "revision": int,
        "renderer": str,
        "capabilities": dict,
    }
    if any(type(value.get(key)) is not kind for key, kind in required.items()):
        return None
    capabilities = value["capabilities"]
    if (
        value["version"] != ARTIFACT_VERSION
        or ARTIFACT_ID_RE.fullmatch(value["artifact_id"]) is None
        or value["kind"] != "html"
        or not value["title"].strip()
        or value["revision"] < 1
        or value["renderer"] != "client_html"
        or set(capabilities) != {"backend", "network", "user_input"}
        or any(not isinstance(item, bool) for item in capabilities.values())
        or capabilities["backend"]
        or capabilities["network"]
    ):
        return None
    return {
        key: value[key]
        for key in (
            "version",
            "artifact_id",
            "kind",
            "title",
            "revision",
            "renderer",
            "capabilities",
        )
    }


def encode_artifact_result(manifest: dict[str, Any]) -> str:
    normalized = validate_manifest(manifest)
    if normalized is None:
        raise ValueError("invalid artifact manifest")
    payload = json.dumps(normalized, separators=(",", ":"), ensure_ascii=False).encode()
    return SENTINEL + base64.urlsafe_b64encode(payload).decode().rstrip("=")


def parse_artifact_result(content: str) -> dict[str, Any] | None:
    idx = content.find(SENTINEL)
    if idx == -1:
        return None
    encoded = content[idx + len(SENTINEL) :]
    match = re.match(r"[A-Za-z0-9_-]+", encoded)
    if match is None:
        return None
    token = match.group(0)
    try:
        payload = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        return validate_manifest(json.loads(payload.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
