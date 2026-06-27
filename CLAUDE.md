# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable) — use the project venv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Full pipeline with WhatsApp approval before each post
cutter run --url "https://www.youtube.com/watch?v=..." --post both --approve

# Full pipeline, post automatically without approval
cutter run --url "https://www.youtube.com/watch?v=..." --post both

# Preview cut points only (no clips generated)
cutter detect --url "https://www.youtube.com/watch?v=..."

# Reframe a single file to 9:16
cutter reframe path/to/clip.mp4

# List withheld clips
cutter withheld

# Authenticate
cutter auth tiktok
cutter auth instagram
cutter auth instagram --refresh   # refresh 60-day token before expiry
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
  → approver.py     →  WhatsApp conversation per clip (if --approve)
  → poster/         →  TikTok / Instagram post
```

`pipeline.py` is the orchestrator — single entry point wiring all stages. The CLI in `cli.py` is a thin Click wrapper over `pipeline.run()`.

### WhatsApp Approval Flow

When `--approve` is passed, `pipeline.py` calls `approver.approve_clip()` for each clip before posting. The approver sends a WhatsApp message via `whatsapp.WhatsAppClient` (Twilio, polling — no webhook/server needed) and loops on replies:

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
- `withheld/` — clips you declined via WhatsApp
- `captions.json` — Claude-generated captions
- `../approval_state.json` — shared approval state across all videos

## Key Implementation Details

**Scene + silence detection** (`detector.py`): two FFmpeg passes — `scdet=t=12` for visual cuts, `silencedetect=n=-40dB:d=0.5` for audio gaps. Silence midpoints are preferred over scene times when within 2 s. Segments enforced to `[min_clip, max_clip]`.

**Blurred background** (`reframer.py`): FFmpeg filtergraph — source scaled up to fill 1080×1920 with `boxblur=luma_radius=30:luma_power=3` as background, original scaled-to-fit overlaid centred. Output is `libx264 -crf 23 -pix_fmt yuv420p` (required by both platforms).

**TikTok upload** (`poster/tiktok.py`): init → chunked PUT (10 MB) → poll status. Auto-refresh on 401.

**Instagram upload** (`poster/instagram.py`): clips staged to S3 (public-read), Meta Graph API container → poll → publish → S3 cleanup. S3 credentials only validated when `--post instagram` or `--post both` is used.

**Captions** (`captioner.py`): `claude-haiku-4-5-20251001`. Returns JSON — `tiktok_caption`, `instagram_caption`, `hashtags`. Tenacity retry for malformed JSON.

**WhatsApp** (`whatsapp.py`): Twilio REST client, polls `messages.list()` for inbound replies every 5 s. No webhook server required.

**Config** (`config.py`): all credentials from `.env` via python-dotenv. Each platform's credentials validated lazily only when that feature is used.

## External Dependencies

- `ffmpeg` on `PATH` — validated at startup
- Python ≥ 3.11
- Twilio account + WhatsApp sandbox: see `docs/whatsapp_setup.md`
- TikTok API app: see `docs/tiktok_oauth.md`
- Instagram Meta app + S3 bucket: see `docs/instagram_oauth.md`
