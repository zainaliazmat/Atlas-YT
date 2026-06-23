"""atlas.eval.tracking — append-only, crash-safe store of evaluation results.

One ROW per (run_id, artifact, stage, prop) measurement. The Inspector/roll-up
produces these rows; this store persists and queries them so that:

  * improvement is AUDITABLE — every eval pass is a permanent, append-only record;
  * the JUDGED NOISE FLOOR is measurable — run the held-out set K>=5 times under
    one change_id and read back the variance of a property's measured_value.

Storage format: JSONL (one JSON object per line) at
``atlas/eval/runs/eval_runs.jsonl`` by default. JSONL is chosen over a single
JSON document precisely because it is APPEND-friendly: a new run never has to
rewrite (and therefore never risks corrupting) the rows already on disk.

Crash-safety / atomicity philosophy (mirrors atlas/chat_state.py, adapted from
"atomic replace of a whole file" to "atomic-ish append of a batch"):

  * A whole ``record_run`` batch is serialized in memory first, then written to
    the open file with a SINGLE ``f.write`` followed by ``f.flush()`` +
    ``os.fsync()``. Building the bytes up-front means a serialization error
    (e.g. a non-JSON value) raises BEFORE any partial line touches disk.
  * We open in APPEND mode (``"a"``), so prior rows are physically untouched —
    an append cannot lose or rewrite earlier runs.
  * Reads are TOLERANT: ``all_rows`` skips blank/garbage/half-written lines and
    never raises. A torn tail line (process killed mid-fsync of the *next*
    batch) is simply ignored; every complete line before it survives.

Variance definition: ``noise_floor`` reports POPULATION variance/std
(``statistics.pvariance`` / ``statistics.pstdev``). The K runs are treated as the
complete set of observations we have for that property, not a sample of a larger
population — we want the spread of exactly these measurements.

Dependency-free: only json, os, pathlib, time, statistics from the stdlib.
No global mutable state — each TrackingStore is bound to its own path.
"""
from __future__ import annotations

import json
import os
import pathlib
import statistics
import time
from typing import Any

# Default log location. NOTE: the parent ``runs/`` dir holds GENERATED eval data
# and should not be committed — it is created lazily on first write, never at
# import time, so importing this module has no filesystem side effects.
DEFAULT_PATH = pathlib.Path(__file__).resolve().parent / "runs" / "eval_runs.jsonl"


def _zero_floor() -> dict[str, Any]:
    """The noise_floor() result for an empty/unmeasurable property."""
    return {
        "n": 0,
        "mean": 0.0,
        "variance": 0.0,
        "std": 0.0,
        "min": 0.0,
        "max": 0.0,
        "values": [],
    }


class TrackingStore:
    """Append-only JSONL store of per-property evaluation rows."""

    def __init__(self, path: str | pathlib.Path | None = None) -> None:
        self.path = pathlib.Path(path) if path is not None else DEFAULT_PATH

    # ----------------------------------------------------------------- write
    def record_run(
        self,
        rows: list[dict],
        *,
        run_id: str,
        change_id: str = "baseline",
        ts: float | None = None,
    ) -> int:
        """Stamp + append a batch of rows for one evaluation pass; return count.

        Precedence for run_id / change_id / ts: a value ALREADY SET on the row
        wins over the kwarg (the row's own provenance is authoritative); the
        kwarg only fills a missing/empty/None value. ``ts`` defaults to
        ``time.time()`` when neither the row nor the kwarg supplies one — tests
        always pass an explicit ts for determinism.

        Each row must carry at least ``prop`` and ``stage`` (raises ValueError
        otherwise). The whole batch is serialized in memory, then written with a
        single write + flush + fsync so a serialization error never leaves a
        partial line on disk.
        """
        if ts is None:
            ts = time.time()

        lines: list[str] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"row {i} is not a dict: {type(row)!r}")
            if not row.get("prop") or not row.get("stage"):
                raise ValueError(
                    f"row {i} missing required prop/stage: "
                    f"prop={row.get('prop')!r} stage={row.get('stage')!r}"
                )
            out = dict(row)
            # Kwarg fills only when the row hasn't set its own value.
            if not out.get("run_id"):
                out["run_id"] = run_id
            if not out.get("change_id"):
                out["change_id"] = change_id
            if out.get("ts") is None:
                out["ts"] = ts
            lines.append(json.dumps(out, ensure_ascii=False))

        if not lines:
            return 0

        # Serialize fully (above) BEFORE touching disk; then one atomic-ish write.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(line + "\n" for line in lines)
        with open(self.path, "a", encoding="utf-8") as f:
            # Self-heal a torn tail: if a prior crash left a newline-less partial
            # line, prepend a newline so this batch starts on its own line and
            # can't be swallowed by (or fused onto) the corruption. The torn line
            # is then isolated and gets skipped harmlessly on read.
            if f.tell() > 0:
                with open(self.path, "rb") as probe:
                    probe.seek(-1, os.SEEK_END)
                    if probe.read(1) != b"\n":
                        payload = "\n" + payload
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        return len(lines)

    # ------------------------------------------------------------------ read
    def all_rows(self) -> list[dict]:
        """Every row on disk. Tolerant: blank/garbage/torn lines are skipped."""
        if not self.path.exists():
            return []
        out: list[dict] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                # Half-written tail line or corruption — never fatal.
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    def rows(
        self,
        *,
        run_id: str | None = None,
        change_id: str | None = None,
        prop: str | None = None,
        stage: str | None = None,
    ) -> list[dict]:
        """Filtered query over all_rows(). Unspecified filters match everything."""
        out = self.all_rows()
        if run_id is not None:
            out = [r for r in out if r.get("run_id") == run_id]
        if change_id is not None:
            out = [r for r in out if r.get("change_id") == change_id]
        if prop is not None:
            out = [r for r in out if r.get("prop") == prop]
        if stage is not None:
            out = [r for r in out if r.get("stage") == stage]
        return out

    def runs(self) -> list[str]:
        """Distinct run_ids in first-seen (insertion) order."""
        seen: list[str] = []
        marker: set[str] = set()
        for r in self.all_rows():
            rid = r.get("run_id")
            if isinstance(rid, str) and rid not in marker:
                marker.add(rid)
                seen.append(rid)
        return seen

    # ------------------------------------------------------------- analytics
    def noise_floor(
        self,
        prop: str,
        *,
        stage: str | None = None,
        run_ids: list[str] | None = None,
    ) -> dict:
        """Spread of ``measured_value`` for one property across runs.

        Collects the matching rows (optionally narrowed by stage and to a set of
        run_ids), takes their non-None numeric ``measured_value``s, and returns
        ``{n, mean, variance, std, min, max, values}``. Variance/std are
        POPULATION statistics (these K runs ARE the observation set we care
        about). n<1 -> zeros/empty. The orchestrator runs the held-out set K>=5
        times and reads this to quantify the judged noise floor.
        """
        rid_filter = set(run_ids) if run_ids is not None else None
        values: list[float] = []
        for r in self.all_rows():
            if r.get("prop") != prop:
                continue
            if stage is not None and r.get("stage") != stage:
                continue
            if rid_filter is not None and r.get("run_id") not in rid_filter:
                continue
            mv = r.get("measured_value")
            if mv is None or isinstance(mv, bool):
                continue
            if isinstance(mv, (int, float)):
                values.append(float(mv))

        if not values:
            return _zero_floor()

        mean = statistics.fmean(values)
        if len(values) > 1:
            variance = statistics.pvariance(values, mean)
            std = statistics.pstdev(values, mean)
        else:
            variance = 0.0
            std = 0.0
        return {
            "n": len(values),
            "mean": mean,
            "variance": variance,
            "std": std,
            "min": min(values),
            "max": max(values),
            "values": values,
        }

    def pass_rate(self, *, run_id: str | None = None) -> dict:
        """Count {passed, failed, ungated, total} over rows (optionally one run).

        A row is ``ungated`` when its ``passed`` is None (not gated / not
        measurable). ``total`` is every row considered.
        """
        rows = self.rows(run_id=run_id) if run_id is not None else self.all_rows()
        passed = failed = ungated = 0
        for r in rows:
            p = r.get("passed")
            if p is True:
                passed += 1
            elif p is False:
                failed += 1
            else:
                ungated += 1
        return {
            "passed": passed,
            "failed": failed,
            "ungated": ungated,
            "total": len(rows),
        }
