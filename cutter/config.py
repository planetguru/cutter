"""Settings loaded from .env — single source of truth for all credentials."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Absolute path to the .env file, derived from this file's location so that
# cron jobs running from arbitrary working directories still find it.
ENV_PATH = Path(__file__).parent.parent / ".env"

load_dotenv(ENV_PATH)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    # Claude
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))

    # yt-dlp cookies (Netscape cookies.txt) — needed when YouTube demands
    # "Sign in to confirm you're not a bot" (common from datacenter/server IPs).
    # If unset, the downloader also looks for youtube_cookies.txt in the workdir.
    youtube_cookies: str = field(default_factory=lambda: os.getenv("YOUTUBE_COOKIES", ""))

    # TikTok
    tiktok_client_key: str = field(default_factory=lambda: os.getenv("TIKTOK_CLIENT_KEY", ""))
    tiktok_client_secret: str = field(default_factory=lambda: os.getenv("TIKTOK_CLIENT_SECRET", ""))
    tiktok_access_token: str = field(default_factory=lambda: os.getenv("TIKTOK_ACCESS_TOKEN", ""))
    tiktok_refresh_token: str = field(default_factory=lambda: os.getenv("TIKTOK_REFRESH_TOKEN", ""))
    tiktok_open_id: str = field(default_factory=lambda: os.getenv("TIKTOK_OPEN_ID", ""))
    # "manual" sends the clip + caption via WhatsApp to post by hand (no TikTok
    # API involved — the only reliable option for unaudited personal apps);
    # "inbox" uploads a draft to the user's TikTok inbox (sandbox-compatible on
    # paper, but drafts were never delivered in testing); "direct" posts
    # immediately and requires an audited app.
    tiktok_post_mode: str = field(default_factory=lambda: os.getenv("TIKTOK_POST_MODE", "manual"))
    # Must match the Desktop Redirect URI registered in the TikTok developer
    # portal character-for-character.
    tiktok_redirect_uri: str = field(
        default_factory=lambda: os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:8080/callback")
    )

    # Facebook Reels (posts to a Facebook Page via the main Meta app + Facebook Login)
    facebook_app_id: str = field(default_factory=lambda: os.getenv("FACEBOOK_APP_ID", ""))
    facebook_app_secret: str = field(default_factory=lambda: os.getenv("FACEBOOK_APP_SECRET", ""))
    facebook_page_id: str = field(default_factory=lambda: os.getenv("FACEBOOK_PAGE_ID", ""))
    facebook_page_token: str = field(default_factory=lambda: os.getenv("FACEBOOK_PAGE_TOKEN", ""))
    facebook_page_name: str = field(default_factory=lambda: os.getenv("FACEBOOK_PAGE_NAME", ""))
    # Facebook Login for Business uses a config ID (built in the portal) instead
    # of a scope list. Leave blank to fall back to classic scope-based login.
    facebook_config_id: str = field(default_factory=lambda: os.getenv("FACEBOOK_CONFIG_ID", ""))

    # Instagram / Meta
    instagram_app_id: str = field(default_factory=lambda: os.getenv("INSTAGRAM_APP_ID", ""))
    instagram_app_secret: str = field(default_factory=lambda: os.getenv("INSTAGRAM_APP_SECRET", ""))
    instagram_access_token: str = field(default_factory=lambda: os.getenv("INSTAGRAM_ACCESS_TOKEN", ""))
    instagram_account_id: str = field(default_factory=lambda: os.getenv("INSTAGRAM_ACCOUNT_ID", ""))
    # Must be HTTPS and registered in the app's Instagram business login
    # settings; the page bounces the code back to localhost:8080.
    instagram_redirect_uri: str = field(
        default_factory=lambda: os.getenv("INSTAGRAM_REDIRECT_URI", "https://cutter.chris.uk.com/instagram/callback")
    )

    # Telegram (approval conversations + notifications)
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Read-only Telegram assistant (cutter assistant)
    assistant_model: str = field(default_factory=lambda: os.getenv("ASSISTANT_MODEL", "claude-opus-4-8"))

    # Media staging server — Instagram's API ingests videos from a public URL,
    # so clips are scp'd here temporarily during posting.
    media_host: str = field(default_factory=lambda: os.getenv("MEDIA_HOST", ""))
    media_user: str = field(default_factory=lambda: os.getenv("MEDIA_USER", "root"))
    media_webroot: str = field(default_factory=lambda: os.getenv("MEDIA_WEBROOT", ""))
    media_base_url: str = field(default_factory=lambda: os.getenv("MEDIA_BASE_URL", ""))
    media_ssh_key: str = field(
        default_factory=lambda: os.getenv("MEDIA_SSH_KEY") or str(Path.home() / ".ssh" / "id_ed25519")
    )

    # YouTube
    youtube_client_id: str = field(default_factory=lambda: os.getenv("YOUTUBE_CLIENT_ID", ""))
    youtube_client_secret: str = field(default_factory=lambda: os.getenv("YOUTUBE_CLIENT_SECRET", ""))
    youtube_access_token: str = field(default_factory=lambda: os.getenv("YOUTUBE_ACCESS_TOKEN", ""))
    youtube_refresh_token: str = field(default_factory=lambda: os.getenv("YOUTUBE_REFRESH_TOKEN", ""))
    youtube_channel_id: str = field(default_factory=lambda: os.getenv("YOUTUBE_CHANNEL_ID", ""))

    def require_anthropic(self) -> None:
        if not self.anthropic_api_key:
            raise ConfigError("ANTHROPIC_API_KEY is not set. Add it to .env to enable caption generation.")

    def require_tiktok(self) -> None:
        missing = [
            k for k in ("tiktok_client_key", "tiktok_client_secret", "tiktok_access_token", "tiktok_open_id")
            if not getattr(self, k)
        ]
        if missing:
            raise ConfigError(
                f"TikTok credentials missing: {', '.join(missing).upper()}. Run: cutter auth tiktok"
            )

    def require_instagram(self) -> None:
        missing = [
            k for k in ("instagram_app_id", "instagram_app_secret", "instagram_access_token", "instagram_account_id")
            if not getattr(self, k)
        ]
        if missing:
            raise ConfigError(
                f"Instagram credentials missing: {', '.join(missing).upper()}. Run: cutter auth instagram"
            )

    def require_facebook(self) -> None:
        missing = [
            k for k in ("facebook_app_id", "facebook_app_secret", "facebook_page_id", "facebook_page_token")
            if not getattr(self, k)
        ]
        if missing:
            raise ConfigError(
                f"Facebook credentials missing: {', '.join(missing).upper()}. Run: cutter auth facebook"
            )

    def require_youtube(self) -> None:
        missing = [
            k for k in ("youtube_client_id", "youtube_client_secret", "youtube_access_token", "youtube_refresh_token")
            if not getattr(self, k)
        ]
        if missing:
            raise ConfigError(
                f"YouTube credentials missing: {', '.join(missing).upper()}. Run: cutter auth youtube"
            )


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise ConfigError(
            "ffmpeg not found on PATH. Install it with:\n"
            "  macOS:  brew install ffmpeg\n"
            "  Ubuntu: sudo apt install ffmpeg"
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
