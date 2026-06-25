"""The frozen artifact contracts — the schemas the whole pipeline depends on.

Every stage of the Showrunner's pipeline reads an upstream artifact and writes a
new one. This package is the ONE place those artifacts' shapes are pinned, so a
stage can never quietly hand the next stage a malformed file. The pipeline
validates each artifact here BEFORE advancing (see pipeline.py); a failed
validation blocks the stage, it does not crash the run.

CONTRACTS (artifact name -> schema file):
- project              project.json            (the master state)
- research_brief       research_brief.json     (Sage's pack shape, reused + envelope)
- creative_treatment   creative_treatment.json (the director's creative direction; runs
                       AFTER research, BEFORE script; Marlow + Iris both consume it;
                       advisory/optional so it's backward-compatible when absent)
- script               script.json
- factcheck_report     factcheck_report.json   (the fact-check gate reads its verdict)
- style_guide          style_guide.json        (additively extensible via schema_version)
- storyboard           storyboard.json         (additively extensible via schema_version)
- asset_manifest       asset_manifest.json
- narration_transcript narration.transcript.json
- audio_manifest       audio_manifest.json
- composition_manifest composition_manifest.json (the Composition Engineer's per-scene
                       build + auto-gate record; additive, additionalProperties:true)
- reference_rubric     reference_rubric.json   (the Reference Analyst's banded quality
                       targets + style profile — a STANDARD, not a pipeline artifact;
                       additive, additionalProperties:true)

Frozen-but-extensible: every schema sets additionalProperties:true, and the
current contract version each real producer emits is in CONTRACT_VERSION. The
Art Director and Composition Engineer (both built) ADD fields under a bumped
schema_version (render fps, texture/overlay set, per-scene effects array); older
readers keep working because unknown keys are allowed.
"""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache

from jsonschema import Draft202012Validator

_DIR = pathlib.Path(__file__).parent

# The version each stub stamps onto the artifacts it writes. Bump per-contract as
# the real specialists extend a schema (additively).
CONTRACT_VERSION = "1.0"

# Per-contract overrides for schemas a REAL specialist has additively extended. A
# contract absent here stays on CONTRACT_VERSION. The Art Director (Iris) bumped
# style_guide + storyboard to "1.1" when she added render-detail fields (fps,
# textures, per-scene effects). The Audio / Sound Designer (Cadence) bumped
# audio_manifest to "1.1" when she added per-track clearance fields (license /
# attribution / status), the signature-SFX scene anchor, and master_uri / vo_uri.
# IMPORTANT: this is for a specialist's REAL output — the offline stub producers keep
# stamping CONTRACT_VERSION ("1.0") because they do NOT emit the 1.1 fields, so
# version_for() would mislabel them. The new fields are OPTIONAL in the schema
# precisely because validate() is NOT version-aware (it always loads the latest
# schema file), so a 1.0 stub artifact still validates against the 1.1 schema.
CONTRACT_VERSIONS: dict[str, str] = {
    "style_guide": "1.1",
    "storyboard": "1.1",
    "audio_manifest": "1.1",
    # Magpie bumped asset_manifest to "1.1" when she added the "diagram" asset type +
    # the optional `plan` object (a cached DiagramPlan Mason composes to SVG at render).
    "asset_manifest": "1.1",
}


def version_for(name: str) -> str:
    """The schema_version a real specialist stamps onto contract `name`.

    Falls back to CONTRACT_VERSION for any contract that hasn't been bumped. (The
    schemas only require schema_version to be a string — the value is documentary —
    so this never affects validation; it keeps emitted artifacts honestly labelled.)
    """
    return CONTRACT_VERSIONS.get(name, CONTRACT_VERSION)

# artifact name -> schema file in this directory
SCHEMA_FILES: dict[str, str] = {
    "project": "project.schema.json",
    "research_brief": "research_brief.schema.json",
    "creative_treatment": "creative_treatment.schema.json",
    "script": "script.schema.json",
    "factcheck_report": "factcheck_report.schema.json",
    "style_guide": "style_guide.schema.json",
    "storyboard": "storyboard.schema.json",
    "asset_manifest": "asset_manifest.schema.json",
    "narration_transcript": "narration_transcript.schema.json",
    "audio_manifest": "audio_manifest.schema.json",
    "composition_manifest": "composition_manifest.schema.json",
    "reference_rubric": "reference_rubric.schema.json",
}


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    try:
        fname = SCHEMA_FILES[name]
    except KeyError:
        raise KeyError(f"No frozen contract named {name!r}. "
                       f"Known: {', '.join(sorted(SCHEMA_FILES))}.") from None
    schema = json.loads((_DIR / fname).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(name: str, obj: object) -> tuple[bool, list[str]]:
    """Validate `obj` against the frozen contract `name`.

    Returns (ok, errors). `errors` is a list of human-readable messages (empty
    when ok). Never raises on a bad artifact — the pipeline turns a failure into a
    blocked stage, not a crash. (Raises only for an unknown contract name, which is
    a programming error.)
    """
    errors = sorted(_validator(name).iter_errors(obj), key=lambda e: list(e.path))
    if not errors:
        return True, []
    msgs = []
    for e in errors:
        loc = "/".join(str(p) for p in e.path) or "(root)"
        msgs.append(f"{loc}: {e.message}")
    return False, msgs


def known_contracts() -> list[str]:
    return sorted(SCHEMA_FILES)
