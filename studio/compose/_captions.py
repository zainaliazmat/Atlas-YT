"""studio.compose._captions — group vo.words.json into lower-third caption phrases.

The word-level transcript (``vo.words.json``: ``[{id,text,start,end}]`` on the global
timeline) is grouped into short, readable phrases — broken at sentence/clause
punctuation and bounded in word-count / duration — each becoming one ``.vo-cap clip``
div (GOLDEN_REFERENCE.md §8). Pure + deterministic: no I/O, no clock.
"""

from __future__ import annotations

# Strong sentence enders always close a caption; soft clause marks close it only once
# the phrase has enough words to stand alone (so we don't fragment on every comma).
_STRONG = ".!?…"
_SOFT = ",;:—"


def _ends_with(text: str, chars: str) -> bool:
    t = text.rstrip("\"')")  # ignore trailing quotes/brackets
    return bool(t) and t[-1] in chars


def group_captions(words, *, max_words: int = 7, max_dur: float = 2.6,
                   max_gap: float = 0.6, min_gap: float = 0.08,
                   min_dur: float = 0.25) -> list[dict]:
    """Group word entries into caption phrases. Returns ``[{text, start, duration}]``
    covering every word once, in order. A phrase closes when the current word ends a
    sentence (strong punctuation), or ends a clause (soft punctuation) once it already
    holds >=3 words, or the phrase hits ``max_words`` / ``max_dur``, or the gap to the
    next word exceeds ``max_gap``. Adjacent phrases are trimmed to leave at least
    ``min_gap`` between them so they never overlap on their shared caption track."""
    words = [w for w in (words or []) if str(w.get("text", "")).strip()]
    phrases: list[dict] = []
    cur: list[dict] = []

    def flush():
        if not cur:
            return
        phrases.append({
            "text": " ".join(str(w["text"]).strip() for w in cur),
            "start": round(float(cur[0]["start"]), 3),
            "duration": round(float(cur[-1]["end"]) - float(cur[0]["start"]), 3),
        })
        cur.clear()

    for i, w in enumerate(words):
        cur.append(w)
        text = str(w["text"]).strip()
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (float(nxt["start"]) - float(w["end"])) if nxt else 0.0
        span = float(w["end"]) - float(cur[0]["start"])
        if (_ends_with(text, _STRONG)
                or (_ends_with(text, _SOFT) and len(cur) >= 3)
                or len(cur) >= max_words
                or span >= max_dur
                or gap > max_gap):
            flush()
    flush()

    # Trim adjacent phrases so they never overlap on the shared caption track: each
    # phrase must end at least `min_gap` before the next one starts.
    for a, b in zip(phrases, phrases[1:]):
        latest_end = b["start"] - min_gap
        if a["start"] + a["duration"] > latest_end:
            a["duration"] = round(max(min_dur, latest_end - a["start"]), 3)
    return phrases
