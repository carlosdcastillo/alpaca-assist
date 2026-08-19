"""Immutable Milestone-1 HTML artifact storage for one Pack session."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from core.artifact_protocol import ARTIFACT_ID_RE
from core.artifact_protocol import validate_manifest

MAX_ARTIFACT_HTML_BYTES = 2 * 1024 * 1024

_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; navigate-to 'none'"
)
_CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">'


def _inject_csp(html: str) -> str:
    """Put the policy before all author content and remove weaker policies."""
    html = re.sub(
        r"<meta\b[^>]*http-equiv\s*=\s*(['\"])content-security-policy\1[^>]*>",
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(r"^\s*<!doctype\b[^>]*>", "", html, flags=re.IGNORECASE)
    # In the HTML parser's "before head" state, this meta token creates the
    # head and is processed before any token from the worker's document. That
    # ordering matters: CSP meta policies do not retroactively block requests.
    return f"<!doctype html>{_CSP_META}{html}"


class ArtifactStore:
    def __init__(self, session_dir: Path | str) -> None:
        self.artifacts_dir = Path(session_dir) / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "artifact_publish_html":
            return self.publish_html(params.get("path"), params.get("title"))
        if method == "artifact_attach":
            return self.attach(params.get("artifact_id"))
        raise ValueError(f"Unknown artifact method: {method!r}")

    def publish_html(self, path_value: Any, title_value: Any) -> dict[str, Any]:
        if not isinstance(path_value, str) or not path_value:
            raise ValueError("path must be a non-empty string")
        if not isinstance(title_value, str) or not title_value.strip():
            raise ValueError("title must be a non-empty string")
        path = Path(os.path.expanduser(path_value)).resolve()
        if path.suffix.lower() not in {".html", ".htm"} or not path.is_file():
            raise ValueError("artifact path must name an existing .html file")
        size = path.stat().st_size
        if size > MAX_ARTIFACT_HTML_BYTES:
            raise ValueError(
                f"artifact is too large ({size} bytes; limit is {MAX_ARTIFACT_HTML_BYTES})",
            )
        try:
            html = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("artifact HTML must be UTF-8") from exc
        secured = _inject_csp(html)
        if len(secured.encode("utf-8")) > MAX_ARTIFACT_HTML_BYTES:
            raise ValueError(
                "artifact exceeds the size limit after security policy injection",
            )

        artifact_id = f"art_{uuid.uuid4().hex[:8]}"
        manifest = {
            "version": 1,
            "artifact_id": artifact_id,
            "kind": "html",
            "title": title_value.strip()[:200],
            "revision": 1,
            "renderer": "client_html",
            "capabilities": {
                "backend": False,
                "network": False,
                "user_input": True,
            },
        }
        artifact_dir = self.artifacts_dir / artifact_id
        artifact_dir.mkdir(mode=0o700)
        (artifact_dir / "index.html").write_text(secured, encoding="utf-8")
        (artifact_dir / "artifact.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"manifest": manifest}

    def attach(self, artifact_id: Any) -> dict[str, Any]:
        if (
            not isinstance(artifact_id, str)
            or ARTIFACT_ID_RE.fullmatch(artifact_id) is None
        ):
            raise ValueError("invalid artifact id")
        artifact_dir = self.artifacts_dir / artifact_id
        try:
            manifest = validate_manifest(
                json.loads(
                    (artifact_dir / "artifact.json").read_text(encoding="utf-8"),
                ),
            )
            html = (artifact_dir / "index.html").read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"artifact {artifact_id} is unavailable") from exc
        if manifest is None or manifest["artifact_id"] != artifact_id:
            raise ValueError(f"artifact {artifact_id} has an invalid manifest")
        if len(html.encode("utf-8")) > MAX_ARTIFACT_HTML_BYTES:
            raise ValueError(f"artifact {artifact_id} exceeds the size limit")
        return {"manifest": manifest, "html": html}
