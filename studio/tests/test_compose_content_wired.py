"""Compose a tiny project end-to-end (offline, fake assets) and assert scripted content lands."""
import json
from pathlib import Path
import pytest

from studio import config
from studio.compose import compose


@pytest.fixture
def mini_project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    pdir = tmp_path / "mini"
    pdir.mkdir()
    (pdir / "research_brief.json").write_text(json.dumps({"topic": "t"}))
    (pdir / "script.json").write_text(json.dumps({"working_title": "T", "scenes": [
        {"scene_no": 1, "on_screen_text": "THE MACHINE", "point": "p",
         "narration": "n", "duration_est_sec": 6,
         "claims": [{"claim_id": "c1",
                     "text": '"Pull-to-refresh is addictive." — Loren Brichter',
                     "source_ref": "F1"}]}]}))
    return pdir


def test_compose_renders_claim_quote_into_index(mini_project):
    # uses the project's real pack via the default registry; if a pack id is needed,
    # the test for your environment passes pack_id="dark-truth-social".
    out = compose("mini", pack_id="dark-truth-social")
    html = Path(out).read_text(encoding="utf-8")
    assert "quote-card" in html
    assert "Pull-to-refresh is addictive" in html
    assert "Loren Brichter" in html
    assert "THE" in html and "MACHINE" in html
