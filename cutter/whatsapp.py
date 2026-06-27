"""Twilio WhatsApp client — send messages and poll for replies."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from twilio.rest import Client

from .config import Settings


class WhatsAppError(Exception):
    pass


class WhatsAppClient:
    POLL_INTERVAL_SECS = 5
    DEFAULT_TIMEOUT_SECS = 600  # 10 minutes per prompt before re-sending

    def __init__(self, settings: Settings) -> None:
        _require_whatsapp(settings)
        self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        self._from = settings.twilio_whatsapp_from
        self._to = settings.twilio_whatsapp_to

    def send(self, body: str, media_url: str | None = None) -> datetime:
        """Send a WhatsApp message (optionally with a media attachment)."""
        kwargs: dict = {"from_": self._from, "to": self._to, "body": body}
        if media_url:
            kwargs["media_url"] = [media_url]
        try:
            msg = self._client.messages.create(**kwargs)
        except Exception as e:
            raise WhatsAppError(f"Failed to send message: {e}") from e

        return msg.date_created or datetime.now(timezone.utc)

    def wait_for_reply(
        self,
        after: datetime,
        timeout_secs: int = DEFAULT_TIMEOUT_SECS,
    ) -> Optional[str]:
        """
        Poll for an inbound reply sent after `after`.
        Returns the message body stripped, or None on timeout.
        """
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)

        deadline = time.monotonic() + timeout_secs
        while time.monotonic() < deadline:
            try:
                # Fetch recent messages broadly — filter in Python to avoid
                # Twilio API quirks with whatsapp: prefix on from_/to params.
                messages = self._client.messages.list(limit=20)
            except Exception as e:
                print(f"[whatsapp] poll error: {e}", flush=True)
                time.sleep(self.POLL_INTERVAL_SECS)
                continue

            for msg in messages:
                sent_at = msg.date_created
                if sent_at and sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)

                is_from_user = (msg.from_ or "").replace(" ", "") == self._to.replace(" ", "")
                is_after = sent_at is not None and sent_at > after
                is_inbound = msg.direction == "inbound"

                if is_from_user and is_after and is_inbound:
                    return msg.body.strip()

            time.sleep(self.POLL_INTERVAL_SECS)

        return None


def _require_whatsapp(settings: Settings) -> None:
    missing = [
        k for k in ("twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_from", "twilio_whatsapp_to")
        if not getattr(settings, k)
    ]
    if missing:
        raise WhatsAppError(
            f"WhatsApp credentials missing: {', '.join(m.upper() for m in missing)}. "
            "See docs/whatsapp_setup.md and add them to .env."
        )
