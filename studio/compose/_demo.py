"""Demo fixture — the 'dark truth behind social media' brief, mirroring the win.

Stands in for the pipeline's research_brief.json + script.json so the Composer can
be demonstrated end-to-end without a live LLM (script generation is exercised
separately by the front-pipeline tests). The scene shapes match Marlow's contract
(scene_no / point / narration / on_screen_text / claims / duration_est_sec).
"""

from __future__ import annotations

from pathlib import Path

from .. import pipeline
from . import compose

BRIEF = {
    "schema_version": "studio-1",
    "topic": "the attention economy / dark truth behind social media",
    "angle": "the addiction is designed, not your weakness",
    "overview": "Platforms engineer compulsion; the cost is your time and attention.",
    "verified_facts": [
        {"claim": "5.66 billion people use social media.", "sources": [0], "confidence": "high"},
        {"claim": "Pull-to-refresh mimics a slot machine's variable reward.", "sources": [1], "confidence": "high"},
    ],
    "myths_and_corrections": [],
    "contested_or_uncertain": [],
    "key_statistics": [],
    "sources": [
        {"url": "https://datareportal.com", "title": "DataReportal 2025"},
        {"url": "https://www.bbc.com", "title": "BBC 2018"},
    ],
}

SCRIPT = {
    "schema_version": "studio-1",
    "working_title": "Dark Truth Behind The Social Media",
    "hook": "You didn't choose to scroll.",
    "cta": "Log off. Breathe. Live real.",
    "total_scenes": 9,
    "est_runtime_sec": 80,
    "scenes": [
        {"scene_no": 1, "beat": "hook", "point": "the compulsion is automatic",
         "narration": "Before this video ends, you'll unlock your phone four more times. You won't decide to.",
         "on_screen_text": "YOU'LL UNLOCK YOUR PHONE MORE TIMES", "visual_note": "", "duration_est_sec": 6, "claims": []},
        {"scene_no": 2, "beat": "title", "point": "the thesis",
         "narration": "This is the dark truth behind social media.",
         "on_screen_text": "DARK TRUTH BEHIND THE SOCIAL MEDIA", "visual_note": "", "duration_est_sec": 4, "claims": []},
        {"scene_no": 3, "beat": "scale", "point": "users worldwide",
         "narration": "Five point six billion of us. Every single day.",
         "on_screen_text": "5.66B USERS", "visual_note": "", "duration_est_sec": 10,
         "claims": [{"claim_id": "c1", "text": "5.66 billion people use social media.", "support": "F1"}]},
        {"scene_no": 4, "beat": "machine", "point": "designed to hook",
         "narration": "This isn't a bug. It's the product. The endless feed is a slot machine for your brain.",
         "on_screen_text": "IT'S NOT A BUG. IT'S THE PRODUCT", "visual_note": "", "duration_est_sec": 11, "claims": []},
        {"scene_no": 5, "beat": "engineers", "point": "the insiders admit it",
         "narration": "The man who invented infinite scroll calls it behavioral cocaine.",
         "on_screen_text": "THE ENGINEERS SPEAK", "visual_note": "", "duration_est_sec": 11, "claims": []},
        {"scene_no": 6, "beat": "cost", "point": "the comparison trap",
         "narration": "It's measuring your real life against everyone else's highlight reel.",
         "on_screen_text": "YOUR WORST VS THEIR HIGHLIGHT REEL", "visual_note": "", "duration_est_sec": 10, "claims": []},
        {"scene_no": 7, "beat": "willpower", "point": "not a discipline problem",
         "narration": "Stop blaming your willpower. You're not weak — you're outgunned. It's a designed problem.",
         "on_screen_text": "IT'S A DESIGNED PROBLEM", "visual_note": "", "duration_est_sec": 12, "claims": []},
        {"scene_no": 8, "beat": "takeback", "point": "take back control",
         "narration": "Kill the red badges. Switch your screen to grayscale. Pick two windows a day.",
         "on_screen_text": "TAKE IT BACK", "visual_note": "", "duration_est_sec": 11, "claims": []},
        {"scene_no": 9, "beat": "outro", "point": "live real",
         "narration": "The algorithm doesn't care about your peace. Protect it yourself.",
         "on_screen_text": "LOG OFF. BREATHE. LIVE REAL", "visual_note": "", "duration_est_sec": 6, "claims": []},
    ],
}


def build_demo(slug: str = "dark-truth-v2", *, pack_id: str = "dark-truth-social") -> Path:
    """Scaffold the project, write the fixture brief + script, author index.html."""
    import json

    pdir = pipeline.scaffold_project(slug)
    (pdir / "research_brief.json").write_text(json.dumps(BRIEF, indent=2), encoding="utf-8")
    (pdir / "script.json").write_text(json.dumps(SCRIPT, indent=2), encoding="utf-8")
    return compose(slug, pack_id=pack_id)


if __name__ == "__main__":  # pragma: no cover
    out = build_demo()
    print("composed:", out)
