"""YouTube Shorts uploader via YouTube Data API v3."""

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

from ..captioner import Caption
from ..config import ENV_PATH, Settings
from .base import PostResult
from .tiktok import _update_env

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
REDIRECT_URI = "http://localhost:8080/callback"


class YouTubeError(Exception):
    pass


class YouTubePoster:
    def __init__(self, settings: Settings) -> None:
        settings.require_youtube()
        self.settings = settings
        self._access_token = settings.youtube_access_token

    def post(self, clip_path: Path, caption: Caption | None) -> PostResult:
        title = (caption.tiktok_caption[:100] if caption else clip_path.stem)
        description = (caption.tiktok_caption if caption else "") + "\n\n#Shorts"
        tags = (caption.hashtags if caption else []) + ["Shorts"]

        try:
            video_id = self._upload(clip_path, title, description, tags)
            url = f"https://www.youtube.com/shorts/{video_id}"
            return PostResult(platform="youtube", clip_path=clip_path, url=url, publish_id=video_id)
        except YouTubeError as e:
            return PostResult(platform="youtube", clip_path=clip_path, error=str(e))

    def _upload(self, clip_path: Path, title: str, description: str, tags: list[str]) -> str:
        file_size = clip_path.stat().st_size

        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        # Initiate resumable upload session
        resp = self._initiate_upload(file_size, metadata)
        upload_uri = resp.headers.get("Location")
        if not upload_uri:
            raise YouTubeError(f"No upload URI in response headers: {dict(resp.headers)}")

        # Stream file in chunks
        return self._upload_chunks(clip_path, upload_uri, file_size)

    def _initiate_upload(self, file_size: int, metadata: dict[str, Any]) -> requests.Response:
        resp = requests.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(file_size),
            },
            data=json.dumps(metadata),
            timeout=30,
        )
        if resp.status_code == 401:
            self._refresh_token()
            resp = requests.post(
                UPLOAD_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(file_size),
                },
                data=json.dumps(metadata),
                timeout=30,
            )
        if not resp.ok:
            raise YouTubeError(f"Upload initiation failed {resp.status_code}: {resp.text[:400]}")
        return resp

    def _upload_chunks(self, clip_path: Path, upload_uri: str, file_size: int) -> str:
        with clip_path.open("rb") as fh:
            offset = 0
            while offset < file_size:
                chunk = fh.read(CHUNK_SIZE)
                end = offset + len(chunk) - 1
                resp = requests.put(
                    upload_uri,
                    headers={
                        "Content-Range": f"bytes {offset}-{end}/{file_size}",
                        "Content-Type": "video/mp4",
                    },
                    data=chunk,
                    timeout=120,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    video_id = data.get("id")
                    if not video_id:
                        raise YouTubeError(f"Upload complete but no video ID in response: {data}")
                    return video_id
                if resp.status_code == 308:
                    # Incomplete - continue uploading
                    range_header = resp.headers.get("Range", "")
                    if range_header:
                        offset = int(range_header.split("-")[1]) + 1
                    else:
                        offset += len(chunk)
                    continue
                raise YouTubeError(f"Chunk upload failed {resp.status_code}: {resp.text[:400]}")
        raise YouTubeError("Upload loop exited without completion")

    def _refresh_token(self) -> None:
        s = self.settings
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": s.youtube_client_id,
                "client_secret": s.youtube_client_secret,
                "refresh_token": s.youtube_refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if not resp.ok:
            raise YouTubeError(f"Token refresh failed: {resp.text[:200]}")
        data = resp.json()
        self._access_token = data["access_token"]
        _update_env("YOUTUBE_ACCESS_TOKEN", self._access_token)


def run_oauth_flow(settings: Settings) -> None:
    """Run Google OAuth 2.0 and write tokens to .env."""
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": settings.youtube_client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
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
                self.wfile.write(b"<h1>YouTube auth complete. You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.HTTPServer(("localhost", 8080), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    print(f"Opening browser for YouTube login...\n{auth_url}")
    webbrowser.open(auth_url)

    for _ in range(120):
        if code_holder:
            break
        time.sleep(1)
    server.shutdown()

    if not code_holder:
        raise YouTubeError("Timed out waiting for OAuth callback.")

    code = code_holder[0]
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    if not resp.ok:
        raise YouTubeError(f"Token exchange failed: {resp.text}")

    data = resp.json()
    access_token = data["access_token"]
    _update_env("YOUTUBE_ACCESS_TOKEN", access_token)
    _update_env("YOUTUBE_REFRESH_TOKEN", data.get("refresh_token", ""))

    # Identify which channel the token is for so the user can verify it's correct.
    ch_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet", "mine": "true", "maxResults": "10"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if ch_resp.ok:
        items = ch_resp.json().get("items", [])
        if items:
            ch = items[0]
            ch_id = ch["id"]
            ch_name = ch["snippet"]["title"]
            _update_env("YOUTUBE_CHANNEL_ID", ch_id)
            print(f"\nAuthorised channel: {ch_name} (id: {ch_id})")
            if len(items) > 1:
                print("Other channels on this token:")
                for extra in items[1:]:
                    print(f"  - {extra['snippet']['title']} ({extra['id']})")
                print("\nIf this is the wrong channel, switch to the correct one at youtube.com then run: cutter auth youtube")
            else:
                print("YouTube tokens saved to .env")
        else:
            print("YouTube tokens saved to .env (could not determine channel name)")
    else:
        print("YouTube tokens saved to .env (channel lookup failed)")
