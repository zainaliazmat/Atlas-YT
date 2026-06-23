"""One minimal, bounded improvement loop — the capstone.

    Inspect -> Diagnose -> Propose (a soft-tier prompt tweak) -> Re-measure
    -> Accept/Reject (revert on reject) -> record to the tracking store.

This is the worked example "train the manager," made safe:
  * "Train him"        = evolve the TEXT he runs on (a persona/playbook addendum
                         injected through the engine's chat seam), never his code.
  * "He delivers"      = the target property moves INTO its rubric band with no
                         regression on the artifact's other properties.
  * "Loop until fixed" = iterate, hard-capped by max_iters (cost discipline).
  * The lock           = the band and the success bar are the CEO-owned rubric;
                         the loop imports it READ-ONLY. There is NO rubric write
                         path, and apply_soft_change() PHYSICALLY refuses to
                         write the rubric, the contracts, or the spine.

The improver is strictly LESS privileged than the guarantees: it may write only
soft-tier persona/prompt/playbook files, and never its own success criterion.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import rubric
from eval import rollup
from eval.types import EvalContext, Measurement
from eval.analyzers import text as text_an

_ATLAS_DIR = Path(__file__).resolve().parents[1]      # .../atlas
_REPO_DIR = _ATLAS_DIR.parent                          # repo root

# ---------------------------------------------------------------------------
# The write boundary: the privilege asymmetry made physical.
# ---------------------------------------------------------------------------


class WriteBoundaryError(PermissionError):
    """Raised when the improver tries to write outside the soft tier."""


# Absolutely forbidden — the success criterion, the contracts, and the spine.
_DENIED_ROOTS = [
    _ATLAS_DIR / "rubric",
    _ATLAS_DIR / "contracts",
]
_DENIED_FILES = [
    _ATLAS_DIR / "pipeline.py",
    _ATLAS_DIR / "registry.py",
    _ATLAS_DIR / "adapters" / "loader.py",
]

# Soft-tier: persona / voice / playbook / prompt text an agent "runs on".
_SOFT_TOKENS = ("SOUL", "STYLE", "SKILL", "PERSONA", "PLAYBOOK", "PROMPT",
                "COACH", "ADDENDUM")


def _is_denied(p: Path) -> bool:
    rp = p.resolve()
    for d in _DENIED_ROOTS:
        if d == rp or d in rp.parents:
            return True
    for f in _DENIED_FILES:
        if f == rp:
            return True
    return False


def _is_soft_tier(p: Path) -> bool:
    rp = p.resolve()
    if rp.suffix.lower() != ".md":
        return False
    stem = rp.stem.upper()
    if any(tok in stem for tok in _SOFT_TOKENS):
        return True
    # any markdown inside a persona "soul/" directory is soft-tier
    return any(part.lower() == "soul" for part in rp.parts)


def apply_soft_change(path: str | Path, content: str) -> Path:
    """Write `content` to `path` — ONLY if it is a soft-tier file and is not the
    rubric / contracts / spine. Otherwise raise WriteBoundaryError WITHOUT
    writing anything. This is the structural guarantee that an improver can never
    'improve' itself by editing its own success bar."""
    p = Path(path)
    if _is_denied(p):
        raise WriteBoundaryError(
            f"refused: {p} is the rubric/contracts/spine — read-only to the improver")
    if not _is_soft_tier(p):
        raise WriteBoundaryError(
            f"refused: {p} is not a soft-tier persona/prompt/playbook file")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p.resolve()


def can_write_rubric() -> bool:
    """Self-check used in tests/reports: there is no code path that writes the
    rubric. (a) the rubric module exposes no writer; (b) apply_soft_change
    refuses every rubric path."""
    has_writer = any(hasattr(rubric, n) for n in ("save", "write", "dump", "store", "set_band"))
    refuses = False
    try:
        apply_soft_change(rubric.__path__[0] + "/rubric.json", "{}")
    except WriteBoundaryError:
        refuses = True
    except Exception:
        refuses = False
    return (not has_writer) and refuses


# ---------------------------------------------------------------------------
# Coach proposal (soft-tier prompt addendum).
# ---------------------------------------------------------------------------

def _sibling_preserve_clause(target: dict, preserve_bands: Optional[set] = None) -> str:
    """A short clause naming the OTHER gated bands of the target's stage and their
    ranges, so the coach lowers/raises the target WITHOUT breaking a sibling.
    Reads the rubric READ-ONLY (it never decides; it only surfaces the bands).

    `preserve_bands`, when given, restricts the clause to bands the baseline
    CURRENTLY PASSES — the coach must not REGRESS a passing sibling, but it must
    NOT be told to also FIX already-failing ones (that scope-creep backfires:
    e.g. "add scenes to fix scene_count" inflates the very density we're lowering)."""
    stage = target.get("stage")
    band_id = target.get("band_id")
    if not stage:
        return ""
    parts = []
    try:
        for bid, b in rubric.bands().items():
            if not bid.startswith(f"{stage}:") or bid == band_id:
                continue
            if preserve_bands is not None and bid not in preserve_bands:
                continue
            comp = b.get("comparator")
            if comp == "range":
                parts.append(f"{bid} in [{b['min']},{b['max']}]")
            elif comp == "gte":
                parts.append(f"{bid} >= {b['min']}")
            elif comp == "lte":
                parts.append(f"{bid} <= {b['max']}")
            # skip eq/eq_true/info (structural booleans the coach can't usefully chase)
    except Exception:
        return ""
    if not parts:
        return ""
    return " Keep these in range while you do: " + "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Coach routing (Phase-2 step 3): a diagnosed shortfall belongs to ONE owning
# stage; map that stage to the domain coach who tunes it. The content/craft split
# mirrors the rubric's own division (and the diagnostician already refuses to
# hand the loop a contested multi-stage target — see diagnose.pick_primary_target
# — so a single primary target maps cleanly to a single coach: no two coaches
# optimize a contested dimension blind).
# ---------------------------------------------------------------------------
EDITORIAL_STAGES = {"research", "script", "factcheck", "assets"}
PRODUCTION_STAGES = {"style", "storyboard", "narration", "compose", "audiomix", "render"}


def coach_for_stage(stage: str) -> Optional[str]:
    """Registry handle of the coach who owns `stage`, or None if unmapped."""
    if stage in EDITORIAL_STAGES:
        return "editorial_coach"
    if stage in PRODUCTION_STAGES:
        return "production_coach"
    return None


def delegate_to_coach(target: dict, direction: str, preserve: str,
                      *, research: bool = False) -> Optional[dict]:
    """Route to the owning domain coach via its registry adapter and return its
    proposed addendum dict, or None if no coach is available / the call fails
    (the caller then falls back to the deterministic rule addendum).

    This is the real Phase-2 split: instead of the in-loop placeholder authoring
    the note, the owning SIBLING coach (Quill / Flux) authors it through its own
    brain + persona. The rubric still decided `direction`; the coach only writes
    the persuasive text. loop.py imports registry lazily so eval has no hard
    dependency on the orchestration layer at import time."""
    stage = target.get("stage", "")
    name = coach_for_stage(stage)
    if name is None:
        return None
    try:
        import registry
        adapter = registry.build_adapters().get(name)
        if adapter is None:
            return None
        res = adapter.run_job("propose_addendum", None,
                              band_id=target["band_id"], direction=direction,
                              preserve=preserve, measured_value=target.get("measured_value"),
                              owner=target.get("owner", ""), research=research)
        if res.get("ok") and (res.get("text") or "").strip():
            return {"addendum": res["text"], "coach": name,
                    "source": res.get("source"), "research": res.get("research")}
    except Exception:
        return None
    return None


def propose_fix(target: dict, *, coach_chat_fn: Optional[Callable] = None,
                preserve_bands: Optional[set] = None,
                coach_fn: Optional[Callable] = None,
                use_coaches: bool = False, research: bool = False) -> dict:
    """Propose a soft-tier prompt addendum that should move `target` into band.

    Authoring priority (the band/direction always comes from the rubric, never the
    coach — research widens hypotheses, the rubric decides):
      1. `coach_fn(payload)->str` — an injected coach seam (tests / explicit wiring);
      2. `use_coaches=True` — DELEGATE to the owning sibling coach (Quill/Flux) via
         its adapter (the Phase-2 split — the real coaching path);
      3. `coach_chat_fn` — the legacy in-loop LLM authoring (kept for back-compat);
      4. the deterministic rule addendum (offline-safe default)."""
    band_id = target["band_id"]
    comparator = target["comparator"]
    val = target["measured_value"]
    lo, hi, tgt = target["band_min"], target["band_max"], target["band_target"]

    if comparator == "range":
        # Aim for the band CENTRE, not its edge — a value parked at the edge isn't
        # robust to the generator's run-to-run variance (the noise-floor gate
        # rightly rejects an edge landing). Coaching toward the centre clears the
        # margin honestly.
        centre = round((lo + hi) / 2.0, 2) if (lo is not None and hi is not None) else None
        if val is not None and hi is not None and val > hi:
            direction = (f"LOWER it to about {centre} — comfortably inside [{lo}, {hi}], "
                         f"NOT just under {hi} (currently {val})")
        elif val is not None and lo is not None and val < lo:
            direction = (f"RAISE it to about {centre} — comfortably inside [{lo}, {hi}], "
                         f"NOT just over {lo} (currently {val})")
        else:
            direction = f"bring it comfortably toward the centre {centre} of [{lo}, {hi}]"
    elif comparator == "gte":
        direction = f"RAISE it to >= {lo} (currently {val})"
    elif comparator == "lte":
        direction = f"LOWER it to <= {hi} (currently {val})"
    elif comparator == "eq":
        direction = f"make it exactly {tgt} (currently {val})"
    else:
        direction = f"satisfy the rubric band (currently {val})"

    # The fix is coupled to the target's SIBLING properties (e.g. lowering
    # claims-per-minute can shorten the script and break runtime_fit). Tell the
    # agent which same-stage bands to KEEP in range so it doesn't trade one
    # failure for another — credit-assignment-aware coaching.
    preserve = _sibling_preserve_clause(target, preserve_bands=preserve_bands)

    rule_addendum = (
        f"## Coach note (eval-driven, target {band_id})\n"
        f"Your last output missed the quality band for **{band_id}**: {direction}. "
        f"Adjust your approach to land inside the band on the next draft, without "
        f"sacrificing the other quality properties.{preserve}\n"
    )

    addendum = rule_addendum
    coach_name = None
    coach_source = "rule"

    # 1. injected coach seam (tests / explicit wiring) — payload in, addendum out
    if coach_fn is not None:
        try:
            txt = coach_fn({"band_id": band_id, "direction": direction,
                            "preserve": preserve, "measured_value": val,
                            "owner": target.get("owner", "")})
            if isinstance(txt, str) and txt.strip():
                addendum = (f"## Coach note (eval-driven, target {band_id})\n{txt.strip()}\n")
                coach_source = "coach_fn"
        except Exception:
            pass  # graceful: fall back to the rule
    # 2. delegate to the owning SIBLING coach (the Phase-2 split); research=True
    #    lets the coach study current best practice (bounded) — the eval still prunes.
    elif use_coaches:
        res = delegate_to_coach(target, direction, preserve, research=research)
        if res is not None:
            addendum = res["addendum"]
            coach_name = res.get("coach")
            coach_source = res.get("source") or "coach"
    # 3. legacy in-loop LLM authoring
    elif coach_chat_fn is not None:
        try:
            sys_p = ("You are a domain coach improving a specialist agent's persona. "
                     "Write 2-4 crisp imperative sentences (markdown) that will move the "
                     "named metric into its band on the next draft WITHOUT pushing any "
                     "other named band out of range. Respect the bands; do not invent new goals.")
            usr_p = (f"Metric: {band_id}\nNeeded change: {direction}\n"
                     f"Keep these sibling properties in range too:{preserve or ' (none)'}\n"
                     f"Write the coaching addendum only.")
            llm_text = coach_chat_fn(sys_p, usr_p)
            if isinstance(llm_text, str) and llm_text.strip():
                addendum = (f"## Coach note (eval-driven, target {band_id})\n"
                            f"{llm_text.strip()}\n")
                coach_source = "inline_llm"
        except Exception:
            pass  # graceful: fall back to the rule

    return {"band_id": band_id, "direction": direction, "addendum": addendum,
            "coach": coach_name, "coach_source": coach_source,
            "soft_path": _soft_path_for(target)}


def _soft_path_for(target: dict) -> str:
    """Where the accepted addendum persists: the OWNING specialist's persona dir
    (e.g. a script target -> scriptwriter/COACH_ADDENDUM.md). Falls back to the
    scriptwriter path (the affordable, render-free demo target)."""
    owner_dirs = {
        "script": "scriptwriter", "research": "topic-researcher",
        "factcheck": "topic-researcher", "assets": "asset-sourcer",
        "style": "art-director", "storyboard": "art-director",
        "narration": "audio-designer", "audiomix": "audio-designer",
        "compose": "composition-engineer", "render": "composition-engineer",
    }
    sub = owner_dirs.get(target.get("stage", ""), "scriptwriter")
    return str(_REPO_DIR / sub / "COACH_ADDENDUM.md")


# ---------------------------------------------------------------------------
# Re-measure for the SCRIPT stage (the affordable, real, render-free target).
# ---------------------------------------------------------------------------

def make_script_remeasure(brief: dict, *, base_chat_fn: Optional[Callable] = None,
                          workdir: Optional[Path] = None) -> Callable[[str], list[Measurement]]:
    """Build a remeasure_fn(addendum) that RE-RUNS Marlow's real engine with the
    soft addendum injected into the system prompt via the chat seam, writes the
    fresh script to a temp dir, and returns the text-analyzer measurements.

    The addendum is injected through the engine's injectable `chat_fn` — i.e. we
    change the TEXT the agent runs on, never the engine code."""
    import sys
    sw = str(_REPO_DIR / "scriptwriter")
    if sw not in sys.path:
        sys.path.insert(0, sw)

    workdir = workdir or (_ATLAS_DIR / "projects" / "_eval_loop_tmp")

    def remeasure(addendum: str) -> list[Measurement]:
        import importlib
        script_engine = importlib.import_module("script_engine")
        base = base_chat_fn or script_engine.llm.chat

        def wrapped(system: str, user: str) -> str:
            # the soft-tier persona tweak prepended to the system prompt
            return base(f"{system}\n\n{addendum}", user)

        new_script = script_engine.write_script(brief, chat_fn=wrapped)
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "script.json").write_text(json.dumps(new_script))
        ctx = EvalContext(workdir)
        # only the script-stage measurements are relevant to this target
        return [m for m in text_an.analyze(ctx) if m.stage == "script"]

    return remeasure


# ---------------------------------------------------------------------------
# Decide + run.
# ---------------------------------------------------------------------------

def _rows_by_band(measurements: list[Measurement]) -> dict[str, dict]:
    return {f"{m.stage}:{m.prop}": rollup.measurement_to_row(m) for m in measurements}


def _inside_with_margin(value, row: dict, margin: float) -> bool:
    """Is `value` inside the band by at least `margin` (an objective change must
    CROSS the band, not sit on its edge where measurement jitter could flip it)?"""
    if value is None:
        return False
    comp = row.get("comparator")
    lo, hi, tgt = row.get("band_min"), row.get("band_max"), row.get("band_target")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if comp == "range" and lo is not None and hi is not None:
        return (lo + margin) <= v <= (hi - margin)
    if comp == "gte" and lo is not None:
        return v >= (lo + margin)
    if comp == "lte" and hi is not None:
        return v <= (hi - margin)
    # eq / eq_true / info: margin is meaningless — fall back to the gate's verdict
    return row.get("passed") is True


def decide(baseline: list[Measurement], candidate: list[Measurement],
           target_band_id: str, *, noise_floor: Optional[dict] = None,
           sigma: float = 2.0, objective_margin: float = 0.0) -> dict:
    """Accept iff the target property now PASSES, no previously-passing property
    of the same artifact regressed, AND the move BEATS THE NOISE FLOOR.

    The noise-floor gate is what separates a real win from LLM/measurement jitter
    (the single most important number in the program — see the measured σ):
      * JUDGED target  → the change must move the metric by > `sigma`·σ, where σ
        comes from `noise_floor['std']` (run the held-out set K≥5× to measure it).
      * OBJECTIVE target → the value must land INSIDE the band by `objective_margin`
        (cross it, don't sit on the edge).
    With noise_floor=None and objective_margin=0.0 this reduces to the Phase-1
    behavior (pass + no-regression), so existing callers are unaffected.
    """
    base = _rows_by_band(baseline)
    cand = _rows_by_band(candidate)

    target_before = base.get(target_band_id, {})
    target_after = cand.get(target_band_id, {})
    target_passes = target_after.get("passed") is True
    kind = target_after.get("kind") or target_before.get("kind")

    regressions = []
    for bid, brow in base.items():
        if brow.get("passed") is True and cand.get(bid, {}).get("passed") is False:
            regressions.append(bid)

    # --- noise-floor / margin gate ---------------------------------------
    before_v = target_before.get("measured_value")
    after_v = target_after.get("measured_value")
    beats_noise = True
    noise_note = "no noise gate applied"
    if kind == "judged" and noise_floor is not None:
        std = float(noise_floor.get("std", 0.0) or 0.0)
        threshold = sigma * std
        try:
            delta = abs(float(after_v) - float(before_v))
        except (TypeError, ValueError):
            delta = 0.0
        beats_noise = delta > threshold
        noise_note = (f"judged Δ={round(delta,4)} vs {sigma}σ={round(threshold,4)} "
                      f"(σ={round(std,4)}) → {'beats' if beats_noise else 'within'} noise")
    elif kind == "objective" and objective_margin > 0:
        beats_noise = _inside_with_margin(after_v, target_after, objective_margin)
        noise_note = (f"objective value={after_v} inside band by margin "
                      f"{objective_margin} → {'yes' if beats_noise else 'no'}")

    accept = bool(target_passes and not regressions and beats_noise)
    if accept:
        reason = "target moved into band, no regressions, beats noise floor"
    elif not target_passes:
        reason = "target still out of band"
    elif regressions:
        reason = f"regressions: {regressions}"
    else:
        reason = f"within noise floor: {noise_note}"
    return {
        "target_band_id": target_band_id,
        "target_before": before_v,
        "target_after": after_v,
        "target_passes_now": target_passes,
        "regressions": regressions,
        "beats_noise_floor": beats_noise,
        "noise_note": noise_note,
        "accept": accept,
        "reason": reason,
    }


def run_loop(*, baseline_measurements: list[Measurement], target: dict,
             remeasure_fn: Callable[[str], list[Measurement]],
             coach_chat_fn: Optional[Callable] = None,
             max_iters: int = 1, store=None, run_id: str = "loop",
             write_soft: bool = True, soft_path: Optional[str] = None,
             noise_floor: Optional[dict] = None, sigma: float = 2.0,
             objective_margin: float = 0.0,
             verify_fn: Optional[Callable[[str], dict]] = None,
             spot_check_fn: Optional[Callable[[dict, dict], bool]] = None,
             use_coaches: bool = False,
             coach_fn: Optional[Callable] = None,
             research: bool = False) -> dict:
    """Run the bounded improvement loop for ONE target, HARDENED (Phase 2 step 2).

    `remeasure_fn(addendum) -> list[Measurement]` re-runs the owning engine with
    the soft addendum on the OPTIMIZE input and returns fresh measurements.

    A tentatively-accepted change must clear THREE further gates before it sticks:
      * the NOISE-FLOOR gate inside decide() (judged > sigma·σ; objective inside
        band by `objective_margin`) — separates a real win from jitter;
      * the held-out VERIFIER `verify_fn(addendum) -> {generalizes: bool, ...}` —
        the change must not regress projects the loop never optimized against
        (no overfitting);
      * the human SPOT-CHECK `spot_check_fn(proposal, verdict) -> bool` — a final
        CEO sign-off before the soft change persists (the gate-style approval).
    Any gate failing ⇒ reject + revert the soft file. All three are optional so
    offline tests can exercise them piecemeal.
    """
    # bands the BASELINE already passes — the coach must not regress these, but is
    # NOT asked to fix already-failing siblings (that scope-creep backfires).
    _base_rows = _rows_by_band(baseline_measurements)
    preserve_bands = {bid for bid, r in _base_rows.items() if r.get("passed") is True}

    history = []
    accepted = None
    for i in range(max_iters):
        proposal = propose_fix(target, coach_chat_fn=coach_chat_fn,
                               preserve_bands=preserve_bands,
                               use_coaches=use_coaches, coach_fn=coach_fn,
                               research=research)
        if soft_path:
            proposal["soft_path"] = soft_path
        soft_written = None
        if write_soft:
            # write the persona/playbook addendum through the GUARDED path —
            # this both persists the change and proves the boundary holds.
            soft_written = str(apply_soft_change(proposal["soft_path"], proposal["addendum"]))
        candidate = remeasure_fn(proposal["addendum"])
        verdict = decide(baseline_measurements, candidate, target["band_id"],
                         noise_floor=noise_floor, sigma=sigma,
                         objective_margin=objective_margin)
        change_id = f"{target['stage']}-coach-iter{i+1}"
        if store is not None:
            cand_rows = [rollup.measurement_to_row(m) for m in candidate]
            store.record_run(cand_rows, run_id=run_id, change_id=change_id)

        # A tentative accept still has to GENERALIZE and pass the human spot-check.
        verification = None
        spot_check = None
        final_accept = verdict["accept"]
        if final_accept and verify_fn is not None:
            try:
                verification = verify_fn(proposal["addendum"])
            except Exception as e:  # a failed verifier never auto-accepts
                verification = {"generalizes": False, "error": f"{type(e).__name__}: {e}"}
            if not verification.get("generalizes", False):
                final_accept = False
        if final_accept and spot_check_fn is not None:
            try:
                spot_check = bool(spot_check_fn(proposal, verdict))
            except Exception:
                spot_check = False
            if not spot_check:
                final_accept = False

        history.append({"iter": i + 1, "change_id": change_id,
                        "soft_written": soft_written, "proposal": proposal["direction"],
                        "coach": proposal.get("coach"), "coach_source": proposal.get("coach_source"),
                        "verdict": verdict, "verification": verification,
                        "spot_check": spot_check, "final_accept": final_accept})
        if final_accept:
            accepted = history[-1]
            break
        # reject (target/noise/held-out/spot-check) -> revert the soft addendum so
        # the persona is unchanged (cost-disciplined, reversible).
        if soft_written:
            try:
                Path(soft_written).unlink()
            except OSError:
                pass

    return {
        "target": target,
        "iterations": history,
        "accepted": accepted is not None,
        "accepted_iteration": accepted,
        "rubric_write_blocked": can_write_rubric(),
    }
