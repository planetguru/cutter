"""Telegram approval conversation for each clip."""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from .captioner import Caption
from .telegram import TelegramClient


class Decision(Enum):
    APPROVED = auto()
    WITHHELD = auto()
    NO_MORE_TODAY = auto()
    TIMEOUT = auto()


@dataclass
class ApprovalResult:
    decision: Decision
    caption: Caption   # may be modified by user


REPLY_TIMEOUT_SECS = 21600  # 6 hours before re-sending the prompt
MAX_REPROMPTS = 2           # give up after this many timeouts (12 hours total)


def approve_clip(
    wa: TelegramClient,
    clip_path: Path,
    caption: Caption,
    clip_index: int,
    total_clips: int,
) -> ApprovalResult:
    """
    Run the Telegram approval conversation for one clip.
    Handles inline edits (title/desc/tags), yes/no, and 'no more today'.
    Returns an ApprovalResult with the (possibly edited) caption.
    """
    current = Caption(
        title=caption.title,
        tiktok_caption=caption.tiktok_caption,
        instagram_caption=caption.instagram_caption,
        youtube_caption=caption.youtube_caption,
        hashtags=list(caption.hashtags),
    )

    for reprompt in range(MAX_REPROMPTS):
        if reprompt == 0:
            title = current.title or current.tiktok_caption.splitlines()[0].strip()
            wa.send_video(clip_path, caption=f"📹 Clip {clip_index}/{total_clips} — {title}")
        sent_at = wa.send(_build_prompt(clip_path, current, clip_index, total_clips))

        # Inner loop: handle edits without consuming a reprompt slot or
        # re-sending the full prompt. Only a timeout breaks out to re-prompt.
        while True:
            reply = wa.wait_for_reply(after=sent_at, timeout_secs=REPLY_TIMEOUT_SECS)

            if reply is None:
                break  # timeout — outer loop will reprompt

            result = _handle_reply(reply, current, clip_path, clip_index, total_clips, wa)
            if result is not None:
                return result
            # Edit was applied — advance the marker so we don't re-read it,
            # but don't re-send the full prompt; just wait for the next reply.
            sent_at = _dt.datetime.now(_dt.timezone.utc)

        if reprompt < MAX_REPROMPTS - 1:
            wa.send(f"⏰ Still waiting on clip {clip_index}/{total_clips}. Reply yes / no / no more today.")

    # Exhausted reprompts
    wa.send(f"⚠️ No response after {MAX_REPROMPTS} prompts. Skipping clip {clip_index}/{total_clips} for now.")
    return ApprovalResult(decision=Decision.TIMEOUT, caption=current)


def _handle_reply(
    reply: str,
    current: Caption,
    clip_path: Path,
    clip_index: int,
    total_clips: int,
    wa: TelegramClient,
) -> ApprovalResult | None:
    """
    Parse one reply and return a decision, or None to keep the conversation going.
    Mutates `current` for inline edits.
    """
    lower = reply.lower().strip()

    # --- Stop for today ---
    if re.search(r"\bno more today\b", lower):
        wa.send("⏸ Got it — stopping for today. I'll offer the remaining clips next time you run.")
        return ApprovalResult(decision=Decision.NO_MORE_TODAY, caption=current)

    # --- Approve ---
    if lower in ("yes", "y", "approve", "ok", "yep", "yeah", "post it", "go"):
        wa.send(f"✅ Approved! Posting clip {clip_index}/{total_clips}…")
        return ApprovalResult(decision=Decision.APPROVED, caption=current)

    # --- Withhold ---
    if lower in ("no", "n", "skip", "nope", "reject", "pass"):
        wa.send(f"🗂 Skipped. Moving clip {clip_index}/{total_clips} to withheld.")
        return ApprovalResult(decision=Decision.WITHHELD, caption=current)

    # --- Edit title ---
    m = re.match(r"(?:title|t)\s*[:\-]\s*(.+)", reply, re.IGNORECASE)
    if m:
        current.title = m.group(1).strip()
        wa.send(_build_edit_ack("Title", current.title, clip_index, total_clips))
        return None  # keep conversation open

    # --- Edit TikTok caption only ---
    m = re.match(r"(?:tiktok|tt)\s*[:\-]\s*(.+)", reply, re.IGNORECASE)
    if m:
        current.tiktok_caption = m.group(1).strip()
        wa.send(_build_edit_ack("TikTok caption", current.tiktok_caption, clip_index, total_clips))
        return None

    # --- Edit Instagram caption only ---
    m = re.match(r"(?:instagram|ig|insta)\s*[:\-]\s*(.+)", reply, re.IGNORECASE)
    if m:
        current.instagram_caption = m.group(1).strip()
        wa.send(_build_edit_ack("Instagram caption", current.instagram_caption, clip_index, total_clips))
        return None

    # --- Edit YouTube description only ---
    m = re.match(r"(?:youtube|yt)\s*[:\-]\s*(.+)", reply, re.IGNORECASE)
    if m:
        current.youtube_caption = m.group(1).strip()
        wa.send(_build_edit_ack("YouTube description", current.youtube_caption, clip_index, total_clips))
        return None

    # --- Edit description (alias for all three captions) ---
    m = re.match(r"(?:desc|description|d)\s*[:\-]\s*(.+)", reply, re.IGNORECASE)
    if m:
        current.tiktok_caption = m.group(1).strip()
        current.instagram_caption = m.group(1).strip()
        current.youtube_caption = m.group(1).strip()
        wa.send(_build_edit_ack("Description (all platforms)", current.tiktok_caption, clip_index, total_clips))
        return None

    # --- Edit hashtags ---
    m = re.match(r"(?:tags?|hashtags?|h)\s*[:\-]\s*(.+)", reply, re.IGNORECASE)
    if m:
        raw_tags = m.group(1).strip()
        current.hashtags = _parse_tags(raw_tags)
        wa.send(_build_edit_ack("Hashtags", current.hashtag_string, clip_index, total_clips))
        return None

    # --- Unrecognised ---
    wa.send(
        "🤔 Didn't understand that. Reply:\n"
        "  *yes* — post\n"
        "  *no* — skip\n"
        "  *no more today* — stop for today\n"
        "  *title: ...*\n"
        "  *desc: ...* (all platforms)\n"
        "  *tiktok: ...*\n"
        "  *instagram: ...*\n"
        "  *youtube: ...*\n"
        "  *tags: #tag1 #tag2*"
    )
    return None  # keep conversation open


def _build_prompt(
    clip_path: Path,
    caption: Caption,
    clip_index: int,
    total_clips: int,
) -> str:
    hashtag_preview = " ".join(f"#{t}" for t in caption.hashtags[:8])
    if len(caption.hashtags) > 8:
        hashtag_preview += f" (+{len(caption.hashtags) - 8} more)"

    def _trunc(text: str, limit: int = 400) -> str:
        return text if len(text) <= limit else text[:limit] + "…"

    title = caption.title or caption.tiktok_caption.splitlines()[0].strip()

    youtube_body = caption.youtube_caption or caption.tiktok_caption
    return (
        f"📹 *Clip {clip_index}/{total_clips}* — {title}\n\n"
        f"*TikTok:*\n{_trunc(caption.tiktok_caption)}\n\n"
        f"*Instagram:*\n{_trunc(caption.instagram_caption)}\n\n"
        f"*YouTube:*\n{_trunc(youtube_body)}\n\n"
        f"*Tags:* {caption.hashtag_string}\n\n"
        "Reply: *yes* · *no* · *no more today*\n"
        "Edit: *title:* · *desc:* · *tiktok:* · *instagram:* · *youtube:* · *tags:*"
    )


def _build_edit_ack(field_name: str, new_value: str, clip_index: int, total_clips: int) -> str:
    # Echo the full new value so the user can verify it (Telegram allows 4096
    # chars; captions cap at 2200, so this fits with room to spare).
    preview = new_value if len(new_value) <= 3500 else new_value[:3500] + "…"
    return (
        f"✏️ *{field_name}* updated:\n_{preview}_\n\n"
        f"Clip {clip_index}/{total_clips} — reply *yes* to post, *no* to skip, "
        "or keep editing (title / desc / tiktok / instagram / youtube / tags)."
    )


def _parse_tags(raw: str) -> list[str]:
    """Extract tag words from a string like '#foo #bar baz'."""
    tokens = re.findall(r"#?(\w+)", raw)
    return [t.lower() for t in tokens if t]
