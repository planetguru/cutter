"""TikTok Content Posting API v2 uploader."""

from __future__ import annotations

import hashlib
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
MAX_SINGLE_CHUNK = 64 * 1024 * 1024  # TikTok's ceiling for one-chunk uploads
API_BASE = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = f"{API_BASE}/oauth/token/"


class TikTokError(Exception):
    pass


def build_caption(caption: Caption | None, clip_path: Path) -> str:
    title = caption.tiktok_caption[:150] if caption else clip_path.stem
    hashtags = caption.hashtag_string if caption else ""
    return f"{title}\n\n{hashtags}".strip()[:2200]


class TikTokPoster:
    def __init__(self, settings: Settings) -> None:
        settings.require_tiktok()
        self.settings = settings
        self._access_token = settings.tiktok_access_token

    def post(self, clip_path: Path, caption: Caption | None) -> PostResult:
        full_caption = build_caption(caption, clip_path)

        try:
            publish_id = self._upload(clip_path, full_caption)
            self._poll_status(publish_id)
            return PostResult(platform="tiktok", clip_path=clip_path, publish_id=publish_id)
        except TikTokError as e:
            return PostResult(platform="tiktok", clip_path=clip_path, error=str(e))

    def _upload(self, clip_path: Path, caption: str) -> str:
        file_size = clip_path.stat().st_size
        # TikTok chunking rules: single-chunk uploads (allowed up to 64 MB) must
        # declare chunk_size == video_size; larger files use fixed chunks with
        # any trailing partial chunk merged into the last one (count = floor).
        if file_size <= MAX_SINGLE_CHUNK:
            chunk_size = file_size
            n_chunks = 1
        else:
            chunk_size = CHUNK_SIZE
            n_chunks = file_size // CHUNK_SIZE

        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": chunk_size,
            "total_chunk_count": n_chunks,
        }

        if self.settings.tiktok_post_mode == "direct":
            # Direct post sets the caption and publishes immediately; TikTok only
            # allows public visibility for apps that have passed their audit —
            # unaudited/sandbox apps are limited to SELF_ONLY.
            creator = self._post_json(f"{API_BASE}/post/publish/creator_info/query/", {})
            allowed = creator.get("data", {}).get("privacy_level_options", [])
            privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in allowed else "SELF_ONLY"
            init_url = f"{API_BASE}/post/publish/video/init/"
            payload: dict[str, Any] = {
                "post_info": {
                    "title": caption,
                    "privacy_level": privacy,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000,
                },
                "source_info": source_info,
            }
        else:
            # Inbox upload: the clip lands as a draft in the user's TikTok inbox
            # and is published manually in-app. Captions can't be attached here,
            # so the pipeline sends the caption via WhatsApp instead.
            init_url = f"{API_BASE}/post/publish/inbox/video/init/"
            payload = {"source_info": source_info}

        init_resp = self._post_json(init_url, payload)
        data = init_resp.get("data", {})
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise TikTokError(f"Init response missing fields: {init_resp}")

        self._upload_chunks(clip_path, upload_url, file_size, chunk_size, n_chunks)
        return publish_id

    def _upload_chunks(
        self, clip_path: Path, upload_url: str, file_size: int, chunk_size: int, n_chunks: int
    ) -> None:
        with clip_path.open("rb") as fh:
            for i in range(n_chunks):
                start = i * chunk_size
                # The last chunk absorbs any remainder beyond a whole chunk.
                read_size = file_size - start if i == n_chunks - 1 else chunk_size
                chunk = fh.read(read_size)
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
                # 206 = intermediate chunk accepted, 201 = final chunk completed the file
                if resp.status_code not in (200, 201, 206):
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
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
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
    """Run the TikTok OAuth 2.0 dance (with PKCE) and write tokens to .env."""
    state = secrets.token_urlsafe(16)
    redirect_uri = settings.tiktok_redirect_uri
    port = urllib.parse.urlparse(redirect_uri).port or 8080
    # Inbox mode only needs video.upload; video.publish requires the Direct Post
    # feature to be enabled on the app, so only request it when actually used.
    # user.info.basic lets us show which account authorized.
    scope = (
        "user.info.basic,video.upload,video.publish"
        if settings.tiktok_post_mode == "direct"
        else "user.info.basic,video.upload"
    )

    # TikTok requires PKCE, with a nonstandard hex-encoded (not base64url)
    # SHA-256 code challenge.
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = hashlib.sha256(code_verifier.encode()).hexdigest()

    params = {
        "client_key": settings.tiktok_client_key,
        "scope": scope,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    code_holder: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                code_holder.append(qs["code"][0])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h1>TikTok auth complete. You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.HTTPServer(("localhost", port), Handler)
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
            "code_verifier": code_verifier,
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

    info = requests.get(
        f"{API_BASE}/user/info/",
        headers={"Authorization": f"Bearer {data['access_token']}"},
        params={"fields": "display_name,username"},
        timeout=30,
    )
    if info.ok:
        user = info.json().get("data", {}).get("user", {})
        name = user.get("display_name", "?")
        username = user.get("username", "")
        handle = f" (@{username})" if username else ""
        print(f"Authorized as: {name}{handle} — check this is the account you post from.")


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
