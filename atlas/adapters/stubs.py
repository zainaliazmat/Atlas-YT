"""Stub specialists — the five not-yet-built roles, as registered slots.

Each of the five specialists (Scriptwriter, Art Director, Asset Sourcer, Composition
Engineer, Audio) has a registry entry but no engine yet. This module gives the
pipeline something real to run against them: deterministic, OFFLINE producers that
read the upstream artifact and write a SCHEMA-VALID placeholder artifact for the
next stage. The data is placeholder; the data-FLOW and the contract validation are
genuine — so the full pipeline, its ordering, and both gates are exercised end-to-end
with no network and no API. When a real specialist lands, it drops into the same slot
and writes the same contract; nothing else changes.

Two surfaces live here:
- PRODUCERS (the pipeline's stage workers): pure file I/O, no progress/LLM, easy to
  unit-test — each returns an `Artifact` the pipeline validates and records.
- `StubAdapter` (the meeting-room delegation path): so `/agents` and `ask_<name>`
  work for a stub slot, and a stub job called conversationally answers honestly
  instead of pretending to be a finished specialist.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

import chat_state
from adapters.base import Adapter
from contracts import CONTRACT_VERSION

# DEMO TOGGLE: set this env var truthy to make the stub script include ONE claim the
# research brief never supports (a source_ref that doesn't resolve). With the REAL
# pass-2 fact-check wired in, that makes the fact-check gate BLOCK — a one-flag demo
# that the gate fires on a real verdict and cannot be approved away. Unset/false =
# coherent stub content whose claims all resolve, so the gate PASSES through.
INJECT_UNSUPPORTED_CLAIM_ENV = "STUB_INJECT_UNSUPPORTED_CLAIM"


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "yes", "true", "on")


# ----------------------------------------------------------------------
# Artifact I/O helpers (atomic writes, reused from chat_state)
# ----------------------------------------------------------------------
@dataclass
class Artifact:
    """One stage's output: where it landed, which contract validates it, its data."""
    rel_path: str             # path under the project dir, e.g. "script.json"
    contract: str | None      # contract name to validate against (None = binary)
    data: dict | None         # the JSON payload (None for binary artifacts)
    summary: str              # one short line the pipeline can narrate


def _read_json(pdir: pathlib.Path, rel: str) -> dict:
    return chat_state.load_json(pdir / rel, {})


def _write_json(pdir: pathlib.Path, rel: str, obj: dict) -> None:
    path = pdir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    chat_state.atomic_write_json(path, obj)


def _write_bytes(pdir: pathlib.Path, rel: str, data: bytes) -> None:
    path = pdir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ----------------------------------------------------------------------
# The producers — one per pipeline stage. (research is here too, so the whole
# pipeline can run fully offline in --stub mode; the real Sage drives research
# otherwise.)
# ----------------------------------------------------------------------
def produce_research(pdir: pathlib.Path, topic: str) -> Artifact:
    """STUB research brief (offline). Real research comes from Sage's engine."""
    # Coherent, self-consistent verified facts (the script lifts these verbatim and
    # cites their sources, so the REAL pass-2 fact-check verdicts `pass`). Stub data,
    # but written as real claims — not literally labelled "placeholder" — so a real
    # brain treats them as the established ground truth they're meant to represent.
    facts = [
        {"claim": f"{topic} is a well-documented subject with a verifiable history.",
         "confidence": "high", "sources": ["https://example.org/1"]},
        {"claim": f"Independent sources broadly agree on the basic facts of {topic}.",
         "confidence": "high", "sources": ["https://example.org/2"]},
        {"claim": f"{topic} has measurable, documented effects reported by multiple sources.",
         "confidence": "medium", "sources": ["https://example.org/3"]},
    ]
    data = {
        "schema_version": CONTRACT_VERSION,
        "topic": topic,
        "angle": "",
        "working_title": f"The truth about {topic}",
        "target_audience": "curious general audience",
        "overview": f"A placeholder research brief on {topic} for the stub pipeline.",
        "verified_facts": facts,
        "myths_and_corrections": [],
        "contested_or_uncertain": [],
        "key_statistics": [], "timeline": [], "notable_quotes": [],
        "open_questions": [], "suggested_angles": [],
        "sources": [{"url": f"https://example.org/{i}", "title": f"Source {i}",
                     "credibility_note": "placeholder"} for i in range(1, 4)],
    }
    _write_json(pdir, "research_brief.json", data)
    return Artifact("research_brief.json", "research_brief", data,
                    f"{len(facts)} verified facts, {len(data['sources'])} sources")


def produce_script(pdir: pathlib.Path, topic: str) -> Artifact:
    """script.json from the brief — one point per scene, each scene cites a source.

    Each placeholder claim is lifted verbatim from a brief `verified_facts` entry and
    its `source_ref` is the 0-based index of a real brief source, so the claims
    RESOLVE and MATCH the brief — the REAL pass-2 fact-check then verdicts `pass` and
    the line flows on through the downstream stub stages.

    Demo toggle: set STUB_INJECT_UNSUPPORTED_CLAIM truthy to append one scene whose
    claim cites a non-existent source (source_ref out of range). The real fact-check
    flags it and the gate BLOCKS — showing the verdict is genuine, not rubber-stamped.
    """
    brief = _read_json(pdir, "research_brief.json")
    facts = brief.get("verified_facts") or []
    scenes = []
    for i, fact in enumerate(facts[:5] or [{"claim": topic}], start=1):
        claim = fact.get("claim", f"{topic} point {i}")
        scenes.append({
            "scene_no": i,
            "beat": "hook" if i == 1 else "point",
            "point": claim,
            "narration": f"Here's the thing about {topic}: {claim}",
            "on_screen_text": claim[:60],
            "claims": [{"claim_id": f"c{i}", "text": claim, "source_ref": i - 1}],
            "visual_note": "single clear visual",
            "duration_est_sec": 8.0,
        })
    if _truthy(os.environ.get(INJECT_UNSUPPORTED_CLAIM_ENV, "")):
        n = len(scenes) + 1
        scenes.append({
            "scene_no": n,
            "beat": "point",
            "point": f"An unsupported aside about {topic}",
            "narration": f"And here's a bold claim about {topic} the research never backed.",
            "on_screen_text": "unsupported claim",
            # source_ref 999 doesn't resolve to any brief source -> mis-sourced -> flagged.
            "claims": [{"claim_id": f"c{n}",
                        "text": f"{topic} secretly does X — a claim absent from the brief.",
                        "source_ref": 999}],
            "visual_note": "single clear visual",
            "duration_est_sec": 8.0,
        })
    data = {
        "schema_version": CONTRACT_VERSION,
        "working_title": brief.get("working_title") or f"The truth about {topic}",
        "hook": f"What everyone gets wrong about {topic}.",
        "cta": "Subscribe for more.",
        "total_scenes": len(scenes),
        "est_runtime_sec": round(sum(s["duration_est_sec"] for s in scenes), 1),
        "scenes": scenes,
    }
    _write_json(pdir, "script.json", data)
    return Artifact("script.json", "script", data, f"{len(scenes)} scenes")


def produce_factcheck(pdir: pathlib.Path, topic: str) -> Artifact:
    """Offline STUB fact-check: auto-verify each script claim; verdict pass.

    NO LONGER WIRED INTO THE PIPELINE — build step #2 replaced the pipeline's
    factcheck stage with the REAL engine (`adapters.sage.produce_factcheck`). This
    rubber-stamping producer is retained only as an offline reference/fixture (it does
    not call Sage's engine), e.g. for tests that need a deterministic factcheck shape.
    """
    script = _read_json(pdir, "script.json")
    claims = []
    for scene in script.get("scenes", []):
        for c in scene.get("claims", []):
            claims.append({
                "claim_id": c.get("claim_id", "c?"),
                "scene_no": scene.get("scene_no", 0),
                "claim_text": c.get("text", ""),
                "status": "verified",
                "sources": [],
                "note": "stub: auto-verified",
            })
    verified = sum(1 for c in claims if c["status"] == "verified")
    flagged = sum(1 for c in claims if c["status"] == "flagged")
    unverifiable = sum(1 for c in claims if c["status"] == "unverifiable")
    data = {
        "schema_version": CONTRACT_VERSION,
        "verdict": "block" if (flagged or unverifiable) else "pass",
        "summary": {"verified": verified, "flagged": flagged,
                    "unverifiable": unverifiable},
        "claims": claims,
    }
    _write_json(pdir, "factcheck_report.json", data)
    return Artifact("factcheck_report.json", "factcheck_report", data,
                    f"{verified} verified, {flagged} flagged, "
                    f"{unverifiable} unverifiable")


def produce_style(pdir: pathlib.Path, topic: str) -> Artifact:
    data = {
        "schema_version": CONTRACT_VERSION,
        "palette": {
            "primary": "#111111", "bg": "#FFFFFF", "text": "#111111",
            "accents": ["#2E6FF2"],
            "signature_highlight": "#FFD000",
        },
        "typography": {"heading": "Inter", "body": "Inter", "scale": 1.25},
        "motion": {"transition_rules": "cut by default", "max_per_scene": 1},
        "layout": {"grid": "12-col", "safe_margins": "5%"},
        "dos": ["one point per scene", "one #FFD000 highlighter beat per video"],
        "donts": ["gratuitous shader transitions"],
        "reference_note": "explainer / Vox-style",
    }
    _write_json(pdir, "style_guide.json", data)
    return Artifact("style_guide.json", "style_guide", data,
                    "palette set; signature #FFD000 reserved")


def produce_storyboard(pdir: pathlib.Path, topic: str) -> Artifact:
    script = _read_json(pdir, "script.json")
    scenes = []
    for s in script.get("scenes", []):
        n = s.get("scene_no", 0)
        scenes.append({
            "scene_no": n,
            "layout": "centered-statement",
            "shots": [{"kind": "title", "content": s.get("on_screen_text", ""),
                       "asset_ref": None}],
            "on_screen_text": s.get("on_screen_text", ""),
            "transition": "cut",
            "signature_beat": (n == 1),
        })
    data = {"schema_version": CONTRACT_VERSION, "total_scenes": len(scenes),
            "scenes": scenes}
    _write_json(pdir, "storyboard.json", data)
    return Artifact("storyboard.json", "storyboard", data, f"{len(scenes)} scenes")


def produce_assets(pdir: pathlib.Path, topic: str) -> Artifact:
    board = _read_json(pdir, "storyboard.json")
    assets = []
    for s in board.get("scenes", []):
        n = s.get("scene_no", 0)
        assets.append({
            "asset_id": f"a{n}",
            "scene_no": n,
            "type": "image",
            "source": "placeholder",
            "uri": f"assets/scene-{n:02d}.png",
            "license": "CC0",
            "attribution": "",
            "status": "placeholder",
        })
    data = {"schema_version": CONTRACT_VERSION, "assets": assets}
    _write_json(pdir, "asset_manifest.json", data)
    return Artifact("asset_manifest.json", "asset_manifest", data,
                    f"{len(assets)} assets (all licensed)")


def produce_narration(pdir: pathlib.Path, topic: str) -> Artifact:
    """narration.wav (placeholder bytes) + narration.transcript.json (timed)."""
    script = _read_json(pdir, "script.json")
    segments, t = [], 0.0
    for s in script.get("scenes", []):
        dur = float(s.get("duration_est_sec", 8.0))
        segments.append({
            "scene_no": s.get("scene_no", 0),
            "start_sec": round(t, 2),
            "end_sec": round(t + dur, 2),
            "text": s.get("narration", ""),
        })
        t += dur
    transcript = {"schema_version": CONTRACT_VERSION,
                  "total_duration_sec": round(t, 2), "segments": segments}
    # A tiny valid-ish WAV header placeholder (44 bytes, no samples).
    wav = (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " +
           (16).to_bytes(4, "little") + (1).to_bytes(2, "little") +
           (1).to_bytes(2, "little") + (22050).to_bytes(4, "little") +
           (44100).to_bytes(4, "little") + (2).to_bytes(2, "little") +
           (16).to_bytes(2, "little") + b"data" + (0).to_bytes(4, "little"))
    _write_bytes(pdir, "audio/narration.wav", wav)
    _write_json(pdir, "audio/narration.transcript.json", transcript)
    return Artifact("audio/narration.transcript.json", "narration_transcript",
                    transcript, f"{len(segments)} segments, {transcript['total_duration_sec']}s")


def produce_compose(pdir: pathlib.Path, topic: str) -> Artifact:
    """Build scene-NN/index.html per scene; lint+validate+inspect each (auto-gate).

    Returns an Artifact whose `data` is None (HTML, not JSON) but carries the
    per-scene inspection results in `summary` so the pipeline can enforce the gate.
    """
    script = _read_json(pdir, "script.json")
    style = _read_json(pdir, "style_guide.json")
    highlight = (style.get("palette") or {}).get("signature_highlight", "#FFD000")
    scenes = script.get("scenes", [])
    results = []
    for s in scenes:
        n = s.get("scene_no", 0)
        text = s.get("on_screen_text", "")
        html = (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            f"<meta charset=\"utf-8\"><title>Scene {n}</title>\n"
            f"<style>mark{{background:{highlight};}}</style>\n</head>\n<body>\n"
            f"<section data-scene=\"{n}\"><h1><mark>{text}</mark></h1>\n"
            f"<p>{s.get('narration','')}</p></section>\n</body>\n</html>\n"
        )
        rel = f"scenes/scene-{n:02d}/index.html"
        (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (pdir / rel).write_text(html)
        results.append((rel, _inspect_scene(html)))
    passed = sum(1 for _, ok in results if ok)
    ok_all = passed == len(results) and bool(results)
    return Artifact(
        f"scenes ({passed}/{len(results)} ok)", None, None,
        ("auto-gate PASS" if ok_all else "auto-gate FAIL") +
        f" — {passed}/{len(results)} scenes lint+validate+inspect clean")


def _inspect_scene(html: str) -> bool:
    """The composition auto-gate's per-scene check: lint + validate + inspect.

    Stub but real: a scene must be non-empty, well-formed-ish (balanced html tags),
    declare a scene section, and carry the signature highlight wiring.
    """
    if not html.strip():
        return False
    if html.count("<html") != 1 or "</html>" not in html:
        return False
    if "data-scene=" not in html:
        return False
    return True


def produce_audiomix(pdir: pathlib.Path, topic: str) -> Artifact:
    transcript = _read_json(pdir, "audio/narration.transcript.json")
    script = _read_json(pdir, "script.json")
    wired = [f"scenes/scene-{s.get('scene_no',0):02d}/index.html"
             for s in script.get("scenes", [])]
    data = {
        "schema_version": CONTRACT_VERSION,
        "total_duration_sec": transcript.get("total_duration_sec", 0.0),
        "tracks": [
            {"role": "narration", "uri": "audio/narration.wav", "gain_db": 0.0,
             "ducking": False},
            {"role": "music", "uri": "audio/bed.mp3", "gain_db": -18.0,
             "ducking": True},
        ],
        "wired_into": wired,
    }
    _write_json(pdir, "audio/audio_manifest.json", data)
    return Artifact("audio/audio_manifest.json", "audio_manifest", data,
                    f"{len(data['tracks'])} tracks, wired into {len(wired)} scenes")


def produce_render(pdir: pathlib.Path, topic: str) -> Artifact:
    """Final render + concat → video.mp4 (placeholder bytes)."""
    mix = _read_json(pdir, "audio/audio_manifest.json")
    dur = mix.get("total_duration_sec", 0.0)
    _write_bytes(pdir, "video.mp4", b"\x00\x00\x00\x18ftypmp42STUB-VIDEO")
    return Artifact("video.mp4", None, None, f"stub render, ~{dur}s")


# ----------------------------------------------------------------------
# The meeting-room delegation surface for the five stub slots
# ----------------------------------------------------------------------
class StubAdapter(Adapter):
    """Registry adapter for a not-yet-built specialist.

    PERSONA `ask` is inherited from Adapter (falls back to "You are <display>." when
    the slot has no soul/ yet). A delegated JOB answers honestly: the slot is
    reserved and its artifacts are produced by the production pipeline, not by a
    finished specialist. The orchestrator narrates this rather than faking success.
    """
    module_name = ""  # no engine to load

    def run_job(self, job_name: str, progress, **params) -> dict:
        who = self.entry.display
        if progress is not None:
            progress.emit(f"{self.entry.emoji} {who} is a registered stub "
                          f"({job_name}) — running the pipeline's placeholder.")
        return {"ok": True,
                "text": (f"{who} is a registered slot; the real specialist isn't "
                         f"built yet. Its '{job_name}' output is produced as a "
                         "schema-valid placeholder by the production pipeline.")}
