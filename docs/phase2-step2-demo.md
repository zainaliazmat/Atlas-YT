# Phase 2 — Step 2: hardened loop, a real accept (live demo)

- optimize project: `coffee-vs-tea-which-actually-gives-you-better-ener-20260622-080322-0e8b`
- target: `script:info_density` (claims/min, band [1.5, 4.0])
- baseline (existing script): **9.85** — far too dense
- held-out (never optimized against): gpt-4o comparison + jensen

## Noise floor (K stochastic Marlow generations, no addendum)

Measured K=5 on the same brief; the value is real, not synthetic:

- n=5 mean=5.842 **std=0.946** min=4.5 max=7.37
- values: [5.36, 5.81, 4.50, 6.18, 7.37] — Marlow's UNCOACHED output for this brief
  never lands in [1.5, 4.0]; run-to-run spread is large (σ≈0.95), which is exactly
  why the noise-floor gate matters.
- objective margin used (0.5σ): **0.473** → a candidate must land ≤ 3.527 to count
  (cross the band, not sit on its jittering edge).

### Path to this accept (honest record)

Three earlier live runs (~9 iterations) did NOT accept — each exposed a gate doing
its job, which drove a genuine coaching fix:

1. rule-only coach → in band (2.47) but **regressed `runtime_fit`** (fewer claims
   shortened the script) → regression gate rejected it.
2. centre-aiming coach → in band but **within the noise margin** (3.69–3.75) →
   noise gate rejected the edge landing.
3. preserving ALL siblings → backfired: telling the coach to also FIX an
   already-failing `scene_count` made it ADD scenes and INFLATE density.

The fix that produced this accept (both general `propose_fix` improvements, not
demo tweaks): preserve only the bands the baseline already PASSES (don't regress a
passing sibling; don't chase a failing one) **and** aim for the band CENTRE
(robust to the generator's variance).

## Hardened loop result

- **accepted: True**  ·  rubric_write_blocked: True

- iter 1: 9.85 → 7.2 · passes=False · beats_noise=False · regressions=[] · **final_accept=False**
- iter 2: 9.85 → 4.285714285714286 · passes=False · beats_noise=False · regressions=[] · **final_accept=False**
- iter 3: 9.85 → 2.7906976744186047 · passes=True · beats_noise=True · regressions=[] · held-out generalizes=True · spot_check=True · **final_accept=True**

## What this proves

- The loop can ACCEPT a real win (not only reject) — and only after it clears the band, beats the noise floor, generalizes to the held-out projects, and passes the CEO spot-check.
- The improver still **cannot write the rubric** (`rubric_write_blocked=True`).
- All rows recorded to the tracking store under run_id `step2-demo-1782168995` (auditable).

> Step 3 re-runs this same accept through the real **sibling coach (Quill)** instead
> of the in-loop inline LLM — see `docs/phase2-step3-demo.md`.
