"""Facebook Reels posting via the Graph API (Page video_reels endpoint).

Unlike Instagram (which cutter drives via the Page-free Instagram-Login API),
Facebook Reels must be posted to a Facebook **Page** using a Page access token.
The token is obtained once via Facebook Login (`cutter auth facebook`): a
long-lived user token is exchanged, then the Page's own token is read from
/me/accounts. Page tokens derived from a long-lived user token do not expire,
so there is no refresh cycle.

Publishing is a three-phase upload: start (get an upload URL) → upload the
video bytes to rupload.facebook.com → finish (publish with a caption).
"""

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
from ..config import Settings
from .base import PostResult
from .tiktok import _update_env

GRAPH = "https://graph.facebook.com/v23.0"
AUTH_URL = "https://www.facebook.com/v23.0/dialog/oauth"
TOKEN_URL = f"{GRAPH}/oauth/access_token"
SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts"


class FacebookError(Exception):
    pass


class FacebookPoster:
    def __init__(self, settings: Settings) -> None:
        settings.require_facebook()
        self.settings = settings
        self._page_id = settings.facebook_page_id
        self._token = settings.facebook_page_token

    def post(self, clip_path: Path, caption: Caption | None) -> PostResult:
        # Facebook Reels take the Instagram caption + hashtags well enough.
        text = ""
        if caption:
            from ..captioner import append_attribution
            text = f"{caption.instagram_caption}\n\n{caption.hashtag_string}".strip()
            text = append_attribution(text, caption.video_id, max_len=2200)

        try:
            video_id, upload_url = self._start()
            self._upload(upload_url, clip_path)
            self._finish(video_id, text)
            self._poll_status(video_id)
            url = self._permalink(video_id)
            return PostResult(platform="facebook", clip_path=clip_path, url=url, publish_id=video_id)
        except FacebookError as e:
            return PostResult(platform="facebook", clip_path=clip_path, error=str(e))

    def _start(self) -> tuple[str, str]:
        resp = requests.post(
            f"{GRAPH}/{self._page_id}/video_reels",
            data={"upload_phase": "start", "access_token": self._token},
            timeout=30,
        )
        data = self._json(resp)
        vid, upload_url = data.get("video_id"), data.get("upload_url")
        if not vid or not upload_url:
            raise FacebookError(f"start phase missing fields: {data}")
        return vid, upload_url

    def _upload(self, upload_url: str, clip_path: Path) -> None:
        size = clip_path.stat().st_size
        with clip_path.open("rb") as fh:
            resp = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {self._token}",
                    "offset": "0",
                    "file_size": str(size),
                },
                data=fh,
                timeout=300,
            )
        if not resp.ok or not resp.json().get("success"):
            raise FacebookError(f"upload failed {resp.status_code}: {resp.text[:400]}")

    def _finish(self, video_id: str, description: str) -> None:
        resp = requests.post(
            f"{GRAPH}/{self._page_id}/video_reels",
            data={
                "access_token": self._token,
                "video_id": video_id,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "description": description,
            },
            timeout=30,
        )
        data = self._json(resp)
        if not data.get("success"):
            raise FacebookError(f"finish phase failed: {data}")

    @retry(
        wait=wait_exponential(min=5, max=30),
        stop=stop_after_attempt(20),
        retry=retry_if_exception(lambda e: isinstance(e, FacebookError) and "IN_PROGRESS" in str(e)),
        reraise=True,
    )
    def _poll_status(self, video_id: str) -> None:
        resp = requests.get(
            f"{GRAPH}/{video_id}",
            params={"fields": "status", "access_token": self._token},
            timeout=30,
        )
        status = self._json(resp).get("status", {})
        pub = (status.get("publishing_phase") or {}).get("status", "")
        proc = (status.get("processing_phase") or {}).get("status", "")
        if pub in ("complete", "published"):
            return
        if "error" in (pub, proc) or status.get("video_status") == "error":
            raise FacebookError(f"Reel processing failed: {status}")
        raise FacebookError(f"IN_PROGRESS: processing={proc} publishing={pub}")

    def _permalink(self, video_id: str) -> str:
        try:
            resp = requests.get(
                f"{GRAPH}/{video_id}",
                params={"fields": "permalink_url", "access_token": self._token},
                timeout=30,
            )
            link = resp.json().get("permalink_url")
            if link:
                return link if link.startswith("http") else f"https://www.facebook.com{link}"
        except Exception:
            pass
        return f"https://www.facebook.com/reel/{video_id}"

    def _json(self, resp: requests.Response) -> dict[str, Any]:
        if resp.status_code == 401:
            raise FacebookError("Facebook page token invalid/expired. Run: cutter auth facebook")
        if not resp.ok:
            raise FacebookError(f"API error {resp.status_code}: {resp.text[:400]}")
        return resp.json()


def run_oauth_flow(settings: Settings) -> None:
    """Facebook Login → long-lived user token → Page token; write both to .env."""
    if not settings.facebook_app_id or not settings.facebook_app_secret:
        raise FacebookError(
            "FACEBOOK_APP_ID / FACEBOOK_APP_SECRET are not set. See docs/facebook_oauth.md."
        )
    redirect_uri = "http://localhost:8080/callback"
    params = {
        "client_id": settings.facebook_app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": secrets.token_urlsafe(16),
    }
    # Facebook Login for Business (Business-type apps) uses a portal-defined
    # configuration instead of a scope list; classic Login uses scope.
    if settings.facebook_config_id:
        params["config_id"] = settings.facebook_config_id
    else:
        params["scope"] = SCOPES
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    code_holder: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                code_holder.append(qs["code"][0])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h1>Facebook auth complete. You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.HTTPServer(("localhost", 8080), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Opening browser for Facebook login…\n{auth_url}")
    webbrowser.open(auth_url)
    for _ in range(120):
        if code_holder:
            break
        time.sleep(1)
    server.shutdown()
    if not code_holder:
        raise FacebookError("Timed out waiting for OAuth callback.")

    # code → short-lived user token
    resp = requests.get(
        TOKEN_URL,
        params={
            "client_id": settings.facebook_app_id,
            "client_secret": settings.facebook_app_secret,
            "redirect_uri": redirect_uri,
            "code": code_holder[0],
        },
        timeout=30,
    )
    if not resp.ok:
        raise FacebookError(f"Token exchange failed: {resp.text}")
    short = resp.json()["access_token"]

    # short → long-lived user token (~60 days)
    resp2 = requests.get(
        TOKEN_URL,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.facebook_app_id,
            "client_secret": settings.facebook_app_secret,
            "fb_exchange_token": short,
        },
        timeout=30,
    )
    if not resp2.ok:
        raise FacebookError(f"Long-lived token exchange failed: {resp2.text}")
    long_user = resp2.json()["access_token"]

    # list Pages this user manages; their tokens (from a long-lived user token)
    # are themselves long-lived / non-expiring.
    me = requests.get(
        f"{GRAPH}/me/accounts",
        params={"fields": "name,access_token,tasks", "access_token": long_user},
        timeout=30,
    )
    if not me.ok:
        raise FacebookError(f"Could not list Pages: {me.text}")
    pages = me.json().get("data", [])
    if not pages:
        raise FacebookError(
            "No Facebook Pages found for this account. Make sure you log in as the "
            "account that manages the Page, and that the app has access to it."
        )

    want = settings.facebook_page_name.strip().lower()
    chosen = None
    if want:
        chosen = next((p for p in pages if p.get("name", "").strip().lower() == want), None)
    if chosen is None:
        chosen = pages[0]
        if len(pages) > 1:
            names = ", ".join(p.get("name", "?") for p in pages)
            print(f"Multiple Pages found ({names}). Using '{chosen.get('name')}'. "
                  f"Set FACEBOOK_PAGE_NAME in .env to pick a different one.")

    _update_env("FACEBOOK_PAGE_ID", chosen["id"])
    _update_env("FACEBOOK_PAGE_TOKEN", chosen["access_token"])
    print(f"Facebook Page '{chosen.get('name')}' ({chosen['id']}) saved to .env — Reels posting ready.")
