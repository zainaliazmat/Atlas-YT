"""studio — the v2 production path for the video agency.

This package is a clean-room rebuild that sits ALONGSIDE ``atlas/`` (which is
left untouched as the v1 path). The design bar it targets is captured in
``studio/GOLDEN_REFERENCE.md`` — one self-contained, deterministic, seekable
GSAP composition, authored against real content and *looked at* frame by frame.

Why a second path instead of editing atlas/:
  - atlas/ encodes a spec-passing pipeline (treatment → narrative_intent →
    motion_mood_board → style → storyboard → enum-driven Mason render). Those
    closed-vocab contracts and the persona roundtable/coaches are the very
    anti-patterns ``GOLDEN_REFERENCE.md`` argues against. They are RETIRED for
    the production path and remain only in atlas/.
  - studio/ keeps the parts of atlas/ that are genuinely good engineering —
    Sage research+factcheck, the un-approvable factcheck gate, the HyperFrames
    CLI wrappers, the Kokoro VO flow, the video analyzers and pairwise judge —
    by WRAPPING them, not forking them. See ``studio/REUSE_MAP.md``.

Build phases (each module names the phase that fills it in):
  Phase 1 — packs/    Design Pack loader + registry
  Phase 2 — library/  Asset Library resolver + manifest
  Phase 3 — compose/  the Composer (authors one index.html)
  Phase 4 — vo/       Kokoro VO + the VO-lock re-timer wiring
  Phase 5 — review/   the vision revise loop (analyzers + judge, in-loop)

Nothing here has real logic yet — this is the skeleton (docstrings + TODOs).
``python -c "import studio"`` must stay clean (stdlib-only imports at module
scope; heavy/sibling imports are deferred into functions).
"""

__version__ = "0.0.0"

# Pin the HyperFrames toolchain for the v2 path here so every wrapper agrees.
# NOTE: atlas/ shells out to hyperframes@0.6.115; studio/ moves to 0.7.10.
HYPERFRAMES_VERSION = "0.7.10"

__all__ = ["__version__", "HYPERFRAMES_VERSION"]

# TODO(phase-0): once config.py lands, optionally re-export StudioConfig here
# for a one-import ergonomics (`from studio import StudioConfig`).
