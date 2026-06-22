# SKILL.md — The Narration & Documentary-Mix Method

This is the method Cadence follows on every audio job. It turns a finished
`script.json` (read against `style_guide.json`, and `storyboard.json` when present)
into the audio trio: `narration.wav`, `narration.transcript.json`, and
`audio_manifest.json` — with a pre-mixed `master.wav` that carries the documentary mix
into the final render.

This file is the engine's *method*, not a voice. It says HOW a script becomes a mixed
soundtrack. Who Cadence is and how she talks lives in `soul/` — never here.

## The one rule everything else serves
**The VO is authoritative; everything else serves the words.** A bed that eats a
syllable is wrong even if it's beautiful. Music ducks hard under narration. SFX is one
accent, not a layer. And **nothing uncleared is ever baked into `master.wav`** — every
music/SFX track carries a verified license + attribution, or it does not ship in the
mix.

## Two jobs, in pipeline order

### Job 1 — `record_narration` (the parallel stage, before compose)
Produces `narration.wav` + `narration.transcript.json`, and nothing else.

1. **Validate the script BEFORE spending.** tts is slow (~11s of wall-clock per scene,
   fixed `npx`+model-load overhead). Reject a malformed/empty script — no scenes, no
   `narration` on any scene — before a single synthesis call.
2. **Per-scene tts.** For each scene, `hyperframes tts <narration> -v <voice> -o
   audio/scene-NN.wav --json`. Capture the JSON's `durationSeconds` — that is the
   authority for scene offsets (validated positive). One text in → one wav out;
   Kokoro emits 24 kHz / mono / s16le. Default voice `af_heart`, configurable.
3. **Concat, losslessly.** Every scene wav shares the same params, so the FFmpeg concat
   demuxer with `-c copy` is sample-accurate and re-encode-free → `audio/narration.wav`.
4. **Build the transcript = the timing authority.** One segment per scene, with GLOBAL
   cumulative `start_sec`/`end_sec` from the accumulated tts durations, tagged with
   `scene_no` + `text`. The Composition Engineer prefers this segment span over the
   script's `duration_est_sec` estimate, and offsets captions off it — so per-scene
   global timing is the contract.
5. **Optional word-level enrichment.** If a `whisper.cpp` binary is present,
   `transcribe` adds per-word timings folded into each scene segment as `words[]`. Its
   absence is never fatal — the deterministic per-scene segments stand on their own.

**Why per-scene, not one pass:** synthesizing per scene hands you exact scene
boundaries *and* the whole transcript for free, with no forced-alignment ASR. A single
concatenated pass would force you to align transcript text back to scene narration.

### Job 2 — `mix_audio` (after compose, before the final render)
Produces `master.wav` + `audio_manifest.json`, reading the narration stage's outputs.

6. **Mood → music query (the one LLM-assisted step).** From `style_guide`
   (`reference_note`, `dos`, palette) name a short *instrumental, no-vocals* bed query.
   The LLM only picks the search words; if no brain is reachable it falls back to a
   deterministic keyword map. Everything downstream is deterministic.
7. **Source + clear ONE bed** from the audio allowlist, exactly the way the Asset
   Sourcer clears images: keyless CC/PD archives first, then keyed. Rank
   license-first, download the first that clears **local** (HyperFrames forbids
   render-time fetches). No bed clears → omit the bed (master = VO + accent); a
   candidate found but un-cleared → a flagged **placeholder** track, excluded from the
   master. Never bake unlicensed audio.
8. **Place the ONE signature SFX accent.** Read `storyboard.json` `signature_beat`
   (it's on disk by the mix stage). Anchor the accent on the **cut into** that scene:
   `at_sec` = the scene's first transcript segment start (global). Synthesize it from
   the bundled CC0 kit (`sfx_kit`) so it lands offline/keyless/cleared, bake it into
   the master, and record it as a spec track (`scene_no`, `at_sec`) so #6 can re-time
   to the exact shot beat later. No signature beat → **omit** it. Silence beats a
   mis-placed hit.
9. **Pre-mix `master.wav` (the documentary mix).** VO at reference gain (0 dB), used
   un-attenuated as the sidechain key; the bed mood-matched to the topic and hard-ducked
   under the VO (`sidechaincompress`) + tail-faded; the accent delayed to `at_sec`,
   sitting under the VO. Let a beat of **near-silence precede the turn** — pull the bed
   toward the duck floor for a breath right before the signature beat so the cut and its
   accent land into space, not into a busy bed. Trim the master to **exactly**
   `total_duration_sec` so it aligns to the concatenated video, with a final limiter
   against summed clipping.
10. **Emit the manifest (the master-bridge).** See below.

## The master-bridge (why the mix actually lands)
The renderer (`composition-engineer/hf_tools.assemble_final`) muxes
`tracks[role=="narration"].uri` and today ignores `gain_db`/`ducking` and the music/SFX
tracks. So:
- the **narration track's `uri` → `master.wav`** (the full mix) — that is what gets
  muxed, so the documentary mix lands in the MP4 now, with **zero** Composition
  Engineer edits;
- `vo_uri` → `narration.wav` (the pure VO the transcript describes) and a top-level
  `master_uri` back-reference the clean follow-up: when Mason later muxes `master_uri`,
  the narration `uri` goes back to the pure VO and no schema changes again;
- `gain_db`/`ducking` on every track stay **accurate to the mix already baked** (and
  are the spec for #6's future per-track composite): narration `ducking:false`, bed
  `ducking:"narration"`.

## Total duration — one source of truth
`total_duration_sec = sum(per-scene tts durationSeconds)`. The SAME value is stamped
into `transcript.total_duration_sec`, `manifest.total_duration_sec`, and used as the
`master.wav` trim target. The three always agree (unit-tested).

## The audio allowlist (auditable config — `audio_sources.py`)
One descriptor per archive, each with its own license/attribution parser. The engine
iterates exactly this list — off-allowlist is structurally impossible.
- **Keyless (CC/PD):** Openverse audio, Wikimedia Commons audio, Internet Archive audio
  (the item's file is resolved via a second metadata request).
- **Free key (silent-skip if absent):** Freesound (SFX/short loops; previews are the
  CC-licensed downloadable).
- **Bundled, local, always-available:** the CC0 SFX kit (`sfx_kit.py`) — synthesized
  in-engine with FFmpeg, so the signature accent never depends on a keyed source. A
  missing optional key is never an error; a dead/timing-out source is skipped, not
  fatal — the run degrades to whatever cleared.

## The license truth table (the crux — the single policy seam)
Same posture as the Asset Sourcer, audio-flavored. Normalize each raw license to a
canonical code, then:

| Class | Codes | Verdict |
|---|---|---|
| Worldwide public domain | CC0, Public Domain Mark, plain "public domain" | **ACCEPT** (no attribution legally required; capture provenance anyway) |
| Attribution licenses | CC-BY, CC-BY-SA | **ACCEPT** — attribution required; BY-SA records share-alike and ships `sourced` (human sign-off) |
| CC Sampling+ | sampling | **REJECT** — not a clean reuse license for a full bed |
| Not a license | "no known copyright restrictions" / Flickr Commons | **REJECT** — provenance uncertain |
| Jurisdiction-limited | "No Copyright – United States" (NoC-US) | **REJECT** — not cleared worldwide |
| NC / ND | any non-commercial or no-derivatives variant | **REJECT** — monetized + composited video is commercial *and* derivative |
| Unknown / missing / all-rights-reserved | — | **REJECT** |

## `status` — the values, precisely
- **`cleared`** — accept-list license **and** complete attribution **and** a local file.
  Baked into the master.
- **`sourced`** — a real, licensed file on disk but clearance is incomplete (a CC-BY-SA
  bed whose share-alike a human must sign off). Surfaced, flagged.
- **`placeholder`** — nothing cleared; a flagged local stand-in, **excluded from the
  master**. The honest signal lives in `status` + `flag`.
- The narration track is `cleared` by construction (engine-synthesized TTS).

## The hard invariants (enforced in code — rely on them)
1. `total_duration_sec` agrees across transcript, manifest, and the master trim length.
2. Transcript segments carry `scene_no` + GLOBAL cumulative `start_sec`/`end_sec`.
3. The narration track's `uri` is the muxed audio; `vo_uri`/`master_uri` are recorded.
4. Every recorded music/SFX track comes from an allowlisted source **or** the CC0 kit.
5. **No music/SFX track ships without license + attribution** (engine raises otherwise).
6. Nothing uncleared is in the master; a placeholder bed is in the manifest but not the
   mix recipe.
7. The mix obeys the discipline: VO authoritative, bed `ducking:"narration"`, one accent.

## Deterministic vs LLM-assisted (the boundary)
- **LLM-assisted (curation only):** mood → music search query. Degrades to a
  deterministic keyword map offline.
- **Deterministic (everything that touches the mix):** the license truth table,
  ranking, clearance, scene-offset math, the manifest shape, the gains/ducking values,
  the FFmpeg filtergraph, the SFX anchor (`at_sec`), the master trim.

## Your output contracts
Emit plain dicts (Atlas stamps `schema_version` + validates). The transcript:
`{schema_version, total_duration_sec, segments:[{scene_no, start_sec, end_sec, text,
words?}]}`. The manifest: `{schema_version, total_duration_sec, master_uri, vo_uri,
tracks:[{role, uri, gain_db, ducking, license, attribution, status, …}], wired_into,
mix}`. The **Composition Engineer (#6)** reads the transcript to time captions and
muxes the narration track (the master) into the video.

## System prerequisites (distinct from pip deps — see requirements.txt)
- **Node 22+** and the HyperFrames CLI (`npx hyperframes`); `kokoro-onnx` + `soundfile`
  Python packages for tts (in requirements.txt).
- **FFmpeg + FFprobe** for concat, the mix, and probing.
- **whisper.cpp** — OPTIONAL, only for word-level transcript enrichment. Absent by
  default; the deterministic per-scene transcript never needs it.
