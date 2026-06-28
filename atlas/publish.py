"""Close the loop to GATED publishing — compliance → unlisted upload → human yes.

prepare_publish is the whole publish flow, and its shape encodes the rule that
nothing goes public on autopilot:
  1. run the COMPLIANCE GATE; write the human-readable report no matter what;
  2. if it BLOCKS — stop. No upload, no go-public ask. The board sees the reasons.
  3. if it PASSES — upload UNLISTED (never public), record it in CEO state, and file
     a request_from_ceo(approval) asking the board to make it public. That approval
     is the human checkpoint; this module never sets privacy=public itself.

ingest_analytics feeds real views/watch-time/RPM back into ceo/state.json, and
adapt_strategy lets the niche strategy follow what actually earns.
"""
from __future__ import annotations

from pathlib import Path

import boundary
import compliance
import youtube
from ceo import state as ceo_state


class PublishError(ValueError):
    """Unknown project — a caller mistake."""


def _project_meta(pdir: Path) -> dict:
    import chat_state
    proj = chat_state.load_json(pdir / "project.json", {}) or {}
    script = chat_state.load_json(pdir / "script.json", {}) or {}
    title = (proj.get("title") or script.get("working_title")
             or proj.get("topic") or pdir.name)
    niche = proj.get("niche", "")
    desc = (f"{title}\n\nAn original, fact-checked explainer. "
            "All assets license-cleared (CC0/PD/CC-BY/CC-BY-SA) with attribution.")
    return {"title": title, "description": desc,
            "tags": [t for t in (niche, "explainer", "educational") if t]}


def prepare_publish(slug: str, *, uploader=None, privacy: str = "unlisted") -> dict:
    """Gate, then (if clean) upload UNLISTED and ask the board to go public.
    `uploader` is injectable (tests / a custom client); when omitted the live
    youtube.upload is used and a missing-credential case degrades to a PREPARED
    record + a credentials ask (never a hard stop)."""
    import projects
    pdir = projects.project_dir(slug)
    if pdir is None:
        raise PublishError(f"no project named {slug!r}")
    pdir = Path(pdir)

    # 1. the gate — and the report, written either way for the human.
    report = compliance.check(pdir)
    report_text = compliance.format_report(report)
    (pdir / "compliance_report.txt").write_text(report_text)
    import chat_state
    chat_state.atomic_write_json(pdir / "compliance_report.json", report)

    # 2. BLOCKED -> stop. No upload, no go-public ask.
    if not report["passed"]:
        boundary.ceo_log(f"PUBLISH BLOCKED for '{slug}': {len(report['blockers'])} "
                         f"blocker(s) — {report['blockers'][:3]}")
        _set_video(slug, status="blocked_compliance")
        return {"slug": slug, "passed": False, "blocked": True, "uploaded": False,
                "video_id": None, "privacy": None, "approval": None,
                "report_path": str(pdir / "compliance_report.txt"),
                "reasons": report["blockers"], "report": report_text}

    # 3. PASSED -> upload UNLISTED (never public here).
    meta = _project_meta(pdir)
    video_path = str(pdir / "video.mp4")
    do_upload = uploader or youtube.upload
    try:
        up = do_upload(title=meta["title"], description=meta["description"],
                       tags=meta["tags"], video_path=video_path, privacy=privacy)
        uploaded = True
    except youtube.MissingCredentials:
        # never stall: prepare locally + ask the board for OAuth creds (env only).
        up = {"video_id": None, "privacy": privacy, "url": None, "status": "prepared"}
        uploaded = False
        boundary.request_from_ceo(
            "api_key", "YouTube OAuth credentials (client id, client secret, refresh token)",
            "to upload the finished video to the channel as unlisted",
            "set them in the environment as YT_CLIENT_ID / YT_CLIENT_SECRET / "
            "YT_REFRESH_TOKEN (env only — never in code)")

    chat_state.atomic_write_json(pdir / "upload.json", {**up, "title": meta["title"]})
    _set_video(slug, status=("uploaded_unlisted" if uploaded else "prepared_unlisted"),
               video_id=up.get("video_id"), privacy=up.get("privacy", privacy),
               compliance_passed=True)

    # the HUMAN CHECKPOINT: ask the board to approve going public. No auto-publish.
    approval = boundary.request_from_ceo(
        "approval",
        f"approve making '{meta['title']}' PUBLIC on YouTube (currently {privacy})",
        "it passed the compliance gate and is uploaded unlisted; going public is your call",
        "review the compliance report, then reply 'publish' to go public — or keep it unlisted")
    boundary.ceo_log(f"PUBLISH READY for '{slug}': uploaded {up.get('privacy', privacy)} "
                     f"({up.get('video_id')}); awaiting board approval to go public.")

    return {"slug": slug, "passed": True, "blocked": False, "uploaded": uploaded,
            "video_id": up.get("video_id"), "privacy": up.get("privacy", privacy),
            "url": up.get("url"), "approval": approval["record"],
            "report_path": str(pdir / "compliance_report.txt"), "report": report_text}


def _set_video(slug: str, **changes) -> None:
    """Update the CEO state's video entry for `slug` (no-op if it isn't tracked)."""
    st = ceo_state.load()
    if any(v.get("slug") == slug for v in st.get("videos", [])):
        ceo_state.update_video(st, slug, **changes)


# ----------------------------------------------------------------------
# Analytics -> CEO loop -> strategy.
# ----------------------------------------------------------------------
def ingest_analytics(slug: str, *, fetch=None) -> dict:
    """Pull a video's performance and write it into ceo/state.json, then let the
    strategy adapt to what actually earns. Returns the metrics."""
    st = ceo_state.load()
    video = next((v for v in st.get("videos", []) if v.get("slug") == slug), None)
    if video is None or not video.get("video_id"):
        raise PublishError(f"no uploaded video for {slug!r} (no video_id to measure)")

    do_fetch = fetch or youtube.fetch_analytics
    metrics = do_fetch(video["video_id"])
    ceo_state.update_video(st, slug, metrics=metrics)

    # roll the per-video revenue estimate up into the company's monthly figure.
    st = ceo_state.load()
    total = sum(float(v.get("metrics", {}).get("estimated_revenue_usd", 0) or 0)
                for v in st.get("videos", []))
    ceo_state.update(st, revenue_usd_per_month=round(total, 2))
    adapt_strategy(ceo_state.load())
    boundary.ceo_log(f"ANALYTICS '{slug}': {metrics.get('views')} views, "
                     f"RPM ${metrics.get('rpm_usd')}, ~${metrics.get('estimated_revenue_usd')}.")
    return metrics


def adapt_strategy(st: dict) -> dict:
    """Adapt the niche strategy toward the best-earning niche (RPM-weighted). Strategy
    follows the data, not vibes — the CEO charter made real."""
    by_niche: dict[str, float] = {}
    for v in st.get("videos", []):
        niche = v.get("niche") or v.get("topic") or ""
        rev = float(v.get("metrics", {}).get("estimated_revenue_usd", 0) or 0)
        if niche:
            by_niche[niche] = by_niche.get(niche, 0.0) + rev
    if not by_niche or max(by_niche.values()) <= 0:
        return st                                  # nothing earning yet — hold
    best = max(by_niche, key=by_niche.__getitem__)
    ceo_state.set_strategy(
        st, f"Double down on '{best}' — it earns the most so far "
            f"(~${round(by_niche[best], 2)}). Concentrate the backlog there and cut "
            "what underperforms.")
    return ceo_state.load()
