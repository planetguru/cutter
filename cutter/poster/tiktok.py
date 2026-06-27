"""TikTok Content Posting API v2 uploader."""

from __future__ import annotations

import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..captioner import Caption
from ..config import ENV_PATH, Settings, get_settings
from .base import PostResult

CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB
API_BASE = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = f"{API_BASE}/oauth/token/"


class TikTokError(Exception):
    pass


class TikTokPoster:
    def __init__(self, settings: Settings) -> None:
        settings.require_tiktok()
        self.settings = settings
        self._access_token = settings.tiktok_access_token

    def post(self, clip_path: Path, caption: Caption | None) -> PostResult:
        title = caption.tiktok_caption[:150] if caption else clip_path.stem
        hashtags = caption.hashtag_string if caption else ""
        full_caption = f"{title}\n\n{hashtags}".strip()[:2200]

        try:
            publish_id = self._upload(clip_path, full_caption)
            self._poll_status(publish_id)
            return PostResult(platform="tiktok", clip_path=clip_path, publish_id=publish_id)
        except TikTokError as e:
            return PostResult(platform="tiktok", clip_path=clip_path, error=str(e))

    def _upload(self, clip_path: Path, caption: str) -> str:
        file_size = clip_path.stat().st_size
        n_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

        init_resp = self._post_json(
            f"{API_BASE}/post/publish/video/init/",
            {
                "post_info": {
                    "title": caption,
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": CHUNK_SIZE,
                    "total_chunk_count": n_chunks,
                },
            },
        )
        data = init_resp.get("data", {})
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise TikTokError(f"Init response missing fields: {init_resp}")

        self._upload_chunks(clip_path, upload_url, file_size, n_chunks)
        return publish_id

    def _upload_chunks(
        self, clip_path: Path, upload_url: str, file_size: int, n_chunks: int
    ) -> None:
        with clip_path.open("rb") as fh:
            for i in range(n_chunks):
                start = i * CHUNK_SIZE
                chunk = fh.read(CHUNK_SIZE)
                end = start + len(chunk) - 1
                resp = requests.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Content-Type": "video/mp4",
                    },
                    data=chunk,
                    timeout=120,
                )
                if resp.status_code not in (200, 206):
                    raise TikTokError(f"Chunk {i} upload failed: {resp.status_code} {resp.text[:200]}")

    @retry(
        wait=wait_exponential(min=5, max=60),
        stop=stop_after_attempt(20),
        retry=retry_if_exception(lambda e: isinstance(e, TikTokError) and "PROCESSING" in str(e)),
        reraise=True,
    )
    def _poll_status(self, publish_id: str) -> None:
        resp = self._post_json(
            f"{API_BASE}/post/publish/status/fetch/",
            {"publish_id": publish_id},
        )
        status = resp.get("data", {}).get("status", "")
        if status == "PUBLISH_COMPLETE":
            return
        if status in ("FAILED", "PUBLISH_FAILED"):
            raise TikTokError(f"TikTok publish failed: {resp}")
        raise TikTokError(f"PROCESSING: {status}")

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code == 401:
            self._refresh_token()
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json=payload,
                timeout=30,
            )
        if not resp.ok:
            raise TikTokError(f"API error {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    def _refresh_token(self) -> None:
        settings = self.settings
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": settings.tiktok_refresh_token,
            },
            timeout=30,
        )
        if not resp.ok:
            raise TikTokError(f"Token refresh failed: {resp.text[:200]}")
        data = resp.json()
        self._access_token = data["access_token"]
        _update_env("TIKTOK_ACCESS_TOKEN", self._access_token)
        if data.get("refresh_token"):
            _update_env("TIKTOK_REFRESH_TOKEN", data["refresh_token"])


def run_oauth_flow(settings: Settings) -> None:
    """Run the TikTok OAuth 2.0 dance and write tokens to .env."""
    state = secrets.token_urlsafe(16)
    redirect_uri = "http://localhost:8080/callback"
    scope = "video.upload,video.publish"

    params = {
        "client_key": settings.tiktok_client_key,
        "scope": scope,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    code_holder: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/callback" and "code" in qs:
                code_holder.append(qs["code"][0])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h1>TikTok auth complete. You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.HTTPServer(("localhost", 8080), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    print(f"Opening browser for TikTok login…\n{auth_url}")
    webbrowser.open(auth_url)

    for _ in range(120):
        if code_holder:
            break
        time.sleep(1)
    server.shutdown()

    if not code_holder:
        raise TikTokError("Timed out waiting for OAuth callback.")

    code = code_holder[0]
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if not resp.ok:
        raise TikTokError(f"Token exchange failed: {resp.text}")

    data = resp.json()
    _update_env("TIKTOK_ACCESS_TOKEN", data["access_token"])
    _update_env("TIKTOK_REFRESH_TOKEN", data.get("refresh_token", ""))
    _update_env("TIKTOK_OPEN_ID", data["open_id"])
    print("TikTok tokens saved to .env")


def _update_env(key: str, value: str) -> None:
    """Update or add a key in the project .env file."""
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        ENV_PATH.write_text("\n".join(lines) + "\n")
    else:
        ENV_PATH.write_text(f"{key}={value}\n")
    os.environ[key] = value
