"""WhatsApp approval conversation for each clip."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from .captioner import Caption
from .config import Settings
from .whatsapp import WhatsAppClient


class Decision(Enum):
    APPROVED = auto()
    WITHHELD = auto()
    NO_MORE_TODAY = auto()
    TIMEOUT = auto()


@dataclass
class ApprovalResult:
    decision: Decision
    caption: Caption   # may be modified by user


REPLY_TIMEOUT_SECS = 600   # 10 min before re-sending the prompt
MAX_REPROMPTS = 3          # give up after this many timeouts with no reply


def approve_clip(
    wa: WhatsAppClient,
    clip_path: Path,
    caption: Caption,
    clip_index: int,
    total_clips: int,
    settings: Settings | None = None,
) -> ApprovalResult:
    """
    Run the WhatsApp approval conversation for one clip.
    Handles inline edits (title/desc/tags), yes/no, and 'no more today'.
    Returns an ApprovalResult with the (possibly edited) caption.
    """
    current = Caption(
        tiktok_caption=caption.tiktok_caption,
        instagram_caption=caption.instagram_caption,
        hashtags=list(caption.hashtags),
    )

    remote_name = _upload_to_server(clip_path, settings) if settings and settings.preview_host else None
    video_url = f"{settings.preview_base_url}/{remote_name}" if remote_name else None

    try:
        for reprompt in range(MAX_REPROMPTS):
            # Video and text must be separate messages — WhatsApp drops the body
            # when a video media_url is present.
            if video_url and reprompt == 0:
                wa.send(media_url=video_url)
            sent_at = wa.send(_build_prompt(clip_path, current, clip_index, total_clips))
            reply = wa.wait_for_reply(after=sent_at, timeout_secs=REPLY_TIMEOUT_SECS)

            if reply is None:
                if reprompt < MAX_REPROMPTS - 1:
                    wa.send(f"⏰ Still waiting on clip {clip_index}/{total_clips}. Reply yes / no / no more today.")
                continue

            result = _handle_reply(reply, current, clip_path, clip_index, total_clips, wa)
            if result is not None:
                return result

        # Exhausted reprompts
        wa.send(f"⚠️ No response after {MAX_REPROMPTS} prompts. Skipping clip {clip_index}/{total_clips} for now.")
        return ApprovalResult(decision=Decision.TIMEOUT, caption=current)
    finally:
        if remote_name and settings:
            _delete_from_server(remote_name, settings)


def _handle_reply(
    reply: str,
    current: Caption,
    clip_path: Path,
    clip_index: int,
    total_clips: int,
    wa: WhatsAppClient,
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

    # --- Edit description (alias for both captions) ---
    m = re.match(r"(?:desc|description|d)\s*[:\-]\s*(.+)", reply, re.IGNORECASE)
    if m:
        current.tiktok_caption = m.group(1).strip()
        current.instagram_caption = m.group(1).strip()
        wa.send(_build_edit_ack("Description", current.tiktok_caption, clip_index, total_clips))
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
        "  *desc: ...*\n"
        "  *tiktok: ...*\n"
        "  *instagram: ...*\n"
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

    return (
        f"📹 *Clip {clip_index}/{total_clips}* — {title}\n\n"
        f"*TikTok:*\n{_trunc(caption.tiktok_caption)}\n\n"
        f"*Instagram:*\n{_trunc(caption.instagram_caption)}\n\n"
        f"*Tags:* {caption.hashtag_string}\n\n"
        "Reply: *yes* · *no* · *no more today*\n"
        "Edit: *title:* · *desc:* · *tiktok:* · *instagram:* · *tags:*"
    )


def _build_edit_ack(field_name: str, new_value: str, clip_index: int, total_clips: int) -> str:
    preview = new_value[:200] + ("…" if len(new_value) > 200 else "")
    return (
        f"✏️ *{field_name}* updated:\n_{preview}_\n\n"
        f"Clip {clip_index}/{total_clips} — reply *yes* to post, *no* to skip, "
        "or keep editing (title / desc / tiktok / instagram / tags)."
    )



def _upload_to_server(clip_path: Path, settings: Settings) -> str | None:
    """
    Compress clip to 540x960 and SCP to the configured preview server.
    Returns the remote filename (not full URL), or None on failure.
    """
    remote_name = f"{clip_path.stem}_preview.mp4"
    ssh_key = str(Path(settings.preview_ssh_key).expanduser())
    dest = f"{settings.preview_user}@{settings.preview_host}:{settings.preview_webroot}/{remote_name}"

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        preview_path = Path(tmp.name)

    try:
        # 540x960 half-res 9:16, CRF 30 — ~3-6 MB for a 30-55s clip
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(clip_path),
                "-vf", "scale=540:960",
                "-c:v", "libx264", "-preset", "fast", "-crf", "30",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                "-y", str(preview_path),
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not preview_path.exists():
            print("[approver] ffmpeg preview encode failed")
            return None

        size_mb = preview_path.stat().st_size / 1_048_576
        if size_mb > 15.5:
            print(f"[approver] preview too large ({size_mb:.1f} MB), skipping video attachment")
            return None

        scp_result = subprocess.run(
            ["scp", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             str(preview_path), dest],
            capture_output=True,
            timeout=120,
        )
        if scp_result.returncode != 0:
            print(f"[approver] scp failed: {scp_result.stderr.decode().strip()}")
            return None

        # Ensure the file is world-readable (web server may run as non-root).
        subprocess.run(
            ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             f"{settings.preview_user}@{settings.preview_host}",
             f"chmod 644 {settings.preview_webroot}/{remote_name}"],
            capture_output=True, timeout=15,
        )

        print(f"[approver] uploaded preview: {settings.preview_base_url}/{remote_name}")
        return remote_name

    except Exception as e:
        print(f"[approver] upload failed: {e}")
        return None
    finally:
        preview_path.unlink(missing_ok=True)


def _delete_from_server(remote_name: str, settings: Settings) -> None:
    """Delete a preview clip from the preview server after approval decision."""
    ssh_key = str(Path(settings.preview_ssh_key).expanduser())
    try:
        subprocess.run(
            ["ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             f"{settings.preview_user}@{settings.preview_host}",
             f"rm -f {settings.preview_webroot}/{remote_name}"],
            capture_output=True,
            timeout=15,
        )
        print(f"[approver] deleted preview: {remote_name}")
    except Exception as e:
        print(f"[approver] server cleanup failed: {e}")


def _parse_tags(raw: str) -> list[str]:
    """Extract tag words from a string like '#foo #bar baz'."""
    tokens = re.findall(r"#?(\w+)", raw)
    return [t.lower() for t in tokens if t]
