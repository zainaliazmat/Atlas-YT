"""The durable business state — projects.py for the whole company.

One JSON file, `ceo/state.json`, holds everything Atlas-as-CEO carries between
cycles: the revenue goal, the channels and niches it runs, the topic backlog, the
videos in flight (with their status + metrics), the current strategy, and the
milestones toward monetization. Read it to know where the business is; update it
through the helpers as each cycle advances the company.

It sits next to the CEO journal/request-queue under `boundary.CEO_DIR`, so a test
that redirects that dir redirects the whole CEO surface at once.
"""
from __future__ import annotations

import time
from pathlib import Path

import boundary
import chat_state

SCHEMA_VERSION = "1.0"
GOAL_USD_PER_MONTH = 10000


def state_path() -> Path:
    """Resolved at call time so a redirected boundary.CEO_DIR redirects state too."""
    return boundary.CEO_DIR / "state.json"


def default_state() -> dict:
    """A bootstrapped company: a starter channel + niche + a small seeded backlog,
    so 'advance the business' does real work from the very first cycle. The board
    can redirect any of it — Atlas seeds a legal default rather than stall."""
    now = time.time()
    niche = "everyday science explained"
    return {
        "schema_version": SCHEMA_VERSION,
        "goal_usd_per_month": GOAL_USD_PER_MONTH,
        "revenue_usd_per_month": 0,
        "channels": [{"name": "main", "platform": "youtube", "status": "building"}],
        "niches": [niche],
        "backlog": [
            {"topic": f"{niche}: why the sky is blue", "niche": niche,
             "status": "proposed", "source": "ceo-seed"},
            {"topic": f"{niche}: how vaccines train your immune system", "niche": niche,
             "status": "proposed", "source": "ceo-seed"},
        ],
        "videos": [],
        "strategy": ("Concentrate on one niche, ship quality explainers that earn "
                     "retention, reach monetization eligibility, then double down on "
                     "what the analytics reward."),
        "milestones": [
            {"name": "Monetization eligibility (1k subs + 4k watch-hours)",
             "status": "pending"},
            {"name": "First monetized dollar", "status": "pending"},
            {"name": f"${GOAL_USD_PER_MONTH}/month sustainable", "status": "pending"},
        ],
        "budget": {"ceiling_usd": 50.0, "spent_usd": 0.0},
        "cycle_count": 0,
        "created": now,
        "updated": now,
    }


def _ensure_keys(st: dict) -> dict:
    """Backfill any missing top-level keys so an older/partial file still loads."""
    base = default_state()
    for k, v in base.items():
        st.setdefault(k, v)
    return st


def load() -> dict:
    """The business state, creating + persisting the default on first read."""
    raw = chat_state.load_json(state_path(), None)
    if not isinstance(raw, dict):
        st = default_state()
        save(st)
        return st
    return _ensure_keys(raw)


def save(st: dict) -> Path:
    """Persist atomically (mirrors projects.py / the repo convention)."""
    st["updated"] = time.time()
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)   # the atomic writer drops a tmp here
    chat_state.atomic_write_json(p, st)
    return p


# --- mutation helpers (mutate the dict, then persist) ----------------------
def update(st: dict | None = None, **changes) -> dict:
    """Merge top-level `changes` into the state and save."""
    st = st if st is not None else load()
    st.update(changes)
    save(st)
    return st


def set_strategy(st: dict, strategy: str) -> dict:
    st["strategy"] = strategy
    save(st)
    return st


def add_niche(st: dict, niche: str) -> dict:
    if niche and niche not in st.setdefault("niches", []):
        st["niches"].append(niche)
        save(st)
    return st


def add_backlog_item(st: dict, topic: str, *, niche: str = "", status: str = "proposed",
                     **extra) -> dict:
    item = {"topic": topic, "niche": niche, "status": status}
    item.update(extra)
    st.setdefault("backlog", []).append(item)
    save(st)
    return st


def add_video(st: dict, *, slug: str, channel: str, topic: str = "",
              status: str = "in_production", metrics: dict | None = None) -> dict:
    st.setdefault("videos", []).append({
        "slug": slug, "channel": channel, "topic": topic, "status": status,
        "metrics": metrics or {}, "created": time.time()})
    save(st)
    return st


def update_video(st: dict, slug: str, **changes) -> dict:
    for v in st.get("videos", []):
        if v.get("slug") == slug:
            metrics = changes.pop("metrics", None)
            if metrics is not None:
                v.setdefault("metrics", {}).update(metrics)
            v.update(changes)
            break
    save(st)
    return st


def add_milestone(st: dict, name: str, *, status: str = "pending") -> dict:
    st.setdefault("milestones", []).append({"name": name, "status": status})
    save(st)
    return st


def bump_cycle(st: dict) -> dict:
    st["cycle_count"] = int(st.get("cycle_count", 0)) + 1
    save(st)
    return st


def summary_text(st: dict) -> str:
    """A compact CEO read-out of where the business stands."""
    videos = st.get("videos", [])
    shipped = sum(1 for v in videos if v.get("status") in ("published", "produced"))
    lines = [
        f"Goal: ${st.get('goal_usd_per_month', GOAL_USD_PER_MONTH)}/mo  "
        f"(now ~${st.get('revenue_usd_per_month', 0)}/mo)",
        f"Channels: {', '.join(c.get('name', '?') for c in st.get('channels', [])) or '—'}",
        f"Niches: {', '.join(st.get('niches', [])) or '—'}",
        f"Backlog: {len(st.get('backlog', []))} topics  |  Videos: {len(videos)} "
        f"({shipped} shipped)",
        f"Strategy: {st.get('strategy', '—')}",
    ]
    return "\n".join(lines)
