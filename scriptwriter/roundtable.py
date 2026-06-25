"""
Creative Roundtable — internal review and enhancement for a specialist agent.

Before a draft leaves a specialist's desk it goes through a three-person internal
review, each running with FRESH context — no shared memory, no chat history:

  1. CRITIC      — finds structural weaknesses against the craft principles (SKILL.md).
                   Diagnoses only; never prescribes a fix.
  2. RESEARCHER  — for each weakness, finds a specific, surprising, SOURCED detail.
                   Has real web-search access (it is not just an LLM with opinions).
  3. CRAFTSMAN   — rewrites the artifact incorporating the criticisms + the research,
                   in the specialist's own voice (STYLE.md), preserving what already works.

This is the canonical implementation built for Marlow (Scriptwriter); Iris, Cadence,
and Mason reuse it via the same RoundtableConfig seam.

DESIGN NOTES (grounded in this codebase, not the generic blueprint):
- The LLM seam is the specialist's own `chat(system, user) -> str` (llm.chat). It does
  NOT take a per-call model or a native tool-use loop, so the three sub-agents share
  one seam; the intended creative/fast model split is recorded in the log as metadata.
- "Fresh context" is structural: each call is an independent one-shot (llm.py's query()
  opens a fresh context per call). We PROVE it by recording, per sub-agent, the system +
  user token estimate and `history_tokens: 0` in the log's `context_proof`.
- The Researcher's search is REAL but orchestrated here (the seam can't run a tool loop):
  we derive a query per weakness, call `search_tool`, and feed the results into the
  Researcher's single call so it picks the killer detail from real sources.
- GRACEFUL DEGRADATION is absolute: if any sub-agent fails (bad JSON, an exception, or
  the Critic finds nothing), `review_and_enhance` returns the DRAFT unchanged and records
  the reason in the log. It never raises — a review step must never crash the pipeline.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Config + result containers
# ----------------------------------------------------------------------
@dataclass
class RoundtableConfig:
    """Configuration for a Creative Roundtable session."""
    specialist_name: str          # "Marlow"
    specialist_role: str          # "Scriptwriter"
    skill_md: str                 # SKILL.md — the craft method (the Critic judges against it)
    style_md: str                 # STYLE.md — the voice (the Craftsman writes in it)
    soul_md: str                  # SOUL.md  — the identity (who the specialist is)
    llm_chat: Callable            # chat(system, user) -> str (the specialist's brain)
    creative_model: str = "claude-opus-4-8"   # Critic + Craftsman (metadata; see module note)
    fast_model: str = "claude-sonnet-4-6"      # Researcher (metadata; see module note)
    search_tool: Optional[Callable] = None     # web_search(query, max_results) -> list[dict]
    max_criticisms: int = 3                    # how many weaknesses the Critic must surface
    max_searches_per_weakness: int = 2         # bound the live search per criticism


@dataclass
class RoundtableResult:
    """The complete output of a roundtable session (convenience container)."""
    draft_artifact: dict
    criticisms: list[dict]
    research_findings: list[dict]
    enhanced_artifact: dict
    metadata: dict = field(default_factory=dict)


# A sub-agent step failed in a recoverable way — caught by review_and_enhance, which
# then returns the draft unchanged. Never propagates out of the roundtable.
class _RoundtableStepError(Exception):
    pass


def _estimate_tokens(text: str) -> int:
    """Dependency-free token estimate (~4 chars/token). Used only for the fresh-context
    proof in the log — exactness doesn't matter, the `history_tokens: 0` does."""
    return max(0, len(text or "") // 4)


class CreativeRoundtable:
    """A reusable internal review system. Fresh sub-agent context every run; no memory
    is shared between sub-agents or across sessions."""

    def __init__(self, config: RoundtableConfig):
        self.config = config
        self._validate_config()

    def _validate_config(self):
        if not self.config.skill_md:
            raise ValueError("SKILL.md content is required for the Critic")
        if not self.config.style_md:
            raise ValueError("STYLE.md content is required for the Craftsman")

    # ------------------------------------------------------------------
    # The full 3-step process
    # ------------------------------------------------------------------
    def review_and_enhance(
        self,
        draft_artifact: dict,
        upstream_intent: dict,
        project_dir: Optional[Path] = None,
    ) -> tuple[dict, dict]:
        """Run Critic -> Researcher -> Craftsman and return (enhanced, log).

        On ANY recoverable failure (bad JSON, a sub-agent exception, or the Critic
        finding nothing to improve) the DRAFT is returned unchanged and the reason is
        recorded in the log. This method never raises.
        """
        name = self.config.specialist_name
        logger.info("=== %s's Creative Roundtable starting ===", name)
        logger.info("All sub-agents run with FRESH context — no shared memory.")
        start = time.time()

        context_proof: dict = {}
        criticisms: list[dict] = []
        findings: list[dict] = []
        enhanced = draft_artifact
        error: str | None = None

        try:
            logger.info("Step 1/3: CRITIC analyzing draft...")
            criticisms = self._run_critic(draft_artifact, upstream_intent, context_proof)
            logger.info("Critic found %d weaknesses.", len(criticisms))
            for i, c in enumerate(criticisms):
                logger.info("  %d. %s", i + 1, str(c.get("diagnosis", ""))[:100])

            if not criticisms:
                # Nothing to improve — the draft stands. Not a failure.
                error = None
                raise _RoundtableStepError("critic found no actionable weaknesses")

            logger.info("Step 2/3: RESEARCHER searching for improvements...")
            findings = self._run_researcher(
                criticisms, draft_artifact, upstream_intent, context_proof)
            logger.info("Researcher returned %d findings.", len(findings))

            logger.info("Step 3/3: CRAFTSMAN rewriting with all feedback...")
            enhanced = self._run_craftsman(
                draft_artifact, criticisms, findings, upstream_intent, context_proof)
            logger.info("Craftsman completed the enhanced artifact.")
        except _RoundtableStepError as exc:
            # "No weaknesses" is a clean no-op; anything else is a recorded soft failure.
            if str(exc) != "critic found no actionable weaknesses":
                error = str(exc)
            enhanced = draft_artifact
            logger.warning("Roundtable degraded to the draft: %s", exc)
        except Exception as exc:  # noqa: BLE001 — a review step must NEVER crash the pipeline
            error = f"{type(exc).__name__}: {exc}"
            enhanced = draft_artifact
            logger.warning("Roundtable failed (%s) — keeping the draft.", error)

        elapsed = time.time() - start
        log = {
            "roundtable_version": "1.0",
            "specialist": name,
            "role": self.config.specialist_role,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_seconds": round(elapsed, 2),
            "models_used": {
                "critic": self.config.creative_model,
                "researcher": self.config.fast_model,
                "craftsman": self.config.creative_model,
            },
            "context_proof": context_proof,
            "draft_artifact": draft_artifact,
            "criticisms": criticisms,
            "research_findings": findings,
            "enhanced_artifact": enhanced,
            "diff_summary": self._generate_diff_summary(draft_artifact, enhanced),
            "error": error,
        }

        if project_dir is not None:
            try:
                log_path = Path(project_dir) / "roundtable_log.json"
                log_path.write_text(json.dumps(log, indent=2))
                logger.info("Roundtable log saved to %s", log_path)
            except Exception as exc:  # noqa: BLE001 — persistence is best-effort
                logger.warning("Couldn't save roundtable log: %s", exc)

        logger.info("=== Roundtable complete in %.1fs ===", elapsed)
        return enhanced, log

    # ------------------------------------------------------------------
    # STEP A: THE CRITIC — diagnose against SKILL.md; never prescribe.
    # ------------------------------------------------------------------
    def _run_critic(self, draft: dict, upstream_intent: dict,
                    context_proof: dict) -> list[dict]:
        cfg = self.config
        system_prompt = f"""You are {cfg.specialist_name}'s INTERNAL CRITIC. You are not a separate person — you are the voice in {cfg.specialist_name}'s head that refuses to accept anything less than excellence.

YOUR STANDARDS ARE DEFINED BY:

=== SKILL.md (Your Craft Principles) ===
{cfg.skill_md}

=== SOUL.md (Your Identity) ===
{cfg.soul_md}

=== UPSTREAM CREATIVE INTENT (the vision this work must serve) ===
{json.dumps(upstream_intent, indent=2)}

---

YOUR JOB: Find the {cfg.max_criticisms} greatest weaknesses in the following draft {cfg.specialist_role.lower()}.

RULES:
1. QUOTE the exact text that is weak. No generalizations.
2. EXPLAIN why it fails against a SPECIFIC principle from SKILL.md (quote the rule).
3. DO NOT suggest fixes. You diagnose, you do not prescribe.
4. Be RUTHLESS. If it's mediocre, say so. Quality, not politeness.
5. Rank from most critical to least.

Return ONLY a JSON array of exactly {cfg.max_criticisms} objects (fewer only if the draft is genuinely strong):
[
  {{
    "rank": 1,
    "severity": "critical|major|moderate",
    "principle_violated": "the rule from SKILL.md this violates (quote it)",
    "target_text": "the exact weak text from the draft",
    "location": "which scene/section",
    "diagnosis": "why it fails — the gap between what is written and what the principle demands",
    "impact": "what the viewer loses"
  }}
]
DO NOT suggest how to fix it. DO NOT rewrite it. Only diagnose."""

        user_prompt = (
            f"Here is the draft {cfg.specialist_role.lower()} to critique:\n\n"
            f"{json.dumps(draft, indent=2)}\n\n"
            f"Find the {cfg.max_criticisms} greatest weaknesses. Quote exact text. "
            "Reference exact principles from SKILL.md.")

        context_proof["critic"] = self._context_proof(system_prompt, user_prompt)
        logger.info(
            "CRITIC: Fresh context established. System: %d tokens, User: %d tokens. "
            "Total: %d. History: 0 tokens.",
            context_proof["critic"]["system_tokens"],
            context_proof["critic"]["user_tokens"],
            context_proof["critic"]["total_tokens"])

        reply = cfg.llm_chat(system_prompt, user_prompt)
        parsed = self._parse_json(reply, "critic")
        criticisms = parsed if isinstance(parsed, list) else parsed.get("criticisms", [])
        if not isinstance(criticisms, list):
            raise _RoundtableStepError("critic did not return a list of criticisms")
        return criticisms[: cfg.max_criticisms]

    # ------------------------------------------------------------------
    # STEP B: THE RESEARCHER — real search, then extract the killer detail.
    # ------------------------------------------------------------------
    def _run_researcher(self, criticisms: list[dict], draft: dict,
                        upstream_intent: dict, context_proof: dict) -> list[dict]:
        cfg = self.config

        # Real web search, orchestrated here (the chat seam can't run a tool loop). We
        # derive a query per weakness and hand the Researcher the raw results to mine.
        search_results: list[dict] = []
        if cfg.search_tool is not None:
            topic = self._topic_hint(upstream_intent, draft)
            for c in criticisms:
                for q in self._search_queries(c, topic)[: cfg.max_searches_per_weakness]:
                    try:
                        hits = cfg.search_tool(q) or []
                    except Exception as exc:  # noqa: BLE001 — a flaky search never crashes
                        logger.warning("Researcher search failed for %r: %s", q, exc)
                        hits = []
                    for h in hits[:5]:
                        search_results.append({"for_rank": c.get("rank"), "query": q, **h})

        brief_items = [{
            "target_criticism_rank": c.get("rank"),
            "weakness": c.get("diagnosis", ""),
            "target_scene": c.get("location", ""),
            "whats_needed": self._infer_research_need(c),
        } for c in criticisms]

        has_search = cfg.search_tool is not None
        tool_line = (
            "You have been handed REAL web-search results below — mine THEM for the "
            "killer detail and cite the source_url they carry."
            if has_search else
            "No live search was available this run — draw the most specific, surprising, "
            "verifiable detail you can from your own knowledge, and say so in the source.")

        system_prompt = f"""You are an EXPERT RESEARCH ASSISTANT embedded in {cfg.specialist_name}'s creative process. Your specialty is finding SPECIFIC, SURPRISING, UNDER-EXPLORED details that turn a weak moment into the strongest moment in the piece.

The Critic identified {len(criticisms)} weaknesses. Find the KILLER DETAIL for each.

WHAT MAKES A KILLER DETAIL:
1. SPECIFIC — "Sales dropped 34% in Q3 2019", not "sales declined".
2. SURPRISING — something the viewer wouldn't already guess.
3. EVOCATIVE — it creates a mental image.
4. SOURCED — you must say where it comes from.

{tool_line}

Return ONLY a JSON object with a "findings" array:
{{
  "findings": [
    {{
      "target_criticism_rank": 1,
      "found_detail": "the specific, surprising detail",
      "detail_type": "statistic|quote|anecdote|metaphor|case_study|causal_link",
      "source_description": "where it comes from",
      "source_url": "URL if available",
      "suggested_use": "one sentence on how it strengthens the weak text",
      "why_surprising": "why the average viewer wouldn't know this"
    }}
  ]
}}
If you can't find a strong detail for a weakness, say so honestly rather than forcing a weak one. Quality over completeness."""

        results_block = (json.dumps(search_results, indent=2)
                         if search_results else "(no live search results this run)")
        user_prompt = (
            "Weaknesses that need better material:\n"
            f"{json.dumps(brief_items, indent=2)}\n\n"
            "Live web-search results to mine (each tagged with the criticism rank it "
            f"was gathered for):\n{results_block}\n\n"
            f"For context, the full draft:\n{json.dumps(draft, indent=2)}\n\n"
            "Find the KILLER DETAIL for each weakness.")

        context_proof["researcher"] = self._context_proof(system_prompt, user_prompt)
        logger.info(
            "RESEARCHER: Fresh context established. System: %d tokens, User: %d tokens. "
            "Total: %d. History: 0 tokens. (%d live search results)",
            context_proof["researcher"]["system_tokens"],
            context_proof["researcher"]["user_tokens"],
            context_proof["researcher"]["total_tokens"], len(search_results))

        reply = cfg.llm_chat(system_prompt, user_prompt)
        parsed = self._parse_json(reply, "researcher")
        findings = parsed.get("findings", []) if isinstance(parsed, dict) else parsed
        return findings if isinstance(findings, list) else []

    # ------------------------------------------------------------------
    # STEP C: THE CRAFTSMAN — rewrite in voice, same schema, preserve strengths.
    # ------------------------------------------------------------------
    def _run_craftsman(self, draft: dict, criticisms: list[dict],
                       findings: list[dict], upstream_intent: dict,
                       context_proof: dict) -> dict:
        cfg = self.config

        feedback = []
        for c in criticisms:
            match = next(
                (r for r in findings
                 if r.get("target_criticism_rank") == c.get("rank")), None)
            feedback.append({
                "weakness_location": c.get("location"),
                "problem": c.get("diagnosis"),
                "severity": c.get("severity"),
                "research_to_use": match.get("found_detail") if match else None,
                "research_suggested_use": match.get("suggested_use") if match else None,
            })

        system_prompt = f"""You are {cfg.specialist_name}. Not a copy — you ARE {cfg.specialist_name}, the master {cfg.specialist_role.lower()}, at the peak of your craft.

YOUR VOICE AND RULES:

=== STYLE.md (Your Voice — how you write) ===
{cfg.style_md}

=== SOUL.md (Your Identity — who you are) ===
{cfg.soul_md}

=== UPSTREAM CREATIVE INTENT (the vision you must serve) ===
{json.dumps(upstream_intent, indent=2)}

---

Rewrite the draft {cfg.specialist_role.lower()}, incorporating feedback from your internal review.

RULES:
1. ADDRESS EVERY CRITICISM — each weakness must be visibly improved.
2. USE THE RESEARCH — weave the specific details/quotes/stats in; don't just slot them.
3. PRESERVE WHAT WORKS — do not rewrite sections the Critic did not flag.
4. ONE COHERENT VOICE — it must read like one sitting, not a patchwork of fixes.
5. FOLLOW STYLE.md — every rule is non-negotiable.
6. SERVE THE INTENT — the emotional arc / pacing / thematic anchor is your constitution.

OUTPUT: Return the complete rewritten {cfg.specialist_role.lower()} as a JSON object with the EXACT SAME schema/structure as the draft. Do not add or remove fields. Do not change the structure. Improve the words. Return ONLY the JSON object."""

        user_prompt = (
            "=== ORIGINAL DRAFT ===\n"
            f"{json.dumps(draft, indent=2)}\n\n"
            "=== CRITICISMS TO ADDRESS ===\n"
            f"{json.dumps(feedback, indent=2)}\n\n"
            "=== FULL RESEARCH FINDINGS ===\n"
            f"{json.dumps(findings, indent=2)}\n\n"
            f"Rewrite the complete {cfg.specialist_role.lower()}. Address every criticism. "
            "Weave in the research. Follow your STYLE.md. Serve the creative intent. "
            "Return ONLY the JSON object with the same structure as the draft.")

        context_proof["craftsman"] = self._context_proof(system_prompt, user_prompt)
        logger.info(
            "CRAFTSMAN: Fresh context established. System: %d tokens, User: %d tokens. "
            "Total: %d. History: 0 tokens.",
            context_proof["craftsman"]["system_tokens"],
            context_proof["craftsman"]["user_tokens"],
            context_proof["craftsman"]["total_tokens"])

        reply = cfg.llm_chat(system_prompt, user_prompt)
        enhanced = self._parse_json(reply, "craftsman")
        if not isinstance(enhanced, dict) or not enhanced:
            raise _RoundtableStepError("craftsman did not return a JSON object")
        return enhanced

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _context_proof(self, system_prompt: str, user_prompt: str) -> dict:
        """The fresh-context receipt for one sub-agent. history_tokens is ALWAYS 0:
        each call is system + user only — no chat_state, no memory, no prior messages."""
        st, ut = _estimate_tokens(system_prompt), _estimate_tokens(user_prompt)
        return {"system_tokens": st, "user_tokens": ut,
                "total_tokens": st + ut, "history_tokens": 0}

    def _topic_hint(self, upstream_intent: dict, draft: dict) -> str:
        """A short topical anchor for search queries, from intent then the draft."""
        ni = (upstream_intent or {}).get("narrative_intent") or {}
        vl = ni.get("video_level") or {}
        for cand in (vl.get("core_thesis"),
                     ((upstream_intent or {}).get("thematic_anchor") or {}).get("core_metaphor"),
                     draft.get("working_title")):
            if cand:
                return str(cand)
        return ""

    _STOP = frozenset({"the", "a", "an", "is", "are", "of", "to", "and", "or", "no",
                       "not", "this", "that", "it", "its", "in", "on", "for", "with",
                       "viewer", "scene", "hook", "draft", "text", "concrete", "image",
                       "generic", "abstract", "vague", "flat", "dry", "weak"})

    def _search_queries(self, criticism: dict, topic: str) -> list[str]:
        """Derive real search queries for a weakness: the topic plus the distinctive
        words of the diagnosis/target text, biased by the inferred research need."""
        words = re.findall(
            r"[a-zA-Z][a-zA-Z0-9'-]{2,}",
            f"{criticism.get('target_text','')} {criticism.get('diagnosis','')}")
        keys = [w for w in words if w.lower() not in self._STOP][:6]
        need = self._infer_research_need(criticism)
        bias = ("statistic" if "statistic" in need else
                "study" if "study" in need or "data" in need else
                "example")
        base = " ".join([topic] + keys).strip()
        if not base:
            base = topic or " ".join(keys)
        queries = [f"{base} {bias}".strip()]
        if topic and keys:
            queries.append(f"{topic} surprising {keys[0]}".strip())
        return [q for q in queries if q]

    def _infer_research_need(self, criticism: dict) -> str:
        d = (criticism.get("diagnosis", "") + " "
             + criticism.get("principle_violated", "")).lower()
        if any(w in d for w in ("generic", "vague", "cliché", "cliche", "abstract")):
            return "Specific, concrete detail with real numbers or named examples"
        if any(w in d for w in ("statistic", "data", "number", "percentage")):
            return "A more surprising or counter-intuitive statistic from a credible source"
        if any(w in d for w in ("metaphor", "analogy", "comparison")):
            return "A fresh metaphor from an unexpected domain"
        if any(w in d for w in ("hook", "opening", "attention")):
            return "A provocative, arresting opening fact that creates a knowledge gap"
        if any(w in d for w in ("emotion", "feeling", "flat", "dry")):
            return "A human story or anecdote with emotional weight"
        if any(w in d for w in ("quote", "authority", "expert")):
            return "A provocative, non-obvious quote from a credible expert"
        return "A specific, surprising detail that strengthens the weak passage"

    def _parse_json(self, response: str, who: str):
        """Parse JSON from an LLM reply, tolerating prose / markdown fences. Raises
        _RoundtableStepError (caught upstream) so bad JSON degrades to the draft."""
        text = (response or "").strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        else:
            openers = [i for i in (text.find("["), text.find("{")) if i != -1]
            if openers:
                text = text[min(openers):]
        try:
            return json.loads(text)
        except Exception:
            # one salvage pass: trim to the last closing bracket
            for close in ("]", "}"):
                cut = text.rfind(close)
                if cut != -1:
                    try:
                        return json.loads(text[: cut + 1])
                    except Exception:
                        continue
            raise _RoundtableStepError(
                f"{who} did not return valid JSON (first 200 chars: "
                f"{(response or '').strip()[:200]!r})")

    def _generate_diff_summary(self, draft: dict, enhanced: dict) -> dict:
        """A small structural diff: which scenes had their narration rewritten."""
        changes = {"scenes_modified": 0, "key_changes": []}
        for i, (d, e) in enumerate(zip(draft.get("scenes", []),
                                       enhanced.get("scenes", []))):
            if d.get("narration") != e.get("narration"):
                changes["scenes_modified"] += 1
                changes["key_changes"].append(
                    {"scene": i + 1, "change_type": "narration_rewritten"})
        if draft.get("hook") != enhanced.get("hook"):
            changes["key_changes"].append({"change_type": "hook_rewritten"})
        return changes
