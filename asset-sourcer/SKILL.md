# SKILL.md — The Asset-Sourcing & Clearance Method

This is the method you follow on every sourcing job. It turns a finished
`storyboard.json` (read against `style_guide.json`) into an `asset_manifest.json`:
for every shot that needs a real asset, find a **provably reusable** one from an
allowlist of public-domain / Creative-Commons archives, download it **local**, and
record source + license + attribution + status. When a shot can't be cleared, ship a
**flagged local placeholder** — never an unlicensed asset.

This file is the engine's *method*, not a voice. It says HOW a storyboard becomes a
cleared manifest. Who Magpie is and how she talks lives in `soul/` — never here.

## The one rule everything else serves
**Nothing is recorded as `cleared` without a verified accept-list license AND complete
attribution AND a local file.** "Probably fine" never passes. "No known copyright
restrictions" is a **reject**, not a maybe. Provenance uncertainty is disqualifying.

## Inputs (what you read)
- `storyboard.json` — the Art Director's plan. Per scene, the fields you read:
  - `scene_no` — carried straight onto each asset.
  - `shots[]` — each `{kind, content, asset_ref}`. **`asset_ref` is the spine: it
    becomes the manifest `asset_id`.** `content` is the plain description you search
    on. `kind` decides the asset type and whether you source it at all.
- `style_guide.json` — read to BIAS the search, never to override the storyboard:
  - `palette` — a near-monochrome base biases toward black-and-white candidates.
  - `reference_note` — the era/style cue (e.g. "editorial explainer / Vox-style").
  - The `#FFD000` `signature_highlight` is the house flourish — not a search term.

You do NOT change the storyboard. You resolve the assets it references.

## Step 1 — Classify each shot: source, generate, or skip
- **Typography** (`title`, `quote`, `text`, `caption`, …) → **skip.** The Composition
  Engineer renders these from script text; they are not assets.
- **Charts / data-viz** (`chart`, `graph`, `diagram`, `infographic`) → **generate.**
  Record a placeholder flagged `composition-generated (data-viz)` so #6 builds it.
  Never source a static picture of someone else's chart.
- **The map/diagram split (load-bearing):** route on the `content`.
  - A **named period/archival** map ("1929 Rand McNally Chicago", a Sanborn sheet) →
    **source as an image.** This is the scavenger hunt — period accuracy is the job.
  - A **data-driven** map ("cases by county", "animated route overlay") → **generate.**
- **Image / video / icon** kinds → **source.** Map `kind` → the manifest `type`:
  - image/photo/still/b-roll/portrait/illustration → `image`
  - footage/video/clip/motion → `video`
  - icon/logo/symbol/glyph → `icon`
  - chart/graph/data/diagram (generated) → `data-viz`

## Step 2 — Derive the search query (deterministic, no guessing)
From the shot's `content`, biased by the style:
- Extract an **era** (a year or a decade) and bias toward period-accurate results.
- If the palette is monochrome, prefer **black-and-white** candidates.
- Trim filler to the nouns that matter. The query is reproducible — same shot, same
  query, every run.

## Step 3 — Search the allowlist (allowlist-FIRST)
Query the registered sources only, **keyless public-domain / CC archives first**, then
the free-key sources. The allowlist is auditable config (`sources.py`), one descriptor
per archive, each with its own license/attribution parser:

- **Keyless:** Openverse, Wikimedia Commons, The Met (CC0 via `isPublicDomain`),
  Library of Congress (`?fo=json`), Internet Archive, NASA.
- **Free key (silent-skip if absent):** Smithsonian Open Access, Pexels, Pixabay.

A **missing optional key** is never an error — skip that source and continue. A
**dead or timing-out source** is skipped, not fatal — the run degrades gracefully to
whatever cleared, with placeholders for the rest. **Off-allowlist is impossible:** the
engine only ever iterates this list.

## Step 4 — Rank candidates (deterministic, fully-ordered)
Best first, by a total sort key so a re-run reproduces the same manifest:
1. **License preference** — worldwide-PD (CC0 / PDM / PD) → CC-BY → CC-BY-SA →
   proprietary stock (NASA / Pexels / Pixabay).
2. **Relevance** — query↔candidate token overlap.
3. **Allowlist order**, then **resolution**, then stable tiebreakers (source, url).

Reject-licensed candidates are dropped before the walk, so you only ever try to clear
a usable asset.

## Step 5 — The license truth table (the crux)
Normalize each raw license string/URL to a canonical code, then:

| Class | Codes | Verdict |
|---|---|---|
| Worldwide public domain | CC0, **Public Domain Mark (PDM)**, plain "public domain" | **ACCEPT** — no attribution legally required (capture provenance anyway) |
| Attribution licenses | CC-BY, CC-BY-SA | **ACCEPT** — attribution required (BY-SA: note share-alike) |
| Proprietary-but-permissive | Pexels, Pixabay, NASA | **ACCEPT but never auto-`cleared`** → `sourced` + a carve-out flag (identifiable people / trademarks / property / third-party can't be auto-verified) |
| Jurisdiction-limited | **"No Copyright – United States" (NoC-US)** | **REJECT** — not cleared worldwide |
| Not a license | **"No known copyright restrictions" / Flickr Commons** | **REJECT** — provenance uncertain |
| NC / ND | any non-commercial or no-derivatives variant | **REJECT** — monetized + composited video is commercial *and* derivative |
| Unknown / missing / all-rights-reserved | — | **REJECT** |

**PDM accepts; NoC-US rejects** — that distinction is intentional, not a conflation:
a worldwide PD *mark* is a definite statement; a US-only "no copyright" is silent on
every other jurisdiction.

## Step 6 — Clear, download local, and record
For the first ranked candidate that downloads:
- Write the bytes to **`<project>/assets/<asset_id>.<ext>`** — a LOCAL file. The
  manifest `uri` is the **relative local path**; it never carries a remote URL.
  (HyperFrames forbids render-time fetches — the file must be on disk for #6.)
- Within a run, **content-hash dedupe** shares the *file* (not the provenance): two
  shots reusing one image are still two asset entries, each its own `asset_id` and
  attribution, pointing at one file.
- Build the **TASL** attribution — **T**itle / **A**uthor / **S**ource / **L**icense
  (with links where available). CC-BY / -SA legally require it.
- Set the **status** (see below).

If nothing downloads or nothing clears → a **placeholder** asset pointing at the one
shared local `assets/_placeholder.png`, flagged with the reason + a suggested query.

## `status` — the three values, precisely
- **`cleared`** — accept-list license **and** complete attribution **and** a local
  `uri`. Provably reusable. (PD/CC0 need no attribution; BY/BY-SA need a findable
  author + source link.)
- **`sourced`** — a real, licensed file is on disk but clearance is **incomplete**:
  either a BY/BY-SA whose required attribution couldn't be completed (no findable
  author), or a Pexels/Pixabay/NASA asset whose carve-outs can't be auto-verified.
  Don't discard it — surface it (flagged) for a human or the future clearance gate to
  promote → `cleared` or → `placeholder`.
- **`placeholder`** — nothing usable found, the download failed, or the shot is a
  composition-generated data-viz. A flagged local stand-in; the honest signal lives in
  `status` + `flag` so #6 (or a human) can swap it.

## The hard invariants (enforced in code — rely on them)
1. `asset_id` == the shot's `asset_ref`.
2. `scene_no` is carried straight from the storyboard scene.
3. `type` ∈ {`image`, `video`, `icon`, `data-viz`}.
4. Every recorded asset comes from an allowlisted source (membership re-checked).
5. Nothing is `cleared` without an accept-list license AND complete attribution AND a
   local `uri`.
6. Every `placeholder` / `sourced` `uri` resolves to a real local file.
7. Ranking is deterministic → the manifest is reproducible (modulo live-archive drift).

## Your output contract (the sourcing job)
Emit plain dicts in the `asset_manifest` shape (Atlas stamps `schema_version` and
validates). Each asset:

```json
{
  "asset_id": "s4-1",
  "scene_no": 4,
  "type": "image",
  "source": "met",
  "uri": "assets/s4-1.jpg",
  "license": "CC0 1.0",
  "license_code": "cc0",
  "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
  "attribution": "\"Cotton Plant\" via The Metropolitan Museum of Art (https://…) CC0 1.0",
  "provenance": "https://www.metmuseum.org/art/collection/search/12345",
  "status": "cleared"
}
```

The **Composition Engineer (#6)** reads this manifest to place each `asset_id` into its
scene and to render the required TASL credits — every asset already cleared, local, and
attributed, or honestly flagged.
