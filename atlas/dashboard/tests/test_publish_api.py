"""T3 publish package — the read-only review SHELL + the 'no fire path' safety property.

The package is assembled from artifacts + settings; NOTHING fires (real publishing is
Herald, #6). These prove the shell shape, the niche→channel routing + §9 verification
blockers, and — critically — that there is NO endpoint that publishes (edge case E8).
"""
from __future__ import annotations

import json

from dashboard import publish


def test_publish_package_shape_for_done_project(client, slugs):
    r = client.get(f"/api/publish/{slugs['done']}")
    assert r.status_code == 200, r.text
    pkg = r.json()
    # the exact reviewable package fields (spec §4 T3)
    p = pkg["package"]
    assert set(["title", "description", "tags", "thumbnail", "visibility", "schedule"]) <= set(p)
    assert p["visibility"] == "private"           # safe default
    assert p["schedule"] is None                  # set only AFTER approval (Herald), never before
    assert p["thumbnail"]["available"] is False   # arrives with Glint (#8)
    # the fire action is ALWAYS disabled — there is no publish path until Herald
    assert pkg["fire_enabled"] is False
    assert any("Herald" in b for b in pkg["blockers"])


def test_publish_package_unknown_project_404(client):
    assert client.get("/api/publish/no-such-slug").status_code == 404


def test_publish_blocks_without_render(client, slugs):
    # a project still at a gate has no finished render → not ready, more blockers
    pkg = client.get(f"/api/publish/{slugs['blocked_clean']}").json()
    assert pkg["ready"] is False
    assert pkg["would_publish"] is False
    assert any("render" in b.lower() for b in pkg["blockers"])


def test_publish_routes_niche_to_channel_and_flags_verification(client, slugs, tmp_path):
    """With a niche→channel mapping in settings, the package routes to that channel and
    surfaces the §9 verification flags as blockers (project + channel must be verified)."""
    # configure a niche + an UNVERIFIED mapped channel
    client.put("/api/settings", json={
        "niches": [{"name": "AI tools", "default_length": "short", "channel_id": "UC_test"}],
        "channels": [{"title": "Test Channel", "channel_id": "UC_test", "niche_id": "0",
                      "project_verified": False, "channel_phone_verified": False}],
    })
    # stamp the done project with that niche on disk
    pp = client._app.state.projects_dir / slugs["done"] / "project.json"
    proj = json.loads(pp.read_text())
    proj.setdefault("config", {})["niche"] = "AI tools"
    pp.write_text(json.dumps(proj))

    pkg = client.get(f"/api/publish/{slugs['done']}").json()
    assert pkg["channel"] and pkg["channel"]["channel_id"] == "UC_test"
    assert pkg["would_publish"] is False
    assert any("verified" in b.lower() for b in pkg["blockers"])


def test_no_publish_fire_endpoint_exists(client, slugs):
    """The 'no auto-fire-unreviewed' property (§4 T3 / E8): there is NO route that fires a
    publish. A POST to the publish path is not allowed (405) — nothing can publish."""
    slug = slugs["done"]
    r = client.post(f"/api/publish/{slug}", json={})
    assert r.status_code in (404, 405)            # no such write route
    r2 = client.post(f"/api/publish/{slug}/confirm", json={})
    assert r2.status_code in (404, 405)           # and no confirm-fire route either
