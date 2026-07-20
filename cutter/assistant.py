"""Read-only Telegram assistant — answers freeform questions about cutter.

Runs as a long-lived service (systemd) on the server, polling the same Telegram
bot used for approvals. It is strictly read-only: it gathers live state (queue,
approval state, recent log) plus the project's own CLAUDE.md and asks Claude to
answer. It has no ability to edit code, run commands, or change any state — the
model only ever returns text.

It yields the Telegram poller to `cutter daily` (detected via the shared flock
lock) so the daily approval conversation is never disrupted, and it leaves
`queue:` / `reset` messages alone for `cutter daily` to consume.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

import anthropic

from .config import Settings, get_settings
from .telegram import TelegramClient

LOCK_PATH = os.getenv("CUTTER_LOCK", "/run/cutter.lock")
LOG_PATH = os.getenv("CUTTER_LOG", "/var/log/cutter.log")
CLAUDE_MD = Path(__file__).parent.parent / "CLAUDE.md"

ASSISTANT_SYSTEM = """You are the assistant for "cutter", a personal tool that turns YouTube videos \
into short vertical clips and posts them to TikTok, Instagram Reels, and YouTube Shorts. You are \
talking to Chris (the owner) over Telegram, from his phone.

Your role is essentially read-only: answer questions, explain how things work, report the current \
status, and give advice. The one action you can take is adding a video to the queue — Chris does that \
by sending a message starting with "queue:" followed by a YouTube URL, which is handled automatically \
(he doesn't need you to do anything). You cannot edit code, run commands, post clips, or change any \
other state — if Chris wants an actual change made, tell him to bring it up in a Claude Code session \
on the project (that's where changes get made). Don't pretend to have taken an action you can't take.

Style: this is a phone chat. Be concise and direct — a few sentences, not an essay. Plain text is \
fine; avoid heavy Markdown. Use the live state and project reference below; if you genuinely don't \
know, say so rather than guessing about his specific setup."""


def run_assistant() -> None:
    settings = get_settings()
    settings.require_anthropic()
    tg = TelegramClient(settings)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    claude_md = CLAUDE_MD.read_text() if CLAUDE_MD.exists() else ""
    model = settings.assistant_model
    print(f"[assistant] started (model={model}); polling Telegram…", flush=True)

    while True:
        if _daily_running():
            # cutter daily owns the Telegram conversation while it runs.
            time.sleep(5)
            continue

        try:
            new = tg.poll_new_messages()
        except Exception as e:  # never let a transient error kill the service
            print(f"[assistant] poll error: {e}", flush=True)
            time.sleep(5)
            continue

        for msg in new:
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            low = text.lower()
            # queue: commands are actioned immediately so the queue reflects them
            # right away (dedup means the 9am daily scan won't re-add them).
            if low.startswith("queue:"):
                _handle_queue(tg, text)
                continue
            # `reset` is destructive — leave it for `cutter daily` to handle.
            if low == "reset":
                continue
            print(f"[assistant] answering: {text[:60]!r}", flush=True)
            try:
                answer = _answer(client, model, claude_md, text)
            except Exception as e:
                print(f"[assistant] answer error: {e}", flush=True)
                answer = "Sorry — I hit an error answering that. Try again in a moment."
            tg.send(answer[:3900])


def _handle_queue(tg: TelegramClient, text: str) -> None:
    """Add a `queue:<url>` command to the video queue immediately."""
    from . import queue as q

    url = text[len("queue:"):].strip().strip("'\"'‘’“”")
    if not url:
        tg.send("Send a URL after queue: — e.g. queue:https://youtu.be/…")
        return
    try:
        added = q.add(url)
    except Exception as e:
        print(f"[assistant] queue add error: {e}", flush=True)
        tg.send(f"Couldn't add that to the queue: {e}")
        return
    if added:
        print(f"[assistant] queued: {url}", flush=True)
        tg.send(f"✅ Added to the queue: {url}\nIt'll be processed on the next daily run (9am UK).")
    else:
        tg.send(f"That URL is already in the queue: {url}")


def _answer(client: anthropic.Anthropic, model: str, claude_md: str, question: str) -> str:
    system = [{
        "type": "text",
        "text": f"{ASSISTANT_SYSTEM}\n\n# Project reference (CLAUDE.md)\n{claude_md}",
        "cache_control": {"type": "ephemeral"},
    }]
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        system=system,
        messages=[{
            "role": "user",
            "content": f"# Live state\n{_gather_state()}\n\n# Chris asks\n{question}",
        }],
    )
    parts = [b.text for b in resp.content if b.type == "text"]
    return "\n".join(parts).strip() or "(no answer)"


def _gather_state() -> str:
    import platformdirs

    data_dir = Path(platformdirs.user_data_dir("cutter"))
    lines: list[str] = []

    queue = data_dir / "queue.json"
    lines.append("## queue.json\n" + (queue.read_text() if queue.exists() else "(none)"))

    state = data_dir / "approval_state.json"
    lines.append("## approval_state.json\n" + (state.read_text() if state.exists() else "(none)"))

    try:
        log_lines = Path(LOG_PATH).read_text().splitlines()[-40:]
        lines.append("## recent cutter.log (last 40 lines)\n" + "\n".join(log_lines))
    except Exception:
        lines.append("## recent cutter.log\n(unavailable)")

    return "\n\n".join(lines)


def _daily_running() -> bool:
    """True if `cutter daily` currently holds the shared flock lock."""
    try:
        fd = open(LOCK_PATH, "a")
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        fd.close()
