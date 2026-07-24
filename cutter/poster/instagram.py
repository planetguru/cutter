"""Instagram Reels posting via the Instagram API with Instagram Login.

This is Meta's newer, Facebook-Page-free API (graph.instagram.com): you
authenticate as the Instagram professional account itself. Meta has disabled
Page↔Instagram linking for many accounts, which broke the older Facebook-Login
flavour of this integration.

The trade-off: graph.instagram.com has no resumable byte upload — containers
ingest from a public `video_url`, so clips are staged temporarily on the media
server (MEDIA_* in .env) during posting.
"""

from __future__ import annotations

import http.server
import secrets
import subprocess
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

GRAPH_BASE = "https://graph.instagram.com/v23.0"
AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_LIVED_URL = "https://graph.instagram.com/access_token"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
SCOPES = "instagram_business_basic,instagram_business_content_publish"


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
            from ..captioner import append_attribution
            text = f"{caption.instagram_caption}\n\n{caption.hashtag_string}".strip()
            text = append_attribution(text, caption.video_id, max_len=2200)

        remote_name = None
        try:
            remote_name, video_url = self._stage_media(clip_path)
            container_id = self._create_container(video_url, text)
            self._poll_container(container_id)
            media_id = self._publish(container_id)
            url = f"https://www.instagram.com/p/{media_id}/"
            return PostResult(platform="instagram", clip_path=clip_path, url=url, publish_id=media_id)
        except InstagramError as e:
            return PostResult(platform="instagram", clip_path=clip_path, error=str(e))
        finally:
            if remote_name:
                self._unstage_media(remote_name)

    # -- media staging (Instagram ingests from a public URL) -----------------

    def _stage_media(self, clip_path: Path) -> tuple[str, str]:
        s = self.settings
        if not (s.media_host and s.media_webroot and s.media_base_url):
            raise InstagramError("MEDIA_HOST/MEDIA_WEBROOT/MEDIA_BASE_URL must be set in .env")
        remote_name = f"{clip_path.stem}_{secrets.token_hex(4)}_ig.mp4"

        if s.media_host == "local":
            # cutter runs on the web server itself — plain copy into the webroot.
            import shutil
            dest_path = Path(s.media_webroot) / remote_name
            shutil.copyfile(clip_path, dest_path)
            dest_path.chmod(0o644)
            return remote_name, f"{s.media_base_url}/{remote_name}"

        ssh_key = str(Path(s.media_ssh_key).expanduser())
        dest = f"{s.media_user}@{s.media_host}:{s.media_webroot}/{remote_name}"
        result = subprocess.run(
            ["scp", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             str(clip_path), dest],
            capture_output=True, timeout=180,
        )
        if result.returncode != 0:
            raise InstagramError(f"Failed to stage clip on media server: {result.stderr.decode().strip()}")
        subprocess.run(
            ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             f"{s.media_user}@{s.media_host}", f"chmod 644 {s.media_webroot}/{remote_name}"],
            capture_output=True, timeout=30,
        )
        return remote_name, f"{s.media_base_url}/{remote_name}"

    def _unstage_media(self, remote_name: str) -> None:
        s = self.settings
        if s.media_host == "local":
            (Path(s.media_webroot) / remote_name).unlink(missing_ok=True)
            return
        ssh_key = str(Path(s.media_ssh_key).expanduser())
        try:
            subprocess.run(
                ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                 f"{s.media_user}@{s.media_host}", f"rm -f {s.media_webroot}/{remote_name}"],
                capture_output=True, timeout=30,
            )
        except Exception:
            pass  # best-effort cleanup

    # -- publishing -----------------------------------------------------------

    def _create_container(self, video_url: str, caption: str) -> str:
        resp = requests.post(
            f"{GRAPH_BASE}/{self._account_id}/media",
            params={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": self._access_token,
            },
            timeout=60,
        )
        self._check_response(resp)
        return resp.json()["id"]

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
    """Run Instagram-Login OAuth and write a long-lived token to .env.

    Instagram requires an HTTPS redirect URI, so the registered URI is a page
    on the media server that immediately bounces the browser (with the auth
    code) back to the local listener on port 8080.
    """
    if refresh:
        _refresh_long_lived_token(settings)
        return

    redirect_uri = settings.instagram_redirect_uri
    auth_params = {
        "client_id": settings.instagram_app_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "response_type": "code",
        "state": secrets.token_urlsafe(16),
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(auth_params)

    code_holder: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                # Instagram appends '#_' to the redirect; strip any fragment junk.
                code_holder.append(qs["code"][0].split("#")[0])
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

    # Exchange code for a short-lived token (1 h)
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code_holder[0],
        },
        timeout=30,
    )
    if not resp.ok:
        raise InstagramError(f"Token exchange failed: {resp.text}")
    short_token = resp.json()["access_token"]

    # Upgrade to a long-lived token (60 days)
    resp2 = requests.get(
        LONG_LIVED_URL,
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.instagram_app_secret,
            "access_token": short_token,
        },
        timeout=30,
    )
    if not resp2.ok:
        raise InstagramError(f"Long-lived token exchange failed: {resp2.text}")
    long_token = resp2.json()["access_token"]
    _update_env("INSTAGRAM_ACCESS_TOKEN", long_token)

    # The professional account ID used by the publishing endpoints
    me = requests.get(
        f"{GRAPH_BASE}/me",
        params={"fields": "user_id,username", "access_token": long_token},
        timeout=30,
    )
    if not me.ok:
        raise InstagramError(f"Could not fetch account info: {me.text}")
    data = me.json()
    _update_env("INSTAGRAM_ACCOUNT_ID", str(data["user_id"]))
    print(f"Authorized as @{data.get('username', '?')} — account ID {data['user_id']} saved to .env.")


def _refresh_long_lived_token(settings: Settings) -> None:
    resp = requests.get(
        REFRESH_URL,
        params={
            "grant_type": "ig_refresh_token",
            "access_token": settings.instagram_access_token,
        },
        timeout=30,
    )
    if not resp.ok:
        raise InstagramError(f"Token refresh failed: {resp.text}")
    _update_env("INSTAGRAM_ACCESS_TOKEN", resp.json()["access_token"])
    print("Instagram token refreshed (valid another 60 days).")
