"""Instagram Reels posting via Meta Graph API v21."""

from __future__ import annotations

import http.server
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
from ..config import ENV_PATH, Settings
from .base import PostResult
from .tiktok import _update_env

GRAPH_BASE = "https://graph.facebook.com/v21.0"
AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
TOKEN_URL = f"{GRAPH_BASE}/oauth/access_token"
LONG_LIVED_URL = f"https://graph.facebook.com/oauth/access_token"


class InstagramError(Exception):
    pass


class InstagramPoster:
    def __init__(self, settings: Settings) -> None:
        settings.require_instagram()
        self.settings = settings
        self._access_token = settings.instagram_access_token
        self._account_id = settings.instagram_account_id

    def post(self, clip_path: Path, caption: Caption | None) -> PostResult:
        text = ""
        if caption:
            text = f"{caption.instagram_caption}\n\n{caption.hashtag_string}".strip()[:2200]

        try:
            container_id, upload_uri = self._init_upload(text)
            self._upload_video(upload_uri, clip_path)
            self._poll_container(container_id)
            media_id = self._publish(container_id)
            url = f"https://www.instagram.com/p/{media_id}/"
            return PostResult(platform="instagram", clip_path=clip_path, url=url, publish_id=media_id)
        except InstagramError as e:
            return PostResult(platform="instagram", clip_path=clip_path, error=str(e))

    def _init_upload(self, caption: str) -> tuple[str, str]:
        """Create a resumable upload container. Returns (container_id, upload_uri)."""
        resp = requests.post(
            f"{GRAPH_BASE}/{self._account_id}/media",
            params={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption,
                "share_to_feed": "true",
                "access_token": self._access_token,
            },
            timeout=60,
        )
        self._check_response(resp)
        data = resp.json()
        return data["id"], data["uri"]

    def _upload_video(self, upload_uri: str, clip_path: Path) -> None:
        """Upload video bytes directly to Meta's resumable upload endpoint."""
        file_size = clip_path.stat().st_size
        with clip_path.open("rb") as f:
            resp = requests.post(
                upload_uri,
                headers={
                    "Authorization": f"OAuth {self._access_token}",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                },
                data=f,
                timeout=300,
            )
        if not resp.ok:
            raise InstagramError(f"Video upload failed {resp.status_code}: {resp.text[:400]}")

    @retry(
        wait=wait_exponential(min=5, max=30),
        stop=stop_after_attempt(24),
        retry=retry_if_exception(lambda e: isinstance(e, InstagramError) and "IN_PROGRESS" in str(e)),
        reraise=True,
    )
    def _poll_container(self, container_id: str) -> None:
        resp = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": self._access_token},
            timeout=30,
        )
        self._check_response(resp)
        status = resp.json().get("status_code", "")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise InstagramError(f"Container processing failed: {resp.json()}")
        raise InstagramError(f"IN_PROGRESS: {status}")

    def _publish(self, container_id: str) -> str:
        resp = requests.post(
            f"{GRAPH_BASE}/{self._account_id}/media_publish",
            params={
                "creation_id": container_id,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        self._check_response(resp)
        return resp.json()["id"]

    def _check_response(self, resp: requests.Response) -> None:
        if resp.status_code == 401:
            raise InstagramError("Instagram access token expired. Run: cutter auth instagram --refresh")
        if not resp.ok:
            raise InstagramError(f"API error {resp.status_code}: {resp.text[:400]}")


def run_oauth_flow(settings: Settings, refresh: bool = False) -> None:
    """Run Meta OAuth and write long-lived token to .env."""
    if refresh:
        _refresh_long_lived_token(settings)
        return

    state = secrets.token_urlsafe(16)
    redirect_uri = "http://localhost:8080/callback"
    scope = "instagram_basic,instagram_content_publish,pages_read_engagement"

    auth_params = {
        "client_id": settings.instagram_app_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "response_type": "code",
        "state": state,
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(auth_params)

    code_holder: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/callback" and "code" in qs:
                code_holder.append(qs["code"][0])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h1>Instagram auth complete. You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.HTTPServer(("localhost", 8080), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    print(f"Opening browser for Instagram login…\n{auth_url}")
    webbrowser.open(auth_url)

    for _ in range(120):
        if code_holder:
            break
        time.sleep(1)
    server.shutdown()

    if not code_holder:
        raise InstagramError("Timed out waiting for OAuth callback.")

    # Exchange code for short-lived token
    resp = requests.get(
        TOKEN_URL,
        params={
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "redirect_uri": redirect_uri,
            "code": code_holder[0],
        },
        timeout=30,
    )
    if not resp.ok:
        raise InstagramError(f"Token exchange failed: {resp.text}")

    short_token = resp.json()["access_token"]

    # Upgrade to long-lived token (60 days)
    resp2 = requests.get(
        LONG_LIVED_URL,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=30,
    )
    if not resp2.ok:
        raise InstagramError(f"Long-lived token exchange failed: {resp2.text}")

    long_token = resp2.json()["access_token"]
    _update_env("INSTAGRAM_ACCESS_TOKEN", long_token)

    # Fetch Instagram account ID
    me_resp = requests.get(
        f"{GRAPH_BASE}/me/accounts",
        params={"access_token": long_token},
        timeout=30,
    )
    if me_resp.ok:
        pages = me_resp.json().get("data", [])
        if pages:
            page_token = pages[0]["access_token"]
            ig_resp = requests.get(
                f"{GRAPH_BASE}/{pages[0]['id']}",
                params={"fields": "instagram_business_account", "access_token": page_token},
                timeout=30,
            )
            if ig_resp.ok:
                ig_id = ig_resp.json().get("instagram_business_account", {}).get("id", "")
                if ig_id:
                    _update_env("INSTAGRAM_ACCOUNT_ID", ig_id)
                    print(f"Instagram account ID set: {ig_id}")

    print("Instagram token saved to .env (expires in ~60 days; run: cutter auth instagram --refresh)")


def _refresh_long_lived_token(settings: Settings) -> None:
    resp = requests.get(
        f"{GRAPH_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "fb_exchange_token": settings.instagram_access_token,
        },
        timeout=30,
    )
    if not resp.ok:
        raise InstagramError(f"Token refresh failed: {resp.text}")
    _update_env("INSTAGRAM_ACCESS_TOKEN", resp.json()["access_token"])
    print("Instagram access token refreshed.")
