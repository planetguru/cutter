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

    # TikTok
    tiktok_client_key: str = field(default_factory=lambda: os.getenv("TIKTOK_CLIENT_KEY", ""))
    tiktok_client_secret: str = field(default_factory=lambda: os.getenv("TIKTOK_CLIENT_SECRET", ""))
    tiktok_access_token: str = field(default_factory=lambda: os.getenv("TIKTOK_ACCESS_TOKEN", ""))
    tiktok_refresh_token: str = field(default_factory=lambda: os.getenv("TIKTOK_REFRESH_TOKEN", ""))
    tiktok_open_id: str = field(default_factory=lambda: os.getenv("TIKTOK_OPEN_ID", ""))

    # Instagram / Meta
    instagram_app_id: str = field(default_factory=lambda: os.getenv("INSTAGRAM_APP_ID", ""))
    instagram_app_secret: str = field(default_factory=lambda: os.getenv("INSTAGRAM_APP_SECRET", ""))
    instagram_access_token: str = field(default_factory=lambda: os.getenv("INSTAGRAM_ACCESS_TOKEN", ""))
    instagram_account_id: str = field(default_factory=lambda: os.getenv("INSTAGRAM_ACCOUNT_ID", ""))

    # WhatsApp (Twilio)
    twilio_account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    twilio_whatsapp_from: str = field(default_factory=lambda: os.getenv("TWILIO_WHATSAPP_FROM", ""))
    twilio_whatsapp_to: str = field(default_factory=lambda: os.getenv("TWILIO_WHATSAPP_TO", ""))

    # Preview server (WhatsApp approval clip hosting)
    preview_host: str = field(default_factory=lambda: os.getenv("PREVIEW_HOST", ""))
    preview_user: str = field(default_factory=lambda: os.getenv("PREVIEW_USER", "root"))
    preview_webroot: str = field(default_factory=lambda: os.getenv("PREVIEW_WEBROOT", ""))
    preview_base_url: str = field(default_factory=lambda: os.getenv("PREVIEW_BASE_URL", ""))
    preview_ssh_key: str = field(
        default_factory=lambda: os.getenv("PREVIEW_SSH_KEY") or str(Path.home() / ".ssh" / "id_ed25519")
    )

    # S3 (Instagram video staging — only needed when posting to Instagram)
    aws_access_key_id: str = field(default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID", ""))
    aws_secret_access_key: str = field(default_factory=lambda: os.getenv("AWS_SECRET_ACCESS_KEY", ""))
    aws_s3_bucket: str = field(default_factory=lambda: os.getenv("AWS_S3_BUCKET", ""))
    aws_s3_region: str = field(default_factory=lambda: os.getenv("AWS_S3_REGION", "us-east-1"))

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
        s3_missing = [
            k for k in ("aws_access_key_id", "aws_secret_access_key", "aws_s3_bucket")
            if not getattr(self, k)
        ]
        if s3_missing:
            raise ConfigError(
                f"S3 credentials missing: {', '.join(s3_missing).upper()}. Required for Instagram video upload."
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
