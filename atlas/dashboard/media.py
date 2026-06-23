"""Streamed media + artifact serving — never buffer a multi-MB file into memory.

Video (and any large binary) is served with HTTP range support so the browser can
seek and the process streams a bounded window at a time. JSON artifacts are parsed
tolerantly, validated against their contract for a badge, and redacted before they
leave. Every path is resolved through security.resolve_* first, so nothing outside a
validated project dir can ever be served.
"""
from __future__ import annotations

import pathlib
import re
from typing import Iterator

from starlette.responses import JSONResponse, Response, StreamingResponse

import contracts
from dashboard import security
from dashboard.data import read_json

_CHUNK = 1024 * 512  # 512 KiB window
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_MIME = {".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".json": "application/json"}


def _content_type(path: pathlib.Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


def _file_iter(path: pathlib.Path, start: int, end: int) -> Iterator[bytes]:
    """Yield [start, end] inclusive in bounded chunks — constant memory, any size."""
    remaining = end - start + 1
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def stream_file(path: pathlib.Path, range_header: str | None) -> Response:
    """Serve `path` with Range support (206 partial / 200 full). Assumes `path` was
    already containment-validated by the caller."""
    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    size = path.stat().st_size
    ctype = _content_type(path)
    headers = {"Accept-Ranges": "bytes", "Content-Type": ctype,
               "Cache-Control": "no-store"}

    m = _RANGE_RE.match(range_header or "")
    if m and (m.group(1) or m.group(2)):
        if not m.group(1):
            # suffix range `bytes=-N` = the trailing N bytes (RFC 7233)
            start = max(0, size - int(m.group(2)))
            end = size - 1
        else:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return Response(status_code=416,
                            headers={"Content-Range": f"bytes */{size}",
                                     "Accept-Ranges": "bytes"})
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Length": str(end - start + 1)})
        return StreamingResponse(_file_iter(path, start, end), status_code=206,
                                 headers=headers, media_type=ctype)

    headers["Content-Length"] = str(size)
    return StreamingResponse(_file_iter(path, 0, size - 1), status_code=200,
                             headers=headers, media_type=ctype)


def serve_video(pdir: pathlib.Path, range_header: str | None) -> Response:
    return stream_file(pdir / "video.mp4", range_header)


def serve_relative_media(pdir: pathlib.Path, rel: str,
                         range_header: str | None) -> Response:
    """Serve a media file under a validated project dir (e.g. a draft render).
    Path is re-validated for containment here."""
    try:
        target = security.resolve_within(pdir, rel)
    except security.UnsafePathError:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    return stream_file(target, range_header)


def serve_artifact(pdir: pathlib.Path, name: str) -> Response:
    """Parse a whitelisted JSON artifact, attach a contract-validity badge, redact,
    and return it. Refuses any file not in the artifact allowlist."""
    if name not in security.ARTIFACT_FILES:
        return JSONResponse({"error": "artifact not allowed"}, status_code=400)
    try:
        target = security.resolve_within(pdir, name)
    except security.UnsafePathError:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "not found", "name": name}, status_code=404)
    obj = read_json(target, None)  # non-mutating: never renames a corrupt file
    if obj is None:
        # corrupt / unparseable — surface a note, never 500
        return JSONResponse({"name": name, "valid": False,
                             "error": "unparseable JSON", "data": None},
                            status_code=200)
    contract = security.ARTIFACT_FILES[name]
    valid, errors = (True, [])
    if contract:
        valid, errors = contracts.validate(contract, obj)
    payload = {"name": name, "contract": contract, "valid": valid,
               "errors": errors[:5] if not valid else [],
               "data": obj}
    # redact the WHOLE envelope (name, errors, data) — not just the inner data.
    return JSONResponse(security.redact(payload))
