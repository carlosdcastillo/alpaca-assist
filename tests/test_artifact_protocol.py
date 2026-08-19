from __future__ import annotations

import base64
import json

import pytest

from core.artifact_protocol import SENTINEL
from core.artifact_protocol import encode_artifact_result
from core.artifact_protocol import parse_artifact_result


@pytest.fixture
def manifest() -> dict:
    return {
        "version": 1,
        "artifact_id": "art_1a2b3c4d",
        "kind": "html",
        "title": "Parser architecture",
        "revision": 1,
        "renderer": "client_html",
        "capabilities": {"backend": False, "network": False, "user_input": True},
    }


def test_round_trip_through_storage_envelope(manifest: dict) -> None:
    encoded = encode_artifact_result(manifest)
    wrapped = json.dumps({"content": [{"type": "text", "text": encoded}]})
    assert parse_artifact_result(wrapped) == manifest
    assert "/" not in encoded


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"artifact_id": "../secret"},
        {"title": ""},
        {"renderer": "remote"},
        {"capabilities": {"backend": False, "network": True, "user_input": True}},
    ],
)
def test_rejects_unsupported_or_malformed_manifests(
    manifest: dict,
    change: dict,
) -> None:
    invalid = {**manifest, **change}
    token = base64.urlsafe_b64encode(json.dumps(invalid).encode()).decode().rstrip("=")
    assert parse_artifact_result(SENTINEL + token) is None


def test_malformed_sentinel_degrades_to_plain_text() -> None:
    assert parse_artifact_result("ordinary result") is None
    assert parse_artifact_result(SENTINEL + "not-json") is None
