# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup (new machine)

```bash
git clone https://github.com/planetguru/cutter
cd cutter
bash setup.sh   # creates venv, installs deps, adds daily cron job at 9am
```

Then fill in `.env` and run the auth commands below.

## Commands

```bash
# Install (editable) — use the project venv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Daily cron entrypoint — reads queue, processes next pending video, posts to all platforms
cutter daily
cutter daily --no-approve   # skip Telegram approval, post automatically

# Manage the video queue
cutter queue add "https://www.youtube.com/watch?v=..."
cutter queue list

# Queue a video via Telegram by sending the bot:
#   queue:https://www.youtube.com/watch?v=...
# cutter daily picks these up automatically on the next run.

# One-off pipeline (bypasses the queue)
cutter run --url "https://www.youtube.com/watch?v=..." --post all --approve
cutter run --url "https://www.youtube.com/watch?v=..." --post all
cutter run --url "https://www.youtube.com/watch?v=..." --post both   # TikTok + Instagram
cutter run --url "https://www.youtube.com/watch?v=..." --post youtube

# Preview cut points only (no clips generated)
cutter detect --url "https://www.youtube.com/watch?v=..."

# Reframe a single file to 9:16
cutter reframe path/to/clip.mp4

# List withheld clips
cutter withheld

# Authenticate
cutter auth telegram             # discover + save your Telegram chat ID
cutter auth tiktok
cutter auth instagram
cutter auth instagram --refresh   # refresh 60-day token before expiry
cutter auth youtube               # shows channel name so you can confirm the right one
```

## Architecture

The pipeline runs these stages in order, each stage caching its output so re-runs skip completed work (override with `--force`):

```
YouTube URL
  → downloader.py   →  source.mp4 + metadata.json   (yt-dlp)
  → detector.py     →  cut_points.json               (FFmpeg scdet + silencedetect)
  → slicer.py       →  raw/clip_NNN.mp4              (FFmpeg stream-copy, fast)
  → reframer.py     →  reframed/clip_NNN.mp4         (FFmpeg re-encode, 9:16 blurred background)
  → captioner.py    →  captions.json                 (Claude Haiku)
  → approver.py     →  Telegram conversation per clip (if --approve)
  → poster/         →  TikTok / Instagram / YouTube Shorts post
```

`pipeline.py` is the orchestrator — single entry point wiring all stages. The CLI in `cli.py` is a thin Click wrapper over `pipeline.run()`.

### Telegram Approval Flow

When `--approve` is passed, `pipeline.py` calls `approver.approve_clip()` for each clip before posting. The approver sends the clip video + a prompt via `telegram.TelegramClient` (Bot API long-polling — no webhook/server needed) and loops on replies:

- **yes** → approved, fall through to posting
- **no** → `state.StateStore.withhold_clip()` moves the file to `{video_id}/withheld/`, updates `approval_state.json`
- **no more today** → `AppState.pause_until_tomorrow()` sets a date in `approval_state.json`; next run checks this and resumes if it's a new day
- **title/desc/tiktok/instagram/tags: ...** → mutates the in-memory `Caption` object and re-prompts
- timeout → re-prompts up to 3 times, then withholds

### Persistent State

`state.StateStore` reads/writes `{workdir}/approval_state.json`. It tracks:
- `pending` — clip filenames not yet approved or withheld, per video ID
- `withheld` — filenames moved to `withheld/` folder
- `posted` — filenames successfully posted
- `no_more_until` — ISO date string; pipeline exits early if today ≤ this date

This means `cutter run` is idempotent: re-running the same URL resumes from where you left off.

**Working directory layout** (under `platformdirs.user_data_dir("cutter")/{video_id}/`):
- `source.mp4`, `metadata.json` — downloaded once
- `cut_points.json` — cached detection results
- `raw/` — stream-copied clips (deleted after reframing unless `--keep-raw`)
- `reframed/` — final output clips
- `withheld/` — clips you declined via Telegram
- `captions.json` — Claude-generated captions
- `../approval_state.json` — shared approval state across all videos

## Key Implementation Details

**Scene + silence detection** (`detector.py`): two FFmpeg passes — `scdet=t=12` for visual cuts, `silencedetect=n=-40dB:d=0.5` for audio gaps. Silence midpoints are preferred over scene times when within 2 s. Segments enforced to `[min_clip, max_clip]`.

**Blurred background** (`reframer.py`): FFmpeg filtergraph — source scaled up to fill 1080×1920 with `boxblur=luma_radius=30:luma_power=3` as background, original scaled-to-fit overlaid centred. Output is `libx264 -crf 23 -pix_fmt yuv420p` (required by both platforms).

**TikTok** (`poster/tiktok.py` + `pipeline._manual_tiktok_handoff`): three modes via `TIKTOK_POST_MODE`. `manual` (default) skips the TikTok API entirely — the clip file + caption are sent to Telegram for posting by hand. `inbox` uploads a draft via the API (init → chunked PUT → poll status, auto-refresh on 401) but sandbox draft notifications never arrived in testing; `direct` publishes immediately and requires a TikTok-audited production app. TikTok rejects production audits for personal/internal tools — see `docs/tiktok_oauth.md` for the full findings.

**Instagram upload** (`poster/instagram.py`): uses the **Instagram API with Instagram login** (graph.instagram.com) — no Facebook Page required (Meta has disabled Page↔IG linking for many accounts, killing the old Facebook-Login flavour). This API ingests from a public URL only, so the clip is scp'd to the media staging server (`MEDIA_*` in `.env`) → `POST /{ig-id}/media` with `media_type=REELS&video_url=…` → poll container → publish → staged file deleted. OAuth needs an HTTPS redirect: `https://cutter.chris.uk.com/instagram/callback` bounces to localhost:8080 (same trick as TikTok).

**Captions** (`captioner.py`): `claude-haiku-4-5-20251001`. Returns JSON — `tiktok_caption`, `instagram_caption`, `hashtags`. Tenacity retry for malformed JSON.

**Telegram** (`telegram.py`): Bot API with `getUpdates` long-polling — no webhook or server. Sends clips as video files directly (50 MB cap). Updates are journaled to `telegram_state.json` in the workdir because Telegram only retains unread updates ~24 h. Replaced the previous Twilio WhatsApp integration, whose sandbox membership expired 72 h after the user's last message and made sends fail silently.

**Config** (`config.py`): all credentials from `.env` via python-dotenv. Each platform's credentials validated lazily only when that feature is used.

## External Dependencies

- `ffmpeg` on `PATH` — validated at startup
- Python ≥ 3.11
- Telegram bot: see `docs/telegram_setup.md`
- TikTok API app: see `docs/tiktok_oauth.md`
- Instagram Meta app: see `docs/instagram_oauth.md`
