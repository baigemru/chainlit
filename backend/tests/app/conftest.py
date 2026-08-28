"""A stand-in for the built frontend.

``chainlit/frontend/dist`` is a build artefact and is not in the repository,
so a test that reads the real one passes or fails depending on whether
somebody ran ``pnpm build``. Every test here points the plugin at a directory
it built itself, which also lets it assert on the exact bytes served.
"""

from pathlib import Path

import pytest

INDEX_MARKER = "<!-- chainlit spa root -->"


@pytest.fixture
def frontend_dir(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(f"<html><body>{INDEX_MARKER}</body></html>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    return dist
