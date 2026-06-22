# OWNER_RUN/PLAN.md — living plan & decisions log

Acting owner run of YT-AGENTS. Main thread = orchestrator; bounded missions delegated to subagents that report digests. Updated as work proceeds.

## Phase checklist

- [~] **§1 ORIENT** — system map + environment (IN PROGRESS)
  - [x] Read PROJECT_CONTEXT.md
  - [x] Environment verified → `ENVIRONMENT.md`
  - [ ] System map → `SYSTEM_MAP.md` (code-walk subagent running)
- [ ] **§2 AUDIT** — scorecard + bug inventory + backlog → `AUDIT_REPORT.md`
- [ ] **§3 LIVE QA** — real end-to-end run, gate exercises, fleet interrogation → `QA_LOG.md`
- [ ] **§4 FIX** — bugs w/ tests, Issue #2 reconciliation, doc reconcile, model IDs, TTS parallelism → `FIXES.md`
- [ ] **§5 CREATIVE R&D** — Vox-craft research, vocabulary extension in lockstep, teach fleet → `CREATIVE_UPGRADES.md`
- [ ] **§6 PRODUCE 5** — escalating ladder, watch every stage, critique → `PRODUCTIONS.md`
- [ ] **§7 FINAL** — before/after scorecard, commit list → `FINAL_REPORT.md`

## Key decisions log

- **2026-06-22 — Node:** system Node is v18 (too old, HyperFrames crashes). nvm has v22.18.0. All render-producing runs MUST `nvm use 22` first. Render is therefore POSSIBLE, contrary to first fear.
- **2026-06-22 — Branch:** only branch is `main` (brief expected `master`). Will work on `main`, branch before substantive commits, never touch `origin/main` without asking.
- **2026-06-22 — Scope discovery:** a SECOND uncommitted body of work exists — a new "Reference Analyst" 8th agent. Brief only mentioned Issue #2. Must audit both; decide whether Reference Analyst is finished, half-done, or should be parked.

## Open questions / forks (escalate only if expensive+irreversible)

1. The Reference Analyst agent is undocumented in the brief. Is it meant to ship, or be reverted? → Assess in §2; lean toward finishing-or-parking cleanly, not deleting work, but flag to CEO before committing it.
2. None render-blocking remain given nvm Node 22.

## Subagent roster status

- code-walk / SYSTEM_MAP — dispatched
- Auditor(s) — pending §2
- Pipeline-QA — pending §3
- Fixer(s), Creative-Researcher, Vocabulary-Extender, Producer, Doc-Reconciler — later phases
