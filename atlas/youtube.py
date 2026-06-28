"""The YouTube seam — a thin client boundary, never a secret store.

Credentials live in the ENVIRONMENT ONLY. This module reads them from os.environ;
it never writes them to a file, a log, or the repo. When they're absent it raises
MissingCredentials so the caller can ask the board via request_from_ceo — Atlas
escalates for the key, it never invents or persists one.

The real network calls (google-api-python-client) are injected as `api` so the
boundary is testable offline and the heavy dependency is optional. Upload defaults
to UNLISTED — going public is a separate, human-approved step (see publish.py).
"""
from __future__ import annotations

import os

# OAuth (an installed-app refresh-token flow) — uploads need write scope, which an
# API key alone cannot grant. All three must be present in the environment.
ENV_VARS = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")

DEFAULT_PRIVACY = "unlisted"
_VALID_PRIVACY = ("unlisted", "private", "public")


class MissingCredentials(RuntimeError):
    """No YouTube OAuth credentials in the environment — ask the board for them."""


def credentials() -> dict:
    """Read OAuth credentials from the environment. Raise MissingCredentials (naming
    the missing vars) if any are absent. Never reads or writes anywhere but env."""
    missing = [v for v in ENV_VARS if not os.environ.get(v)]
    if missing:
        raise MissingCredentials(
            "missing YouTube OAuth env vars: " + ", ".join(missing))
    return {v.lower(): os.environ[v] for v in ENV_VARS}


def _real_api(creds: dict):  # pragma: no cover - exercised only with live creds
    """Build a live YouTube Data API client. Imported lazily so the google client
    library is an OPTIONAL dependency — the seam works injected/offline without it."""
    from googleapiclient.discovery import build  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    c = Credentials(
        None, refresh_token=creds["yt_refresh_token"],
        client_id=creds["yt_client_id"], client_secret=creds["yt_client_secret"],
        token_uri="https://oauth2.googleapis.com/token")

    class _Api:
        def insert_video(self, *, title, description, tags, video_path, privacy):
            from googleapiclient.http import MediaFileUpload  # type: ignore
            yt = build("youtube", "v3", credentials=c)
            body = {"snippet": {"title": title, "description": description,
                                "tags": tags or []},
                    "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
            req = yt.videos().insert(part="snippet,status", body=body,
                                     media_body=MediaFileUpload(video_path))
            resp = req.execute()
            return {"id": resp["id"], "privacyStatus": privacy}

        def video_stats(self, video_id):
            yt = build("youtubeAnalytics", "v2", credentials=c)
            # left to the live integration; shape mirrors fetch_analytics' contract
            raise NotImplementedError
    return _Api()


def upload(*, title: str, description: str = "", tags=None, video_path: str,
           privacy: str = DEFAULT_PRIVACY, api=None, creds=None) -> dict:
    """Upload a video. Defaults to UNLISTED (never public here). `api` is injectable;
    when omitted, a live client is built from env credentials (raises if absent)."""
    if privacy not in _VALID_PRIVACY:
        privacy = DEFAULT_PRIVACY
    if api is None:
        api = _real_api(creds or credentials())
    resp = api.insert_video(title=title, description=description, tags=tags or [],
                            video_path=video_path, privacy=privacy)
    vid = resp.get("id") or resp.get("video_id")
    return {"video_id": vid, "privacy": resp.get("privacyStatus", privacy),
            "url": f"https://youtu.be/{vid}" if vid else None, "status": "uploaded"}


def fetch_analytics(video_id: str, *, api=None, creds=None) -> dict:
    """Fetch performance for one video. `api` injectable; live path builds from env.
    Returns a normalized {views, watch_time_min, rpm_usd, estimated_revenue_usd}."""
    if api is None:
        api = _real_api(creds or credentials())
    raw = api.video_stats(video_id)
    return {
        "views": int(raw.get("views", 0)),
        "watch_time_min": float(raw.get("watch_time_min", 0)),
        "rpm_usd": float(raw.get("rpm_usd", 0.0)),
        "estimated_revenue_usd": float(raw.get("estimated_revenue_usd", 0.0)),
    }
