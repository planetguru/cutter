"""Telegram Bot API client — approval conversations and notifications.

Uses getUpdates long-polling (no webhook/server). Telegram bots keep
undelivered updates for only ~24 hours, so every poll drains them into a local
journal (telegram_state.json in the workdir); the scan_* methods read from the
journal and therefore see messages older than that.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import platformdirs
import requests

from .config import Settings


class TelegramError(Exception):
    pass


class TelegramClient:
    LONG_POLL_SECS = 25
    DEFAULT_TIMEOUT_SECS = 600  # 10 minutes per prompt before re-sending
    JOURNAL_RETENTION_DAYS = 14

    def __init__(self, settings: Settings) -> None:
        _require_telegram(settings)
        self._base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self._chat_id = int(settings.telegram_chat_id)
        state_dir = Path(platformdirs.user_data_dir("cutter"))
        state_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = state_dir / "telegram_state.json"

    # -- sending ------------------------------------------------------------

    def send(self, body: str = "", media_url: str | None = None) -> datetime:
        """Send a message (optionally a video by URL). Returns the send time."""
        if media_url:
            result = self._api(
                "sendVideo", chat_id=self._chat_id, video=media_url,
                caption=body[:1024] or None,
            )
        else:
            # Prompts use *bold*/_italic_ markers; render them, but fall back
            # to plain text when user-supplied content breaks entity parsing
            # (e.g. an unbalanced underscore in a caption).
            try:
                result = self._api(
                    "sendMessage", chat_id=self._chat_id, text=body, parse_mode="Markdown"
                )
            except TelegramError as e:
                if "parse" not in str(e).lower():
                    raise
                result = self._api("sendMessage", chat_id=self._chat_id, text=body)
        return datetime.fromtimestamp(result["date"], tz=timezone.utc)

    def send_video(self, path: Path, caption: str = "") -> datetime:
        """Upload a local video file to the chat."""
        # The Bot API doesn't probe uploads — without explicit dimensions,
        # clients render the video at a wrong aspect ratio.
        data = {"chat_id": self._chat_id, "supports_streaming": "true"}
        data.update(_probe_video(path))
        if caption:
            data["caption"] = caption[:1024]
        with path.open("rb") as fh:
            resp = requests.post(
                f"{self._base}/sendVideo",
                data=data,
                files={"video": (path.name, fh, "video/mp4")},
                timeout=300,
            )
        payload = resp.json()
        if not payload.get("ok"):
            raise TelegramError(f"sendVideo failed: {payload.get('description', resp.text[:200])}")
        return datetime.fromtimestamp(payload["result"]["date"], tz=timezone.utc)

    # -- receiving ----------------------------------------------------------

    def wait_for_reply(
        self,
        after: datetime,
        timeout_secs: int = DEFAULT_TIMEOUT_SECS,
    ) -> Optional[str]:
        """Poll for an inbound reply sent after `after`; None on timeout."""
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)

        deadline = time.monotonic() + timeout_secs
        while time.monotonic() < deadline:
            for msg in self._fetch_updates(long_poll=True):
                sent_at = datetime.fromtimestamp(msg["date"], tz=timezone.utc)
                if sent_at > after:
                    return msg["text"].strip()
        return None

    def scan_queue_messages(self, since: datetime | None = None) -> list[str]:
        """Return YouTube URLs from 'queue:...' messages received since `since`."""
        since = _default_since(since, days=7)
        urls: list[str] = []
        for msg in self._journal_since(since):
            body = msg["text"].strip()
            if body.lower().startswith("queue:"):
                url = body[6:].strip().strip("'\"'‘’“”")
                if url:
                    urls.append(url)
        return urls

    def scan_for_reset(self, since: datetime | None = None) -> datetime | None:
        """Return the timestamp of the earliest 'reset' message since `since`."""
        since = _default_since(since, days=1)
        reset_times = [
            datetime.fromtimestamp(msg["date"], tz=timezone.utc)
            for msg in self._journal_since(since)
            if msg["text"].strip().lower() == "reset"
        ]
        return min(reset_times) if reset_times else None

    # -- internals ----------------------------------------------------------

    def _api(self, method: str, *, http_timeout: int = 30, **params) -> dict:
        try:
            resp = requests.post(f"{self._base}/{method}", json=params, timeout=http_timeout)
            payload = resp.json()
        except Exception as e:
            raise TelegramError(f"{method} request failed: {e}") from e
        if not payload.get("ok"):
            raise TelegramError(f"{method} failed: {payload.get('description', resp.text[:200])}")
        return payload["result"]

    def _fetch_updates(self, long_poll: bool = False) -> list[dict]:
        """Drain pending updates into the journal; return new messages from the user."""
        state = self._load_state()
        params: dict = {"offset": state["offset"], "allowed_updates": ["message"]}
        if long_poll:
            params["timeout"] = self.LONG_POLL_SECS
        try:
            updates = self._api(
                "getUpdates",
                http_timeout=self.LONG_POLL_SECS + 10 if long_poll else 30,
                **params,
            )
        except TelegramError as e:
            print(f"[telegram] poll error: {e}", flush=True)
            time.sleep(5)
            return []

        new: list[dict] = []
        for update in updates:
            state["offset"] = max(state["offset"], update["update_id"] + 1)
            msg = update.get("message") or {}
            if msg.get("chat", {}).get("id") == self._chat_id and msg.get("text"):
                entry = {"date": msg["date"], "text": msg["text"]}
                new.append(entry)
                state["messages"].append(entry)
        if updates:
            self._save_state(state)
        return new

    def _journal_since(self, since: datetime) -> list[dict]:
        self._fetch_updates()
        cutoff = since.timestamp()
        return [m for m in self._load_state()["messages"] if m["date"] > cutoff]

    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                state = json.loads(self._state_path.read_text())
                if "offset" in state and "messages" in state:
                    return state
            except Exception:
                pass
        return {"offset": 0, "messages": []}

    def _save_state(self, state: dict) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.JOURNAL_RETENTION_DAYS)).timestamp()
        state["messages"] = [m for m in state["messages"] if m["date"] >= cutoff]
        self._state_path.write_text(json.dumps(state, indent=2))


def run_setup(settings: Settings) -> None:
    """Discover the chat ID: user messages the bot, we read it off getUpdates."""
    if not settings.telegram_bot_token:
        raise TelegramError(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather first "
            "and add the token to .env."
        )
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    me = requests.get(f"{base}/getMe", timeout=30).json()
    if not me.get("ok"):
        raise TelegramError(f"Token rejected: {me.get('description', 'unknown error')}")
    bot_name = me["result"]["username"]

    print(f"Bot: @{bot_name}")
    print(f"Open https://t.me/{bot_name} and send it any message. Waiting…")

    deadline = time.monotonic() + 120
    offset = 0
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{base}/getUpdates",
            params={"offset": offset, "timeout": 20, "allowed_updates": '["message"]'},
            timeout=35,
        ).json()
        if not resp.get("ok"):
            raise TelegramError(f"getUpdates failed: {resp.get('description')}")
        for update in resp["result"]:
            offset = max(offset, update["update_id"] + 1)
            msg = update.get("message") or {}
            chat = msg.get("chat", {})
            if chat.get("type") == "private":
                chat_id = chat["id"]
                name = chat.get("first_name") or chat.get("username") or "?"
                from .poster.tiktok import _update_env
                _update_env("TELEGRAM_CHAT_ID", str(chat_id))
                print(f"Chat ID {chat_id} ({name}) saved to .env — Telegram is ready.")
                return
    raise TelegramError("Timed out waiting for a message to the bot.")


def _probe_video(path: Path) -> dict:
    """Return width/height/duration for sendVideo; empty dict if ffprobe fails."""
    import subprocess
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, timeout=30,
        )
        info = json.loads(out.stdout)
        stream = info["streams"][0]
        return {
            "width": stream["width"],
            "height": stream["height"],
            "duration": int(float(info["format"]["duration"])),
        }
    except Exception:
        return {}


def _default_since(since: datetime | None, days: int) -> datetime:
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return since


def _require_telegram(settings: Settings) -> None:
    missing = [
        k for k in ("telegram_bot_token", "telegram_chat_id")
        if not getattr(settings, k)
    ]
    if missing:
        raise TelegramError(
            f"Telegram credentials missing: {', '.join(m.upper() for m in missing)}. "
            "See docs/telegram_setup.md and run: cutter auth telegram"
        )
