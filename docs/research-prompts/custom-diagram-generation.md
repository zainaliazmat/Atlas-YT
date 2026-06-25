# Research brief: a reliable custom-diagram (hand-style illustration) generator for the video pipeline

> Paste everything below into a fresh research/brainstorming session. It is written to be
> self-contained — it states the goal, the hard constraints from our real render stack, what
> we already tried and why it failed, and the specific questions to answer.

---

## The goal

Give the YT-AGENTS video pipeline a way to turn a **storyboard shot description** (plain
English, e.g. *"a chat bubble sprouting small robotic arms reaching toward buttons"*) into a
**clean, on-brand, hand-illustrated-style visual** that drops into a scene — reliably, fast
enough for a pipeline, and without a human in the loop.

Today these "diagram" shots render as blank placeholders, so videos look like captions on an
empty background. We are shipping a stock-photo stopgap now; this brief is for the real fix.

## How our render stack actually works (hard constraints — design around these)

- **Renderer = HyperFrames CLI** (`npx hyperframes`, currently v0.7.5). Scenes are authored as
  **self-contained HTML/CSS** and rendered to MP4 via headless Chrome. So the *native* visual
  format is HTML/CSS/**inline SVG** — no external image fetches at render time (the engine's
  rule is "no runtime dependency, no render-time fetch"; brand logos are already inlined as SVG).
- **House style:** flat, editorial, minimal. Palette is roughly cream background `#f2ede4`,
  near-black ink `#2b2722`, one warm accent (e.g. `#c8632b`). Fraunces (display) + Inter (text).
- **Captions occupy the bottom ~140px** of a 1280×720 frame, so artwork should live in the
  upper/centre region and leave that band clear.
- **There is an automated quality gate** (`lint` / `validate` / `inspect`) that BLOCKS a render
  on structural problems: console errors, layout overflow, and **`text_occluded`** (text hidden
  under an opaque element). Any generated artwork must pass this — no stray text nodes, no
  elements covering the caption zone.
- **LLM access** is the Claude Agent SDK on a subscription seam (no metered key). Small calls
  (~10s) are fine; large/complex single generations are unreliable (see "what failed").

## What we already tried, and exactly why it failed

We prototyped **"ask the LLM to emit one inline SVG per shot."** Findings from real runs:

- A trivial request (a 100×100 circle) returned valid SVG in **~10s**. Good.
- A full-scene 1280×720 illustration request **timed out at 150s** (never returned).
- Even a *constrained* "≤25 shapes, 800×450, 2-colour line art" icon request **timed out at 90s**.

So latency is **wildly unpredictable** (10s … >90s) and a pipeline needs ~10–20 of these per
video. One hung generation stalls the whole render. We could not make per-shot LLM-SVG
dependable. That is the core problem to solve — not "can an LLM draw SVG" (it can), but
**"how do we get good diagrams reliably and fast at pipeline scale."**

## Questions to research (this is the actual ask)

1. **Latency/reliability:** Why does Claude-via-Agent-SDK SVG generation spike from ~10s to
   >90s as the drawing gets more complex? Is it the model reasoning, output-token length, or
   SDK streaming overhead? What makes it bounded and predictable — `max_tokens` caps, a smaller/
   faster model (Haiku) for the draw step, forcing terse output, or a non-SDK direct API path?
2. **Better generation strategies than "one freeform SVG per shot":**
   - A **parametric template library** (hand-drawn-style SVG components: speech bubble, arrow,
     loop, node-graph, before/after, bar/stack) that an LLM *composes/parameterises* instead of
     drawing from scratch — fast, deterministic, on-style, but limited vocabulary. How wide a
     vocabulary covers most explainer shots?
   - A **batch call** that returns all N scene diagrams in one structured response (amortise
     latency) — does quality/reliability hold vs per-shot?
   - **Generative image models** (e.g. an image API) producing a hand-illustration look, then
     placed as a scene asset. Trade-offs: cost, latency, style consistency, the "no
     render-time fetch" rule (we'd download once into the project, which is fine), licensing.
   - **Icon/illustration libraries** with a hand-drawn aesthetic (e.g. open sets) mapped to shot
     concepts by an LLM. Reliable + instant, but how well do fixed icons match arbitrary shots?
3. **Style control:** how to keep every diagram in ONE consistent hand-illustrated house style
   (stroke weight, palette, level of detail) across a whole video, regardless of approach?
4. **Validation loop:** how to auto-check a generated diagram is good — passes our HyperFrames
   `inspect`/`validate` gate (no occlusion/overflow), has no text in the caption band, isn't
   visually empty/garbled — and auto-retry or fall back to stock when it isn't?
5. **The integration point:** should diagrams be produced in the **asset stage** (Magpie writes
   real asset files the composition engine then embeds) or inside the **composition engine**
   (Mason generates inline at compose time)? Which keeps the frozen contracts clean? (Context:
   shots are typed `kind:"diagram"` and currently routed to "composition-generated" but the
   engine only has bar-chart and brand-chip generators, so they fall through to placeholders.)

## What a good answer looks like

A recommended architecture (one primary approach + fallback), with concrete latency/cost/quality
trade-offs, a small proof that it reliably produces 10–20 on-style diagrams for one real video
without hanging, and the exact pipeline integration point. Deliverable: a buildable plan, not
just options.

## Useful repo pointers for the researcher
- Composition engine + existing inline-SVG brand chips: `composition-engineer/composition_engine.py`
  (see `BRAND_CHIPS`, `render_brand_chips`, the `_media_html` / data-viz fallback chain).
- HyperFrames CLI wrappers + the gate: `composition-engineer/hf_tools.py`.
- Asset sourcing + shot classification (`kind:diagram` → composition-generated): `asset-sourcer/source_engine.py`, `asset-sourcer/sources.py`.
- A real failing example: `atlas/projects/how-ai-agents-actually-work-*` — `storyboard.json`
  (the `kind:"diagram"` shots), `asset_manifest.json` (all `status:"placeholder"`), and the
  scene frames (clean captions, empty backgrounds).



## Research on the following topics, packages and libraries
- Hand-drawn doodle animation (often called "boiling line" or "sketch" animation) is a charming style defined by its raw, imperfect, and constantly moving linework. It embraces a textured, human feel that digital perfection cannot replicate.
- https://roughjs.com/
- https://roughnotation.com/


## Look at this spec file for context, it has the the D3 GSAP Lottie specs which we have to implement
- /home/zain-ali/Documents/YT-AGENTS/docs/superpowers/specs/2026-06-23-motion-stack-d3-gsap-lottie.md