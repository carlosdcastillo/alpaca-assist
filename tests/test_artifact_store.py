from __future__ import annotations

from pathlib import Path

import pytest

from core.artifact_store import MAX_ARTIFACT_HTML_BYTES
from core.artifact_store import ArtifactStore

FIXTURES = Path(__file__).parent / "fixtures"


def test_publish_snapshots_html_with_restrictive_csp(tmp_path: Path) -> None:
    source = tmp_path / "game.html"
    source.write_text(
        "<html><head><title>Game</title></head><body><canvas></canvas>"
        "<script>window.played = true</script></body></html>",
    )
    store = ArtifactStore(tmp_path / "session")

    published = store.publish_html(str(source), "Tiny game")
    artifact_id = published["manifest"]["artifact_id"]
    attached = store.attach(artifact_id)

    assert attached["manifest"]["capabilities"] == {
        "backend": False,
        "network": False,
        "user_input": True,
    }
    assert "Content-Security-Policy" in attached["html"]
    assert "connect-src 'none'" in attached["html"]
    assert "navigate-to 'none'" in attached["html"]
    assert "script-src 'unsafe-inline'" in attached["html"]
    assert "window.played = true" in attached["html"]
    assert str(source) not in str(published)


def test_snapshot_is_immutable_when_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "viz.html"
    source.write_text("<h1>first</h1>")
    store = ArtifactStore(tmp_path / "session")
    artifact_id = store.publish_html(str(source), "Visualization")["manifest"][
        "artifact_id"
    ]
    source.write_text("<h1>second</h1>")

    assert "first" in store.attach(artifact_id)["html"]
    assert "second" not in store.attach(artifact_id)["html"]


def test_replaces_author_supplied_csp(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.html"
    source.write_text(
        '<head><meta http-equiv="Content-Security-Policy" content="default-src *">'
        "</head>",
    )
    store = ArtifactStore(tmp_path / "session")
    artifact_id = store.publish_html(str(source), "Safe")["manifest"]["artifact_id"]

    html = store.attach(artifact_id)["html"]
    assert "default-src *" not in html
    assert html.count("Content-Security-Policy") == 1


def test_csp_precedes_all_author_content(tmp_path: Path) -> None:
    source = tmp_path / "odd.html"
    source.write_text('<img src="https://example.com/leak"><head></head>')
    store = ArtifactStore(tmp_path / "session")
    artifact_id = store.publish_html(str(source), "Odd")["manifest"]["artifact_id"]

    html = store.attach(artifact_id)["html"]
    assert html.index("Content-Security-Policy") < html.index("https://example.com")


def test_enforces_size_limit(tmp_path: Path) -> None:
    source = tmp_path / "huge.html"
    source.write_bytes(b"x" * (MAX_ARTIFACT_HTML_BYTES + 1))
    store = ArtifactStore(tmp_path / "session")

    with pytest.raises(ValueError, match="too large"):
        store.publish_html(str(source), "Huge")


@pytest.mark.parametrize("name", ["plain.txt", "missing.html"])
def test_rejects_wrong_or_missing_input(tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    if name == "plain.txt":
        source.write_text("hello")
    store = ArtifactStore(tmp_path / "session")

    with pytest.raises(ValueError, match="existing .html"):
        store.publish_html(str(source), "Nope")


def test_unknown_artifact_degrades_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unavailable"):
        ArtifactStore(tmp_path).attach("art_deadbeef")


def test_publishes_self_contained_double_pendulum_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "session")

    published = store.dispatch(
        "artifact_publish_html",
        {
            "path": str(FIXTURES / "double_pendulum.html"),
            "title": "Double Pendulum Lab",
        },
    )
    attached = store.attach(published["manifest"]["artifact_id"])

    assert attached["manifest"]["title"] == "Double Pendulum Lab"
    assert '<canvas id="scene"' in attached["html"]
    assert "requestAnimationFrame(frame)" in attached["html"]
    assert "https://" not in attached["html"]
