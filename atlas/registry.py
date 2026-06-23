"""THE registry — the one place that declares who Atlas can delegate to.

This is the key to "head of all agents, including future ones." Each managed agent
is ONE entry: its name, a one-line blurb of what it's good at, its capabilities
(jobs + persona), the adapter that wraps it, and the sibling project directory.

Atlas reads this registry to know who's in the room. The orchestrator's tools are
GENERATED from it (see tools.py), so:

    ADDING A FUTURE AGENT  =  one AgentEntry here  +  one adapter class.
    No orchestrator edits. No tool wiring by hand. Its tools just appear.

(See the worked third-agent example in the README.)
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from adapters.art_director import ArtDirectorAdapter
from adapters.asset_sourcer import AssetSourcerAdapter
from adapters.audio import AudioAdapter
from adapters.composition_engineer import CompositionEngineerAdapter
from adapters.editorial_coach import EditorialCoachAdapter
from adapters.production_coach import ProductionCoachAdapter
from adapters.reference_analyst import ReferenceAnalystAdapter
from adapters.sage import SageAdapter
from adapters.scout import ScoutAdapter
from adapters.scriptwriter import ScriptwriterAdapter

# Sibling projects live next to atlas/ under the repo root.
_ROOT = pathlib.Path(__file__).resolve().parent.parent


@dataclass
class JobSpec:
    """One delegable job a managed agent can run."""
    name: str                 # internal name passed to adapter.run_job
    tool: str                 # generated SDK tool name, e.g. "scout_find_topics"
    description: str          # what the orchestrator LLM reads to decide to call it
    params: dict              # {param_name: python_type} -> the tool input schema
    timeout: int = 300        # seconds before the job is treated as stalled


@dataclass
class AgentEntry:
    """One managed agent."""
    name: str                 # lowercase handle, e.g. "scout"
    display: str              # human name, e.g. "Viral Scout"
    emoji: str                # status-line emoji, e.g. "🔎"
    blurb: str                # one line: what it's good at
    project_dir: str          # the sibling project directory (engine + soul/)
    adapter_cls: type         # the Adapter subclass that wraps it
    jobs: list = field(default_factory=list)
    persona: bool = True      # exposes an ask_<name> persona tool
    stub: bool = False        # True = registered slot, specialist not yet built
    role: str = ""            # production role/title (e.g. "Scriptwriter")


REGISTRY: list[AgentEntry] = [
    AgentEntry(
        name="scout",
        display="Viral Scout",
        emoji="🔎",
        blurb="Finds ranked, viral-leaning YouTube topic ideas for a niche.",
        project_dir=str(_ROOT / "youtube-topic-agent"),
        adapter_cls=ScoutAdapter,
        role="Topic Scout (intake)",
        jobs=[JobSpec(
            name="find_topics",
            tool="scout_find_topics",
            description=("Find and rank viral-leaning YouTube topic ideas for a "
                         "given niche. Returns a numbered list of ideas, strongest "
                         "first, each with a confidence and the reason it could pop."),
            params={"niche": str},
            timeout=240,
        )],
    ),
    AgentEntry(
        name="sage",
        display="Sage",
        emoji="📚",
        blurb="Produces a fact-checked, sourced research pack on a topic.",
        project_dir=str(_ROOT / "topic-researcher"),
        adapter_cls=SageAdapter,
        role="Researcher & Fact-Checker",
        jobs=[
            JobSpec(
                name="research",
                tool="sage_research",
                description=("Run deep, fact-checked research on a specific topic and "
                             "return a digest: verified facts, myths/corrections, "
                             "contested claims, open questions, and source count. Pass "
                             "an optional 'angle' to focus the investigation."),
                params={"topic": str, "angle": str},
                timeout=360,
            ),
            # Pass-2 (build step #2): the REAL fact-check. SageAdapter.run_job
            # locates the project's script + brief, runs Sage's `factcheck` engine,
            # writes a contract-valid factcheck_report.json, and returns a verdict
            # digest. Same slot/params as the step-#1 stub — no registry change.
            JobSpec(
                name="factcheck",
                tool="sage_factcheck",
                description=("Fact-check a drafted script's claims against the research "
                             "brief and return a verdict (pass/block) with per-claim "
                             "status and the flagged claim ids."),
                params={"topic": str},
                timeout=120,
            ),
        ],
    ),

    # ------------------------------------------------------------------
    # The five production specialists — all BUILT and dropped into their slots.
    # Each real specialist replaced its stub one at a time, with NO orchestrator
    # or pipeline changes (same tool names / params / output contracts).
    # ------------------------------------------------------------------
    # Marlow — the real Scriptwriter (the stub slot was filled). His engine drafts
    # script.json from the brief; ATLAS validates it against the frozen contract at
    # the boundary. Same tool name / params / output contract as the step-#1 stub —
    # only the adapter and the stub flag changed; no orchestrator or tool-wiring edit.
    AgentEntry(
        name="scriptwriter",
        display="Marlow",
        emoji="📝",
        blurb="Turns a research brief into a tight, one-point-per-scene script.",
        project_dir=str(_ROOT / "scriptwriter"),
        adapter_cls=ScriptwriterAdapter,
        role="Scriptwriter",
        jobs=[JobSpec(
            name="write_script",
            tool="scriptwriter_write_script",
            description=("Draft script.json from the research brief: scenes, narration, "
                         "on-screen text, and the claims each scene rests on."),
            params={"topic": str},
        )],
    ),
    # Iris — the real Art Director (the stub slot was filled). Her engine reads the
    # fact-checked script and emits style_guide.json + storyboard.json as plain dicts;
    # ATLAS stamps the bumped schema_version + validates against the frozen contracts at
    # the boundary. She SPECIFIES, never implements (no HTML) — the Composition Engineer
    # builds from her specs. Same tool names / params / output contracts as the stub —
    # only the adapter and the stub flag changed; no orchestrator or tool-wiring edit.
    AgentEntry(
        name="art_director",
        display="Iris",
        emoji="🎨",
        blurb="Designs a restrained style guide + scene-by-scene storyboard; one #FFD000 beat she won't cut.",
        project_dir=str(_ROOT / "art-director"),
        adapter_cls=ArtDirectorAdapter,
        role="Art Director",
        jobs=[
            JobSpec(
                name="design_style",
                tool="art_director_design_style",
                description="Produce style_guide.json (palette, type, motion, the "
                            "signature #FFD000 highlighter beat).",
                params={"topic": str},
            ),
            JobSpec(
                name="build_storyboard",
                tool="art_director_build_storyboard",
                description="Produce storyboard.json: one planned scene per script scene.",
                params={"topic": str},
            ),
        ],
    ),
    # Magpie — the real Asset Sourcer (the stub slot was filled). Her engine reads the
    # storyboard's shots[].asset_ref + content, sources a provably-reusable asset from an
    # allowlist of PD/CC archives, downloads it LOCAL, and emits asset_manifest.json as a
    # plain dict; ATLAS stamps schema_version + validates against the frozen contract at
    # the boundary. She clears licenses strictly — nothing reaches `cleared` without a
    # verified license + complete attribution; uncleared shots ship as flagged local
    # placeholders. Same tool name / params / output contract as the step-#1 stub — only
    # the adapter and the stub flag changed; no orchestrator or tool-wiring edit.
    AgentEntry(
        name="asset_sourcer",
        display="Magpie",
        emoji="🗂️",
        blurb="Sources + licenses each shot's asset from a PD/CC allowlist; flags what won't clear, never guesses PD.",
        project_dir=str(_ROOT / "asset-sourcer"),
        adapter_cls=AssetSourcerAdapter,
        role="Asset Sourcer & Licensing",
        jobs=[JobSpec(
            name="source_assets",
            tool="asset_sourcer_source_assets",
            description="Produce asset_manifest.json: every asset with its license.",
            params={"topic": str},
        )],
    ),
    # Cadence — the real Audio / Sound Designer (the stub slot was filled). Her engine
    # voices the script per-scene (HyperFrames Kokoro tts -> lossless concat) and writes
    # the transcript (the downstream timing authority), then sources a license-cleared
    # music bed, places ONE signature SFX accent on the cut into the storyboard's
    # signature beat, and pre-mixes a documentary master.wav (VO authoritative; bed
    # ducked under VO; nothing uncleared baked). The narration track's uri points at the
    # master, so the renderer muxes the full mix (the master-bridge — no Composition
    # Engineer edits). She emits the transcript + manifest as plain dicts; ATLAS stamps
    # schema_version (transcript "1.0", audio_manifest "1.1") + validates at the boundary.
    # Same tool names / params / output contracts as the stub — only the adapter, the
    # stub flag, the project_dir + display changed; no orchestrator or tool-wiring edit.
    AgentEntry(
        name="audio",
        display="Cadence",
        emoji="🎙️",
        blurb="Voices the script, ducks the bed hard under it, lands one signature SFX on the cut; nothing uncleared gets baked.",
        project_dir=str(_ROOT / "audio-designer"),
        adapter_cls=AudioAdapter,
        role="Audio / Sound Designer",
        jobs=[
            JobSpec(
                name="record_narration",
                tool="audio_record_narration",
                description="Produce narration.wav + narration.transcript.json (timed).",
                params={"topic": str},
                # Per-scene tts has fixed npx+model-load overhead (~11s/scene); a 10+
                # scene script runs sequentially well past the 300s default. (Per-scene
                # tts is parallelizable — a documented follow-up.)
                timeout=900,
            ),
            JobSpec(
                name="mix_audio",
                tool="audio_mix_audio",
                description="Produce audio_manifest.json wired into the scene HTML.",
                params={"topic": str},
                timeout=600,
            ),
        ],
    ),
    # Mason — the real Composition Engineer (the stub slot was filled). His engine
    # turns the 5 upstream artifacts into deterministic per-scene HyperFrames projects,
    # runs the self-scan + lint/validate/inspect auto-gate before spending a render,
    # and emits composition_manifest.json as a plain dict; ATLAS stamps schema_version
    # + validates against the frozen contract at the boundary. He BUILDS to spec — he
    # never redesigns the storyboard (that's Iris). Same tool names / params / outputs
    # as the stub — only the adapter and the stub flag changed; no orchestrator edit.
    AgentEntry(
        name="composition_engineer",
        display="Mason",
        emoji="🛠️",
        blurb="Builds deterministic per-scene HyperFrames HTML, gates it (lint/validate/inspect), then renders.",
        project_dir=str(_ROOT / "composition-engineer"),
        adapter_cls=CompositionEngineerAdapter,
        role="Composition Engineer",
        jobs=[
            JobSpec(
                name="compose_scenes",
                tool="composition_engineer_compose_scenes",
                description="Build scene-NN/index.html per scene (lint+validate+inspect).",
                params={"topic": str},
            ),
            JobSpec(
                name="render_video",
                tool="composition_engineer_render_video",
                description="Render + concat the scenes into the final video.mp4.",
                params={"topic": str},
            ),
        ],
    ),
    # Vera — the Reference Analyst (a delegable job + persona, NOT a pipeline stage).
    # She DEFINES the standard: her engine measures one or more reference VIDEOS
    # (FFmpeg/OpenCV, offline + deterministic) into banded quality targets + a judged
    # style profile, and merges them into a durable named "standard" — feeding more
    # references tightens the bands toward their shared DNA. She never generates or
    # improves a video, and she is not the Coach/self-improvement loop. ATLAS stamps
    # schema_version + validates the rubric against the frozen reference_rubric contract
    # at the adapter boundary; Vera never imports atlas. Purely additive — no pipeline
    # STAGES / gate / contract / existing-agent edits, her tools just appear.
    AgentEntry(
        name="reference_analyst",
        display="Vera",
        emoji="🔬",
        blurb="Measures reference videos into a rubric (banded targets + style profile); turns taste into numbers, flags what's out of reach.",
        project_dir=str(_ROOT / "reference-analyst"),
        adapter_cls=ReferenceAnalystAdapter,
        role="Reference Analyst (standards)",
        jobs=[JobSpec(
            name="build_rubric",
            tool="reference_analyst_build_rubric",
            description=("Measure one or more REFERENCE videos into a rubric: banded "
                         "objective targets (pacing, motion, color, audio, structure) "
                         "plus a judged style profile, merged into a durable standard. "
                         "Pass 'videos' (a local path or a list/comma-separated list of "
                         "local paths) and an optional 'ceo_prefs' (JSON of taste "
                         "answers). Returns a targets digest + the open questions only "
                         "taste can answer. Feeding more references tightens the bands."),
            params={"videos": str, "ceo_prefs": str},
            # FFmpeg + OpenCV analysis of several videos, plus an optional vision pass.
            timeout=900,
        )],
    ),

    # ------------------------------------------------------------------
    # The two domain COACHES — the self-improvement loop's hands (Phase-2 step 3).
    # A coach is NOT a pipeline stage. Its job is to AUTHOR a soft-tier coaching
    # addendum that moves a quality metric into its rubric band; the CEO-owned
    # rubric decides the DIRECTION (the band decides; the coach proposes). The
    # improvement loop (atlas/eval/loop.py) routes a diagnosed shortfall to the
    # owning coach and persists the addendum through the GUARDED soft-tier write
    # path — so a coach can influence only soft-tier persona/prompt text, never the
    # rubric/contracts/spine. Purely additive: one registry entry + one adapter
    # each, no orchestrator edits — their tools just appear.
    # ------------------------------------------------------------------
    # Quill — the Editorial / Content coach (pre-production: research, script,
    # factcheck framing, asset relevance; mirrors rubric dimension G2).
    AgentEntry(
        name="editorial_coach",
        display="Quill",
        emoji="🖋️",
        blurb="Coaches the content side (research/script/relevance) to move an editorial metric into band — authors the soft-tier note; the rubric sets the target.",
        project_dir=str(_ROOT / "editorial-coach"),
        adapter_cls=EditorialCoachAdapter,
        role="Editorial / Content Coach",
        jobs=[JobSpec(
            name="propose_addendum",
            tool="editorial_coach_propose_addendum",
            description=("Author a soft-tier coaching addendum for a CONTENT specialist "
                         "(research/script/factcheck/assets) to move a named quality band "
                         "in the rubric-decided direction, without regressing siblings. "
                         "Pass band_id and direction; returns the addendum text."),
            params={"band_id": str, "direction": str},
            timeout=180,
        )],
    ),
    # Flux — the Production / Craft coach (production: style, storyboard, narration,
    # composition, audio mix; mirrors rubric dimensions G3/G5/G6).
    AgentEntry(
        name="production_coach",
        display="Flux",
        emoji="🎚️",
        blurb="Coaches the craft side (style/storyboard/audio/composition) to move a production metric into band — authors the soft-tier note; the rubric sets the target.",
        project_dir=str(_ROOT / "production-coach"),
        adapter_cls=ProductionCoachAdapter,
        role="Production / Craft Coach",
        jobs=[JobSpec(
            name="propose_addendum",
            tool="production_coach_propose_addendum",
            description=("Author a soft-tier coaching addendum for a CRAFT specialist "
                         "(style/storyboard/narration/compose/audiomix/render) to move a "
                         "named quality band in the rubric-decided direction, without "
                         "regressing siblings. Pass band_id and direction; returns the "
                         "addendum text."),
            params={"band_id": str, "direction": str},
            timeout=180,
        )],
    ),
]


def build_adapters() -> dict[str, object]:
    """Instantiate one adapter per registry entry. Engines load lazily on first use."""
    return {e.name: e.adapter_cls(e) for e in REGISTRY}


def get_entry(name: str) -> AgentEntry | None:
    """Resolve an agent by handle or display name (case-insensitive)."""
    key = (name or "").strip().lower()
    for e in REGISTRY:
        if key == e.name or key == e.display.lower():
            return e
    # tolerate "scout"/"sage" appearing inside a longer token like "scout,"
    for e in REGISTRY:
        if e.name in key or e.display.lower() in key:
            return e
    return None


def status_label(entry: AgentEntry) -> str:
    """How an agent reads on the call sheet: ready (real) vs a registered stub slot."""
    return "stub (slot reserved — specialist not built yet)" if entry.stub else "ready"


def roster() -> str:
    """A '/agents' call sheet: who's on the team, their role, status, and tools."""
    lines = []
    for e in REGISTRY:
        caps = [j.tool for j in e.jobs] + (["ask"] if e.persona else [])
        role = f" — {e.role}" if e.role else ""
        lines.append(f"{e.emoji} {e.display} ({e.name}){role}  ·  {status_label(e)}\n"
                     f"    {e.blurb}\n"
                     f"    [{', '.join(caps)}]")
    return "\n".join(lines)
