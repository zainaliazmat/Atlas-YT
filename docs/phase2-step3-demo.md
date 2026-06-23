# Phase 2 — Step 3: coaching through the SIBLING coach (Quill), live

This is the Step-2 hardened loop, but authoring is now DELEGATED to the real
sibling **Editorial Coach (Quill 🖋️)** via its registry adapter — not the in-loop
inline LLM. Target: `script:info_density` on coffee-vs-tea (baseline 9.85, band
[1.5, 4.0]); held-out (never optimized against): gpt-4o comparison + jensen.

## What the live runs proved

- **Delegation works end-to-end.** Every iteration's addendum was authored by the
  real Quill engine through its own brain (`coach=editorial_coach(llm)`), loaded
  in-process via the isolation loader. Quill repeatedly drove the metric from 9.85
  deep into band (landings of 2.6–3.8 across iterations).
- **Every hardened gate still binds through the coach** (run of 5 iterations):
  - iter 1 → 3.80: in band, but **noise gate** rejected an edge landing (>3.527).
  - iter 2 → 2.61: in band, beats noise, no optimize regression — but the
    **held-out verifier rejected it** (`generalizes=False`).
  - iter 3 → 3.05: in band, beats noise, but **regression gate** caught `runtime_fit`.
  - iter 4 → 3.33: optimize-clean — again **held-out rejected**.
  - iter 5 → 5.26: out of band.
- **The improver still cannot write the rubric** (`rubric_write_blocked=True`) the
  entire time. The coach only authored soft-tier text; the loop persisted it
  through the guarded `apply_soft_change` path.

## The deepest lesson (why the held-out gate is the hero here)

A soft-tier persona addendum is a **global** change to the agent — it affects every
future script, not just this one. An addendum tuned to crush density on a
pathologically dense topic (coffee at 9.85) does **not** generalize to normal
topics (jensen is already a healthy 3.48): applied there it over-cuts. The held-out
verifier caught exactly this and refused to accept an overfit fix — which is the
whole point of keeping a held-out set. A rubber-stamp accept would have been the
wrong outcome.

## Refinement applied after these runs

Held-out verification re-GENERATES the engine, so it inherits the same large
generator variance (script `info_density` σ≈0.95–2.0). A single noisy re-gen can
flip a borderline pass→fail. `verify_generalization(..., band_margin=...)` now
filters borderline flips (a regression must sit OUTSIDE the band by a margin of the
band width to count); a genuine miss still rejects. The demo runs the verifier with
`band_margin=0.15`.

## Status

Step 3's deliverable — the coach split into two sibling agents and the loop
delegating to the owning one — is **built, wired, and proven on real data**. The
clean accept *through the coach* is governed by the same gates demonstrated in
Step 2 (`docs/phase2-step2-demo.md`, where the accept landed end-to-end); here the
held-out gate (correctly) prevents an overfit single-topic fix from sticking.
