"""studio.gate.parse — static structural parsing of a composed index.html, shared by the
motion_variety and content_fidelity dimensions. Regex-based (the compositions are
machine-authored with a stable shape), tolerant of missing pieces."""
from __future__ import annotations

import re

_SECTION_RE = re.compile(
    r'<section\b[^>]*\bid="(?P<id>s\d+)"[^>]*\bclass="[^"]*\bscene\b[^"]*"[^>]*>'
    r'(?P<body>.*?)</section>', re.DOTALL | re.IGNORECASE)

# Beat/layout tokens we recognize in the choreography + markup. Order = priority.
_BEAT_TOKENS = [
    ("count-up", r'count-host|countUp|count-up'),
    ("orbit", r'makeOrbitCluster|class="[^"]*orbit'),
    ("bell", r'\bbell\b|notif'),
    ("quote-cards", r'quoteCards|class="[^"]*cards'),
    ("shatter", r'shatter|crumble'),
    ("drain", r'grayscale|drain'),
    ("checklist", r'checklist|checkmark'),
    ("strike", r'strike|strikethrough'),
    ("signature", r'signature|writeOn'),
    ("underline", r'makeOutlineDraw|underline'),
]


def scene_blocks(html: str) -> list[dict]:
    out = []
    for m in _SECTION_RE.finditer(html or ""):
        sid = m.group("id")
        out.append({"scene_no": int(sid[1:]), "id": sid, "html": m.group("body")})
    return out


def scene_signature(block_html: str, choreo_js: str, sid: str = "") -> str:
    """A stable token for the scene's beat. Looks in BOTH the scene markup and the
    composition's choreography script (beats are wired by `#sid` selector there).

    ``sid`` should be passed explicitly (e.g. ``b["id"]`` from ``scene_blocks``)
    because ``block_html`` is the inner body of the <section> tag and does not
    contain the opening tag where ``id="sN"`` lives.  When ``sid`` is empty the
    function falls back to a best-effort regex extraction from ``block_html`` so
    that direct callers without a known sid still degrade gracefully.
    """
    if not sid:
        sid_m = re.search(r'id="(s\d+)"', block_html)
        sid = sid_m.group(1) if sid_m else ""
    # choreography lines that mention this scene id
    scoped = "\n".join(l for l in (choreo_js or "").splitlines() if f"#{sid} " in l or f'#{sid}"' in l)
    hay = block_html + "\n" + scoped
    for name, pat in _BEAT_TOKENS:
        if re.search(pat, hay, re.IGNORECASE):
            return name
    return "plain"


def normalize_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def is_attributed_quote(s: str) -> bool:
    s = s or ""
    has_quote = ('"' in s) or ("“" in s) or ("”" in s)
    has_attrib = bool(re.search(r"[—\-–]\s*[A-Z][a-z]", s))
    return has_quote and has_attrib
