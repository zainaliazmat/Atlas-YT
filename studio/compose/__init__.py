"""studio.compose — the Composer (Phase 3).

Authors ONE self-contained, deterministic, seekable HyperFrames ``index.html``
for a project — the real composition, NOT a JSON spec for a downstream renderer.
It mirrors how reference/dark-truth-social/index.html was authored.

Pipeline: load pack + script + brief → materialize pack fonts/filters/base.css +
tokens → obtain per-scene visuals from the Asset Library → build one
``<section class="scene clip">`` per scene → author bespoke per-scene GSAP on the
pack's re-timer proxy, with transitions + ticker on the real timeline → write
index.html. New reusable beats are saved to the pack's motion-library and
registered (the compounding policy).

Determinism + HyperFrames key rules: every timed element is ``class="clip"`` with
data-start/duration/track-index; the timeline is paused + registered on
``window.__timelines``; no Math.random/Date.now/fetch.
"""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from .. import config
from ..packs import load_pack
from . import _css, _motion
from ._captions import group_captions

GSAP_CDN = "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"
SEAM_OVERLAP = 0.5  # adjacent scene windows overlap so transitions cover the seam
# Seam verbs that keep BOTH scenes within the canvas at every clip edge (opacity
# fade + tiny shift / paper flash), so frame-seek inspection never catches a
# scene translated off-frame. The green swipe still covers the seam.
_TX_VERBS = ["tile", "cut", "tile", "cut*", "tile", "cut", "tile", "cut"]


class ComposeError(Exception):
    pass


def _read_json(path: Path, default=None):
    import json
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(x) -> str:
    """Format a timing/volume number the way the golden reference does: a whole
    number loses its trailing ``.0`` (``0.0`` -> ``"0"``, ``5.0`` -> ``"5"``), anything
    else keeps up to 3 dp (``5.867`` -> ``"5.867"``)."""
    x = round(float(x), 3)
    return str(int(x)) if x == int(x) else str(x)


_BUSY_LOWER_CENTER = ("social", "platform", "app", "feed", "scroll", "media")


def _is_lower_center_busy(scene: dict) -> bool:
    """A scene whose lower-centre is occupied (a numeric stat count-up, or an
    orbit/feed beat) — its captions should drop to the ``.vo-cap-low`` position so they
    never collide with that content. Content-derived, so it's independent of whether
    brand icons were actually sourced."""
    ost = scene.get("on_screen_text") or ""
    narr = scene.get("narration") or ""
    if _num(ost or narr):
        return True
    text = f"{ost} {narr}".lower()
    return any(w in text for w in _BUSY_LOWER_CENTER)


def _num(text: str):
    """Find the first number + unit in text → (target, dec, suffix) or None."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%]+)?", text or "")
    if not m:
        return None
    raw, unit = m.group(1).replace(",", ""), (m.group(2) or "")
    dec = len(raw.split(".")[1]) if "." in raw else 0
    suffix = unit if unit in ("%", "x") else (f" {unit}" if unit else "")
    try:
        return float(raw), dec, suffix
    except ValueError:
        return None


class Composer:
    """Authors one index.html for a project against a Design Pack."""

    def __init__(self, slug: str, pack_id: str):
        self.slug = slug
        self.pack = load_pack(pack_id)
        self.pdir = config.PROJECTS_DIR / slug
        self.assets_dir = self.pdir / "assets"
        self.tokens = self.pack.tokens
        self.colors = self.tokens.get("colors", {})
        ad = self.pack.manifest.get("aspect_defaults", {})
        self.width = int(ad.get("width", config.DEFAULT_WIDTH))
        self.height = int(ad.get("height", config.DEFAULT_HEIGHT))
        self.fps = int(self.tokens.get("motion", {}).get("fps", config.DEFAULT_FPS))
        self.spray = self.colors.get("spray", "#2e5e1f")
        self.ink = self.colors.get("ink", "#1f1f1e")
        self._inlines: list[str] = []  # extra JS module sources to inline

    # --- asset materialization ----------------------------------------------
    def _materialize(self, ref) -> str | None:
        """Copy a library file asset into the project assets/ and return a
        composition-relative path. Returns None if there's no file payload."""
        if ref is None or ref.embed not in ("img", "audio", "font_face"):
            return None
        src = config.ASSET_LIBRARY_DIR / ref.entry["file"]
        if not src.is_file():
            return None
        sub = {"img": "img", "audio": "audio", "font_face": "fonts"}[ref.embed]
        dest_dir = self.assets_dir / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if not dest.exists():
            shutil.copyfile(src, dest)
        return f"assets/{sub}/{dest.name}"

    def _obtain(self, kind, tags, constraints=None):
        from ..library import generate
        try:
            return generate.obtain(kind, tags, constraints)
        except Exception:
            return None

    def _snippet(self, name: str) -> str | None:
        """Obtain a procedural snippet and remember its source to inline once."""
        ref = self._obtain("snippet", [name])
        if ref is None:
            return None
        if ref.payload not in self._inlines:
            self._inlines.append(ref.payload)
        return ref.factory

    # --- fonts ---------------------------------------------------------------
    def _fontfaces(self) -> str:
        from ..library import list_assets
        css = []
        fonts = {f.get("family") for f in self.pack.manifest.get("fonts", [])}
        for fam in sorted(x for x in fonts if x):
            entry = next((e for e in list_assets(kind="font") if e.get("family") == fam), None)
            if not entry:
                continue
            # fake an AssetRef-like for materialize
            from types import SimpleNamespace
            rel = self._materialize(SimpleNamespace(embed="font_face", entry=entry))
            if rel:
                css.append(
                    f'@font-face {{ font-family: "{fam}"; '
                    f'src: url("{rel}"); font-display: block; }}'
                )
        return "\n      ".join(css)

    # --- archetype resolution ------------------------------------------------
    def _archetype_for(self, scene: dict) -> str:
        """Resolve the archetype for a scene.

        Resolution order:
          1. If ``self._storyboard`` has a tag for ``scene['scene_no']``, return it
             (the Iris-supplied storyboard tag wins).
          2. Otherwise delegate to ``archetypes.classify(scene)`` (heuristic fallback).

        ``self._storyboard`` is ``{str(scene_no): archetype}`` and is populated by
        ``author()`` from ``storyboard.json``; it may be ``None`` when the file is absent
        (legacy projects) — in that case the fallback always applies.
        """
        from . import archetypes
        sb = self._storyboard or {}
        tag = sb.get(str(scene.get("scene_no", "")))
        if tag:
            return tag
        return archetypes.classify(scene)

    # --- build ---------------------------------------------------------------
    def author(self) -> Path:
        brief = _read_json(self.pdir / "research_brief.json", {})
        script = _read_json(self.pdir / "script.json", {})
        scenes = script.get("scenes") or []
        if not scenes:
            raise ComposeError("script has no scenes")

        # Load storyboard tags once (tag wins; absent file → empty map → classify fallback).
        # Tolerates absence so legacy projects compose without a storyboard stage.
        sb_raw = _read_json(self.pdir / "storyboard.json", None)
        if sb_raw is not None:
            self._storyboard = {
                str(s["scene_no"]): s["archetype"]
                for s in (sb_raw.get("scenes") or [])
                if "scene_no" in s and "archetype" in s
            }
        else:
            self._storyboard = {}

        # save/refresh the pack's reusable beats (compounding policy)
        beats = _motion.ensure_motion_library(self.pack)
        for src in beats.values():
            self._inlines.append(src)

        # The authored NOMINAL grid (round scene boundaries) from duration_est_sec.
        old_durs = [max(2.0, float(s.get("duration_est_sec") or 6)) for s in scenes]
        old_starts, t = [], 0.0
        for d in old_durs:
            old_starts.append(round(t, 3))
            t += d
        old_total = round(t, 3)

        # VO-LOCK (GOLDEN_REFERENCE.md §2): when studio.vo has produced vo.grid.json,
        # conform the composition to the real VO — scene WINDOWS become NS/ND and the
        # choreography (authored against OS/OD) auto-fits via the re-timer proxy. Absent
        # it, new == old (the provisional grid) so this stays backward-compatible.
        vo_grid = _read_json(self.pdir / "vo.grid.json", None)
        g = (vo_grid or {}).get("grid") or {}
        OS = [round(float(x), 3) for x in g.get("OS", old_starts)]
        OD = [round(float(x), 3) for x in g.get("OD", old_durs)]
        if g.get("NS") and g.get("ND"):
            NS = [round(float(x), 3) for x in g["NS"]]
            ND = [round(float(x), 3) for x in g["ND"]]
            total = round(float(g.get("total", old_total)), 3)
            sec_durs = ND  # the VO window already overlaps the next seam by the tail
        else:
            NS, ND = old_starts, old_durs
            total = old_total
            sec_durs = [round(d + SEAM_OVERLAP, 3) for d in old_durs]  # provisional overlap

        def _at(arr, i, fallback):
            return arr[i] if i < len(arr) else fallback

        plans = [self._plan_scene(i, s, _at(OS, i, old_starts[i]), _at(OD, i, old_durs[i]),
                                  _at(NS, i, old_starts[i]), _at(sec_durs, i, old_durs[i]))
                 for i, s in enumerate(scenes)]

        sections = "\n".join(p["html"] for p in plans)
        choreo = self._choreography(plans, OS, OD, NS, ND, total, script)
        audio = (self._audio_from_manifest(vo_grid)
                 if (vo_grid and vo_grid.get("audio")) else self._audio_html(total))
        captions = self._captions_html(vo_grid, scenes) if vo_grid else ""

        css = self._build_style()
        filters = self.pack.read_partial("filters")
        comp_id = self.slug

        doc = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width={self.width}, height={self.height}">
    <script src="{GSAP_CDN}"></script>
    <style>
{css}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="{comp_id}" data-start="0" data-duration="{total}" data-width="{self.width}" data-height="{self.height}">
{filters}
{sections}

      <div class="grain" data-layout-ignore="" aria-hidden="true">
        <svg width="100%" height="100%" preserveAspectRatio="none"><rect width="100%" height="100%" filter="url(#grain-noise)" /></svg>
      </div>
      <div class="tx-swipe" data-layout-ignore="" aria-hidden="true"></div>
      <div class="tx-paper" data-layout-ignore="" aria-hidden="true"></div>
      <div class="tx-flash" data-layout-ignore="" aria-hidden="true"></div>
      <div class="ticker-band" data-layout-ignore="" aria-hidden="true"><div class="ticker-track" data-layout-ignore=""></div></div>
{audio}
{captions}
      <script>
{choreo}
      </script>
    </div>
  </body>
</html>
"""
        out = self.pdir / "index.html"
        out.write_text(doc, encoding="utf-8")
        return out

    def _build_style(self) -> str:
        ff = self._fontfaces()
        root_vars = "\n        ".join(f"--{k}: {v};" for k, v in self.colors.items())
        base = self.pack.read_partial("base_css")
        comp = _css.composition_css(self.tokens, self.width, self.height)
        return (
            f"      /* fonts */\n      {ff}\n"
            f"      :root {{\n        {root_vars}\n      }}\n"
            f"      /* pack base.css */\n{base}\n"
            f"      /* composition shell */\n{comp}"
        )

    # --- per-scene plan ------------------------------------------------------
    def _plan_scene(self, i: int, scene: dict, author_start: float, author_dur: float,
                    sec_start: float, sec_dur: float) -> dict:
        """Build one scene. ``author_start/dur`` is the OLD nominal grid the GSAP is
        written against (remapped by the re-timer proxy); ``sec_start/sec_dur`` is the
        NEW (VO-driven) window the ``.clip`` is actually visible for."""
        sid = f"s{i + 1}"
        ti = 1 if i % 2 == 0 else 3

        from . import _content
        lead_html = _content.render_on_screen_text(
            scene.get("on_screen_text") or scene.get("point") or "")
        claims_html = _content.render_claims(scene)

        beat, extra_html = self._scene_beat(i, scene, author_start)
        # the .clip window is the NEW VO window (it already overlaps the next seam)
        sec = (
            f'      <section id="{sid}" class="scene clip" data-start="{_fmt(sec_start)}" '
            f'data-duration="{_fmt(sec_dur)}" data-track-index="{ti}">\n'
            f'        <div class="scene-content">\n'
            f'          <div class="label faint mono anim">FIELD REPORT // FIG. {i+1:02d}</div>\n'
            f'{lead_html}\n'
            f'{claims_html}\n'
            f'{extra_html}'
            f'        </div>\n'
            f'        <div class="fx" data-layout-ignore="" aria-hidden="true"></div>\n'
            f'      </section>'
        )
        return {"sid": sid, "i": i, "start": author_start, "dur": author_dur,
                "beat": beat, "scene": scene, "html": sec}

    def _scene_beat(self, i: int, scene: dict, author_start: float) -> tuple[dict, str]:
        """Decide the bespoke beat for a scene from its content. Returns
        (beat_descriptor, extra_scene_html).

        When ``archetypes.REGISTRY`` has a builder for the resolved archetype,
        that builder is called and its result is used.  Otherwise the existing
        generic beat logic is applied unchanged — so this method is safe while
        REGISTRY is empty (Phase B3) and becomes progressively richer as Phase C
        builders land.
        """
        from . import archetypes
        sid = f"s{i + 1}"
        arch = self._archetype_for(scene)
        if arch in archetypes.REGISTRY:
            ctx = {
                "sid": sid,
                "spray": self.spray,
                "ink": self.ink,
                "width": self.width,
                "height": self.height,
                "at": round(author_start + 0.6, 3),
            }
            result = archetypes.REGISTRY[arch](scene, ctx)
            beat = {"kind": arch, "token": result.get("token", arch),
                    "beats_js": result.get("beats_js", "")}
            return beat, result.get("html", "")        # html only -> section body

        # --- existing generic beat logic (unchanged) -------------------------
        text = f"{scene.get('on_screen_text','')} {scene.get('narration','')}".lower()
        num = _num(scene.get("on_screen_text") or scene.get("narration") or "")
        # numeric scene -> count-up stat
        if num:
            target, dec, suffix = num
            small = html.escape((scene.get("point") or "").upper()[:18] or "FIGURE")
            extra = (
                f'          <div class="row anim"><div class="stat">'
                f'<span class="count-host" data-count="{target}" data-dec="{dec}" data-suffix="{suffix}"></span>'
                f'<small>{small}</small></div></div>\n'
            )
            return {"kind": "count-up", "target": target, "dec": dec, "suffix": suffix}, extra
        # platforms scene -> orbit cluster of brand icons
        if any(w in text for w in ("social", "platform", "app", "feed", "scroll", "media")):
            icons = self._brand_icon_svgs()
            if icons:
                return {"kind": "orbit", "icons": icons}, ""
        # first scene -> notification bell
        if i == 0:
            return {"kind": "bell"}, ""
        # otherwise -> a spray underline self-draw under the hero line
        return {"kind": "underline"}, ""

    def _brand_icon_svgs(self) -> list[str]:
        out = []
        for name in ("x", "facebook", "instagram", "tiktok", "youtube"):
            ref = self._obtain("icon", [name, "brand"])
            if ref and ref.embed == "svg":
                out.append(ref.payload)
        return out

    # --- choreography (the GSAP program) ------------------------------------
    def _choreography(self, plans, os_arr, od_arr, ns_arr, nd_arr, total, script) -> str:
        spray = self.spray
        # ensure the snippets we reference are inlined
        count_factory = self._snippet("count-up")
        bell_factory = self._snippet("bell")

        OS = "[" + ", ".join(_fmt(s) for s in os_arr) + "]"
        OD = "[" + ", ".join(_fmt(d) for d in od_arr) + "]"
        NS = "[" + ", ".join(_fmt(s) for s in ns_arr) + "]"
        ND = "[" + ", ".join(_fmt(d) for d in nd_arr) + "]"
        partials = (
            self.pack.read_partial("retimer") + "\n"
            + self.pack.read_partial("transitions") + "\n"
            + self.pack.read_partial("ticker") + "\n"
            + "\n".join(self._inlines)
        )

        # hero reveals: one per scene at start+0.3
        hero = "\n".join(
            f'        HERO("#{p["sid"]} .lead", {round(p["start"] + 0.3, 3)});'
            for p in plans
        )
        # supporting .anim rise-in per scene
        anim = "\n".join(
            f'        tl.from("#{p["sid"]} .anim", {{ y: 44, opacity: 0, duration: 0.6, stagger: 0.1, ease: "power3.out", overwrite: "auto" }}, {round(p["start"] + 0.55, 3)});'
            for p in plans
        )
        # per-scene bespoke beats
        beats_js = "\n".join(self._beat_js(p, count_factory, bell_factory) for p in plans)

        # transitions on the real timeline + boundary map
        tx_map = []
        for b in range(1, len(plans)):
            verb = _TX_VERBS[(b - 1) % len(_TX_VERBS)]
            if verb == "cut*":
                tx_map.append(f"        T.txCut({b}, true);")
            elif verb == "cut":
                tx_map.append(f"        T.txCut({b}, false);")
            else:
                tx_map.append(f"        T.tx{verb.capitalize()}({b});")
        tx_map_js = "\n".join(tx_map)

        ticker_labels = "[" + ", ".join(
            '"' + html.escape((p["scene"].get("beat") or p["scene"].get("point") or f"SCENE {p['i']+1}").upper()[:16]).replace('"', "") + '"'
            for p in plans
        ) + "]"

        return f"""        window.__timelines = window.__timelines || {{}};
        var SPRAY = "{spray}";

        // --- inject the poster metadata into every scene (deterministic) ---
        document.querySelectorAll("#root .scene").forEach(function (sc, i) {{
          var n = String(i + 1).padStart(2, "0");
          var vtag = document.createElement("div");
          vtag.className = "v-tag mono"; vtag.setAttribute("data-layout-ignore", "");
          vtag.textContent = "FIELD REPORT · {html.escape((script.get('working_title') or self.slug)).upper()[:48]}";
          sc.appendChild(vtag);
          var ticks = document.createElement("div");
          ticks.className = "reg-ticks"; ticks.setAttribute("data-layout-ignore", "");
          ticks.innerHTML = '<svg width="58" height="58" viewBox="0 0 58 58" fill="none" stroke="currentColor" stroke-width="1.25">'
            + '<line x1="29" y1="5" x2="29" y2="22"/><line x1="29" y1="36" x2="29" y2="53"/>'
            + '<line x1="5" y1="29" x2="22" y2="29"/><line x1="36" y1="29" x2="53" y2="29"/>'
            + '<circle cx="29" cy="29" r="9"/></svg>';
          sc.appendChild(ticks);
          var credit = document.createElement("div");
          credit.className = "credit mono"; credit.setAttribute("data-layout-ignore", "");
          credit.innerHTML = '<span class="l">© FIELD REPORT</span><span class="l">FIG. ' + n + '</span>';
          sc.appendChild(credit);
        }});

{partials}

        // --- timing grids: OS/OD = authored nominal grid, NS/ND = real VO windows ---
        var OS = {OS}, OD = {OD};
        var NS = {NS}, ND = {ND};
        var TOTAL = {total};
        var tl = makeRetimer(OS, OD, NS, ND);
        var tlReal = tl.real;

        // --- kinetic typography: split a hero line into word units + reveal ---
        function splitWords(el) {{
          if (!el) return [];
          var units = [], frag = document.createDocumentFragment();
          Array.from(el.childNodes).forEach(function (node) {{
            if (node.nodeType === 3) {{
              node.textContent.split(/(\\s+)/).forEach(function (tok) {{
                if (tok.length === 0) return;
                if (/^\\s+$/.test(tok)) {{ frag.appendChild(document.createTextNode(" ")); }}
                else {{ var w = document.createElement("span"); w.className = "word"; w.textContent = tok; frag.appendChild(w); units.push(w); }}
              }});
            }} else {{ node.classList.add("word"); frag.appendChild(node); units.push(node); }}
          }});
          el.replaceChildren(frag);
          return units;
        }}
        function HERO(sel, at) {{
          splitWords(document.querySelector(sel)).forEach(function (w, i) {{
            tl.fromTo(w, {{ clipPath: "inset(0% 100% 0% 0%)", opacity: 0, y: 12 }},
              {{ clipPath: "inset(0% 0% 0% 0%)", opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }}, at + i * 0.08);
          }});
        }}
{hero}

        // --- supporting elements rise in ---
{anim}

        // --- bespoke per-scene beats ---
{beats_js}

        // --- texture is alive: grain drift + breathe + scale (real timeline) ---
        tlReal.to(".grain", {{ x: 70, y: -45, duration: TOTAL, ease: "none" }}, 0);
        tlReal.to(".grain", {{ opacity: 0.105, duration: 3.6, ease: "sine.inOut", yoyo: true, repeat: Math.ceil(TOTAL / 3.6) }}, 0);
        tlReal.to(".grain", {{ scale: 1.035, duration: 5.2, ease: "sine.inOut", yoyo: true, repeat: Math.ceil(TOTAL / 5.2), transformOrigin: "50% 50%" }}, 0);
        document.querySelectorAll(".reg-ticks").forEach(function (tk, i) {{
          tlReal.to(tk, {{ keyframes: [
            {{ opacity: 0.55, duration: 0.09 }}, {{ opacity: 0.92, duration: 0.05 }}, {{ opacity: 0.66, duration: 0.13 }},
            {{ opacity: 1.0, duration: 0.04 }}, {{ opacity: 0.5, duration: 0.1 }}, {{ opacity: 0.85, duration: 0.17 }}
          ], ease: "steps(1)", repeat: Math.ceil(TOTAL / 0.58) }}, (i % 6) * 0.21);
        }});

        // --- transitions (overlap each seam) + ticker through-line ---
        var T = makeTransitions({{
          tl: tlReal,
          swipe: document.querySelector(".tx-swipe"),
          flash: document.querySelector(".tx-flash"),
          paper: document.querySelector(".tx-paper"),
          boundaryTime: function (b) {{ return NS[b]; }},
          swipeColor: SPRAY
        }});
{tx_map_js}
        makeTicker({{ tl: tlReal, track: document.querySelector(".ticker-track"),
          labels: {ticker_labels}, duration: TOTAL, accentColor: SPRAY }});

        window.__timelines["{self.slug}"] = tlReal;"""

    def _beat_js(self, p, count_factory, bell_factory) -> str:
        beat = p["beat"]
        if beat.get("beats_js"):
            return beat["beats_js"]
        sid, start, beat = p["sid"], p["start"], p["beat"]
        at = round(start + 0.6, 3)
        kind = beat["kind"]
        if kind == "count-up" and count_factory:
            return (
                f'        (function () {{ var h = document.querySelector("#{sid} .count-host"); if (h) '
                f'{count_factory}({{ tl: tl, mount: h, at: {at}, color: SPRAY, target: {beat["target"]}, '
                f'dec: {beat["dec"]}, suffix: "{beat["suffix"]}", duration: 1.5 }}); }})();'
            )
        if kind == "bell" and bell_factory:
            return (
                f'        {bell_factory}({{ tl: tl, mount: "#{sid} .fx", at: {at}, color: "var(--ink)", size: 96 }});'
            )
        if kind == "orbit":
            items = "[" + ", ".join("'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'" for s in beat["icons"]) + "]"
            return (
                f'        (function () {{ var fx = document.querySelector("#{sid} .fx"); if (fx) {{'
                f' var oh = document.createElement("div");'
                f' oh.style.cssText = "position:absolute;left:50%;top:150px;width:320px;height:320px;transform:translateX(-50%);pointer-events:none";'
                f' fx.appendChild(oh);'
                f' makeOrbitCluster({{ tl: tl, mount: oh, at: {at}, color: SPRAY, radius: 120, node: 58, items: {items} }}); }} }})();'
            )
        # underline self-draw under the hero line
        return (
            f'        makeOutlineDraw({{ tl: tl, mount: "#{sid} .fx", at: {at}, color: SPRAY, '
            f'd: "M120 880 C 600 840, 1320 900, 1800 870", viewBox: "0 0 1920 1080", '
            f'width: {self.width}, height: {self.height}, strokeWidth: 6, dur: 1.2 }});'
        )

    def _captions_html(self, vo_grid: dict, scenes: list) -> str:
        """Burn in whisper-synced captions from vo.words.json as a ROOT-level
        ``.vo-cap-layer`` (outside the scenes, so it survives cuts and never overlaps a
        scene's metadata block). Each phrase is a ``.vo-cap clip`` on its own track
        index (2), ``data-layout-ignore``. Scenes whose lower-centre is busy (a stat
        count-up or an orbit/feed beat) get the ``.vo-cap-low`` variant."""
        words_rel = vo_grid.get("words_json")
        if not words_rel:
            return ""
        wpath = self.pdir / words_rel
        if not wpath.is_file():
            return ""
        try:
            words = _read_json(wpath, [])
        except Exception:
            return ""
        phrases = group_captions(words)
        if not phrases:
            return ""

        NS = (vo_grid.get("grid") or {}).get("NS") or []
        vo_scenes = vo_grid.get("scenes") or []
        busy = [_is_lower_center_busy(s) for s in scenes]
        cuts = [round(NS[i] + float(vo_scenes[i].get("vo_dur", 0)), 3)
                if i < len(vo_scenes) else NS[i] for i in range(len(NS))]

        def _scene_of(mid: float) -> int:
            for i in range(len(NS)):
                last = i == len(NS) - 1
                if NS[i] <= mid < cuts[i] or (last and mid >= NS[i]):
                    return i
            return 0

        rows = []
        for p in phrases:
            mid = p["start"] + p["duration"] / 2.0
            i = _scene_of(mid)
            low = busy[i] if i < len(busy) else False
            cls = "vo-cap clip vo-cap-low" if low else "vo-cap clip"
            rows.append(
                f'        <div class="{cls}" data-start="{_fmt(p["start"])}" '
                f'data-duration="{_fmt(p["duration"])}" data-track-index="2" '
                f'data-layout-ignore="">{html.escape(p["text"])}</div>'
            )
        return ('      <div class="vo-cap-layer" data-layout-ignore="" aria-hidden="true">\n'
                + "\n".join(rows) + "\n      </div>")

    def _audio_from_manifest(self, vo_grid: dict) -> str:
        """Emit the VO-driven audio layer authored by studio.vo: per-scene VO on
        ALTERNATING track indices (so adjacent VO overlaps across a seam), a ducked
        bed on its own track, and SFX on the transition beats. Each row is a separate
        ``<audio>`` clip (key rule: media is muted video + separate audio)."""
        rows = []
        for k, a in enumerate(vo_grid.get("audio", [])):
            rows.append(
                f'      <audio id="aud-{k + 1}" src="{a["src"]}" '
                f'data-start="{_fmt(a["start"])}" data-duration="{_fmt(a["dur"])}" '
                f'data-track-index="{int(a["track"])}" data-volume="{_fmt(a.get("volume", 1))}"></audio>'
            )
        return "\n".join(rows)

    def _audio_html(self, total: float) -> str:
        """Music bed (mood-matched) + a whoosh SFX on each seam, as separate
        <audio> clips (key rule: media is muted video + separate audio)."""
        rows = []
        bed = self._obtain("music", ["bed"], {"mood": ["dark", "hopeful"]})
        rel = self._materialize(bed) if bed else None
        if rel:
            rows.append(
                f'      <audio id="aud-bed" src="{rel}" data-start="0" data-duration="{total}" data-track-index="8" data-volume="1"></audio>'
            )
        sfx = self._obtain("sfx", ["whoosh"])
        srel = self._materialize(sfx) if sfx else None
        if srel:
            # one accent on the first seam; provisional until VO beats land
            rows.append(
                f'      <audio id="aud-sfx-1" src="{srel}" data-start="0.6" data-duration="0.6" data-track-index="7" data-volume="0.5"></audio>'
            )
        return "\n".join(rows)


def compose(slug: str, *, pack_id: str) -> Path:
    """Author studio/projects/<slug>/index.html. Returns the path."""
    return Composer(slug, pack_id).author()
