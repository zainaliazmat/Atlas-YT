"""The CEO work cycle — Atlas's one-action-at-a-time operating loop.

ONE cycle = review state → choose the SINGLE highest-leverage next action →
execute it through the existing tools/playbook → update state + journal → surface
a digest line + any ask. The choosing is a deterministic priority ladder
(choose_action) so the spine is predictable, testable, and safe; the heavy
creative execution (running the full production playbook) delegates to the
orchestrator LLM, which is injected as `orch` so the spine runs offline in tests.

Three guardrails, all structural:
  * the kill-switch — a ceo/STOP file halts a cycle before it acts;
  * the budget cap — run_cycles stops before a cycle that would exceed it;
  * never stall — every action does real work AND/OR files a concrete ask; if the
    board can't provide something, the next cycle finds a legal alternative.
"""
from __future__ import annotations

import boundary
from ceo import state as ceo_state

# Trigger phrases that route a chat turn into a CEO cycle.
ADVANCE_TRIGGERS = ("advance the business", "advance business", "run a business cycle",
                    "run the business")

# A rough per-action cost estimate (USD) for the budget cap. Production is the
# expensive one (the full multi-agent render); planning/analysis are cheap.
ACTION_COST_USD = {
    "produce_video": 0.50, "continue_production": 0.40, "research_niche": 0.10,
    "analyze_performance": 0.05, "publish_video": 0.05, "improve_agent": 0.05,
    "propose_agent": 0.05, "set_direction": 0.01, "review_strategy": 0.01,
}


def is_advance_command(msg: str) -> bool:
    """True if a chat message is the 'advance the business' trigger."""
    return (msg or "").strip().lower() in ADVANCE_TRIGGERS or \
        (msg or "").strip().lower().startswith("advance the business")


# ----------------------------------------------------------------------
# Choose: the single highest-leverage next action (pure + deterministic).
# ----------------------------------------------------------------------
def _action(kind: str, rationale: str, target=None) -> dict:
    return {"kind": kind, "rationale": rationale, "target": target}


def choose_action(st: dict) -> dict:
    """Pick ONE action. The ladder is leverage-ordered: direction before topics,
    measuring what shipped before shipping more, shipping before researching, and
    never two videos in flight at once (focus over volume)."""
    niches = st.get("niches") or []
    backlog = st.get("backlog") or []
    videos = st.get("videos") or []

    if not niches:
        return _action("set_direction",
                       "No niche chosen — the studio has no direction to optimize.")

    # Measuring a shipped/produced-but-unevaluated video is the highest leverage:
    # strategy must adapt from analytics, not vibes.
    produced = [v for v in videos
                if v.get("status") in ("produced", "rendered", "published")
                and not v.get("evaluated")]
    if produced:
        return _action("analyze_performance",
                       "A produced video hasn't been measured — learn before shipping more.",
                       target=produced[0])

    # An evaluated video that hasn't been uploaded yet is ready for the publish gate.
    publishable = [v for v in videos
                   if v.get("evaluated") and not v.get("video_id")
                   and v.get("status") not in ("blocked_compliance", "public",
                                                "uploaded_unlisted", "prepared_unlisted")]
    if publishable:
        return _action("publish_video",
                       "A finished video passed measurement — run the compliance gate "
                       "and prepare a gated (unlisted) publish.",
                       target=publishable[0])

    in_prod = [v for v in videos if v.get("status") in ("in_production", "drafting")]
    ready = [b for b in backlog
             if b.get("status") in ("proposed", "ready", "new", None)]

    # Focus: only one video in flight. If one is mid-production, push it, don't start another.
    if in_prod:
        return _action("continue_production",
                       "A video is mid-production — drive it to the finish before starting another.",
                       target=in_prod[0])
    if ready:
        return _action("produce_video",
                       "Top backlog topic is ready and the line is free — ship it.",
                       target=ready[0])
    # backlog empty -> refill it from the niche
    return _action("research_niche",
                   "Backlog is empty — research the niche to find the next winner.",
                   target=niches[0])


# ----------------------------------------------------------------------
# Execute: do the action through existing tools; return (summary, ask_spec).
# Each branch mutates `st` in place; advance_business saves once at the end.
# ----------------------------------------------------------------------
def _channel_name(st: dict) -> str:
    chans = st.get("channels") or []
    return chans[0]["name"] if chans and chans[0].get("name") else "unassigned"


def _execute(action: dict, st: dict, *, orch=None) -> tuple[str, dict | None, bool]:
    kind = action["kind"]
    goal = st.get("goal_usd_per_month", ceo_state.GOAL_USD_PER_MONTH)

    if kind == "set_direction":
        # Seed a legal default niche so we proceed, and ask the board to confirm/redirect.
        seed = "everyday science explained"
        ceo_state.add_niche(st, seed)
        ask = {"kind": "info",
               "what": "confirm or redirect the studio's target niche(s) and channel",
               "why": "the whole strategy keys off the niche; I seeded a default to not stall",
               "how_to_provide": "reply with the niche(s) you want, or 'keep' to keep mine"}
        return (f"No niche was set — seeded '{seed}' and asked the board to confirm.",
                ask, True)

    if kind == "produce_video":
        topic = (action["target"] or {}).get("topic", "untitled")
        channel = _channel_name(st)
        # REAL, deterministic: stand up the project workspace via the existing tool.
        import projects
        info = projects.start_project(topic)
        slug = info["slug"]
        ceo_state.add_video(st, slug=slug, channel=channel, topic=topic,
                            status="in_production")
        # mark the backlog item consumed
        for b in st.get("backlog", []):
            if b is action["target"]:
                b["status"] = "in_production"
                b["slug"] = slug
                break
        # The full multi-agent playbook is the board-gated spend → ask to greenlight.
        ask = {"kind": "approval",
               "what": f"greenlight end-to-end production of '{topic}' on {channel}",
               "why": f"it's the top backlog item toward the ${goal}/mo goal; the render "
                      "run spends real compute",
               "how_to_provide": "reply 'approved' to run the line, or redirect me"}
        if orch is not None:
            # Live: hand the playbook to the orchestrator for this slug (best-effort;
            # the cycle never blocks on it). Offline tests pass orch=None.
            try:
                orch.ask(f"Run the production playbook for project '{slug}' "
                         f"(topic: {topic}). Stop at the fact-check and render gates.")
            except Exception:  # noqa: BLE001 — execution is best-effort; the cycle goes on
                pass
        return (f"Started production on '{topic}' → project '{slug}' on {channel}; "
                "the team runs the line next.", ask, True)

    if kind == "continue_production":
        v = action["target"] or {}
        topic = v.get("topic", v.get("slug", "the current video"))
        ask = {"kind": "approval",
               "what": f"approve the next production stage for '{topic}'",
               "why": "a video is mid-flight; advancing it spends the team's time",
               "how_to_provide": "reply 'go' to continue, or tell me to pause"}
        return (f"'{topic}' is mid-production — the team needs to run its next stage.",
                ask, orch is not None)

    if kind == "analyze_performance":
        v = action["target"] or {}
        slug = v.get("slug", "")
        summary = f"Measured '{slug}'"
        try:
            import agency
            res = agency.run_self_eval(slug, apply=False)
            ceo_state.update_video(st, slug, evaluated=True,
                                   metrics={"overall": res.get("overall"),
                                            "quality_score": res.get("quality_score")})
            summary = (f"Measured '{slug}': overall {res.get('overall')}, "
                       f"quality {res.get('quality_score')}.")
        except Exception as exc:  # noqa: BLE001 — analysis never crashes a cycle
            ceo_state.update_video(st, slug, evaluated=True,
                                   metrics={"error": f"{type(exc).__name__}"})
            summary = f"Tried to measure '{slug}' but the eval errored ({exc})."
        # if it's already live, also pull real performance into the loop.
        if v.get("video_id"):
            try:
                import publish
                m = publish.ingest_analytics(slug)
                summary += (f" Live: {m.get('views')} views, RPM ${m.get('rpm_usd')}.")
                st.update(ceo_state.load())   # reflect strategy/revenue adaptation
            except Exception:  # noqa: BLE001
                pass
        ask = {"kind": "api_key",
               "what": "a read-only YouTube Data API key",
               "why": "to track real RPM/retention and double down on what the data rewards",
               "how_to_provide": "add it to .env as YT_API_KEY"}
        return (summary, ask, True)

    if kind == "publish_video":
        v = action["target"] or {}
        slug = v.get("slug", "")
        try:
            import publish
            res = publish.prepare_publish(slug)   # gate -> unlisted upload -> approval
        except Exception as exc:  # noqa: BLE001 — publishing never crashes a cycle
            return (f"Tried to publish '{slug}' but it errored ({exc}).", None, False)
        if not res.get("passed"):
            # BLOCKED: the gate said no. Surface why; do NOT ask to go public.
            reasons = "; ".join(res.get("reasons", [])[:3])
            return (f"⛔ '{slug}' BLOCKED by the compliance gate: {reasons}. "
                    "It does not ship — routing back to fix.", None, False)
        # PASSED: prepare_publish already filed the go-public approval (the human
        # checkpoint), so we don't double-file an ask here.
        where = res.get("privacy", "unlisted")
        return (f"'{slug}' passed compliance and is uploaded {where} "
                f"({res.get('video_id')}); asked the board to approve going public.",
                None, True)

    if kind == "research_niche":
        niche = action["target"] if isinstance(action["target"], str) else \
            (st.get("niches") or ["the niche"])[0]
        seeds = [f"{niche}: the most common misconception, debunked",
                 f"{niche}: a 5-minute explainer for beginners"]
        for topic in seeds:
            ceo_state.add_backlog_item(st, topic, niche=niche, source="ceo-research")
        ask = {"kind": "api_key",
               "what": "a read-only YouTube Data API key",
               "why": "to rank topic ideas on real demand/RPM instead of guessing",
               "how_to_provide": "add it to .env as YT_API_KEY"}
        return (f"Researched '{niche}' and seeded {len(seeds)} topics into the backlog.",
                ask, True)

    # default
    return ("Reviewed the portfolio and held strategy.", None, True)


# ----------------------------------------------------------------------
# Advance: one full cycle.
# ----------------------------------------------------------------------
def advance_business(*, orch=None) -> dict:
    """Run ONE CEO cycle. Halts (no state change) if the kill-switch is set."""
    if boundary.kill_switch_active():
        digest = "🛑 STOP is set (ceo/STOP) — holding. No cycle ran."
        return {"halted": True, "kind": None, "rationale": None, "digest": digest,
                "ask": None, "executed": False}

    st = ceo_state.load()
    action = choose_action(st)
    summary, ask_spec, executed = _execute(action, st, orch=orch)

    # charge the cycle's estimated cost against the budget meter the UI surfaces.
    budget = st.setdefault("budget", {"ceiling_usd": 50.0, "spent_usd": 0.0})
    budget["spent_usd"] = round(float(budget.get("spent_usd", 0)) +
                                ACTION_COST_USD.get(action["kind"], 0.1), 4)
    ceo_state.bump_cycle(st)        # also persists the _execute mutations + budget

    digest = f"📊 CEO cycle #{st.get('cycle_count')} — {action['kind']}: {summary}"

    # keep the board in the loop: journal the decision...
    boundary.ceo_log(f"{digest} | rationale: {action['rationale']}")
    # ...and file the concrete ask (the request queue the board reads).
    ask_record = None
    if ask_spec is not None:
        req = boundary.request_from_ceo(ask_spec["kind"], ask_spec["what"],
                                        ask_spec["why"], ask_spec["how_to_provide"])
        ask_record = req["record"]
        digest = f"{digest}\n   ↳ Ask [{ask_spec['kind']}]: {ask_spec['what']}"

    return {"halted": False, "kind": action["kind"], "rationale": action["rationale"],
            "summary": summary, "digest": digest, "ask": ask_record,
            "executed": executed, "cycle": st.get("cycle_count")}


# ----------------------------------------------------------------------
# Scheduler: bounded autonomy.
# ----------------------------------------------------------------------
def run_cycles(*, max_cycles: int = 1, budget_usd: float | None = None,
               orch=None, cost_fn=None) -> dict:
    """Run up to `max_cycles` CEO cycles. Stops early on: the kill-switch, or a
    cycle whose estimated cost would breach `budget_usd`. Returns the cycle results
    + why it stopped. The budget is checked BEFORE a cycle runs, so it is a ceiling
    never exceeded (not an after-the-fact tripwire)."""
    cost_of = cost_fn or (lambda kind: ACTION_COST_USD.get(kind, 0.1))
    cycles, spent, reason = [], 0.0, "max_cycles"

    for _ in range(max(0, max_cycles)):
        if boundary.kill_switch_active():
            reason = "kill_switch"
            break
        # peek the next action's cost; deterministic choose -> same action advance runs.
        peek = choose_action(ceo_state.load())
        cost = cost_of(peek["kind"])
        if budget_usd is not None and (spent + cost) > budget_usd:
            reason = "budget"
            break
        result = advance_business(orch=orch)
        if result.get("halted"):
            reason = "kill_switch"
            break
        cycles.append(result)
        spent += cost

    return {"cycles": cycles, "spent_usd": round(spent, 4), "stop_reason": reason,
            "ran": len(cycles)}
