# cutter

Downloads a YouTube video, cuts it into short vertical clips, and posts them to TikTok, Instagram Reels, and YouTube Shorts — one clip per day, with optional Telegram approval before each post.

## How it works

1. You add YouTube URLs to a queue (via the CLI or Telegram)
2. Every morning at 9am, the cron job picks the next clip from the queue, sends it to you on Telegram as a video preview
3. You reply **yes** to post it, **no** to skip it, or **no more today** to stop and resume tomorrow
4. Approved clips are posted to whichever platforms you have set up

Each YouTube video is cut into multiple clips. They go out one per day, in order, before moving on to the next video in the queue.

## Setup on a new machine

```bash
git clone https://github.com/planetguru/cutter
cd cutter
cp .env.example .env
# Edit .env and fill in your credentials (see below)
bash setup.sh
```

`setup.sh` creates the Python environment, installs dependencies, and registers a daily 9am cron job. To change the time, run `crontab -e` afterwards.

## Credentials (.env)

Fill in `.env` before running anything. Each section is only required if you're posting to that platform.

| Variable | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Required. Used to generate captions. Get one at console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather — see `docs/telegram_setup.md` |
| `TELEGRAM_CHAT_ID` | Your chat ID — discovered by `cutter auth telegram` |
| `PREVIEW_HOST` / `PREVIEW_USER` / `PREVIEW_WEBROOT` / `PREVIEW_BASE_URL` | SSH server where clip previews are hosted temporarily during approval |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | TikTok developer app credentials |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | Meta developer app credentials |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_S3_BUCKET` | S3 bucket for temporary Instagram video staging |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | Google OAuth app credentials |

## Authenticating with platforms

Run these once after filling in the client credentials above. Each opens a browser for OAuth and writes the resulting tokens back to `.env`.

```bash
source .venv/bin/activate

cutter auth youtube     # shows which channel will receive uploads — re-run if wrong
cutter auth tiktok
cutter auth instagram
cutter auth instagram --refresh   # refresh Instagram token before the 60-day expiry
```

## Managing the queue

```bash
# Add a video
cutter queue add "https://www.youtube.com/watch?v=..."

# See what's queued
cutter queue list
```

You can also queue a video from your phone by sending your Telegram bot a message:

```
queue:https://www.youtube.com/watch?v=...
```

Cutter picks it up automatically at the start of the next daily run.

## Running manually

```bash
source .venv/bin/activate

# Process the next clip in the queue (same as the cron job)
cutter daily

# Post to YouTube only (if TikTok/Instagram aren't set up yet)
cutter daily --post youtube

# Skip Telegram approval and post automatically
cutter daily --no-approve

# Process more than one clip in a single run
cutter daily --max-clips 3    # 0 = no limit

# Run the full pipeline on a specific URL, bypassing the queue
cutter run --url "https://www.youtube.com/watch?v=..." --post all --approve
```

## Telegram approval

When a clip is ready, cutter sends two messages: a video preview, then a text prompt showing the generated captions and your reply options.

| Reply | What happens |
|---|---|
| `yes` | Clip is posted |
| `no` | Clip is skipped (moved to withheld folder) |
| `no more today` | Stops for the day — resumes remaining clips tomorrow |
| `title: new title` | Updates the clip title before posting |
| `tiktok: new caption` | Updates the TikTok caption |
| `instagram: new caption` | Updates the Instagram caption |
| `tags: tag1 tag2 tag3` | Replaces the hashtags |

After making edits, the updated preview is shown again for confirmation.

## Resetting everything

```bash
cutter reset
```

Kills any in-flight processes, clears the queue, wipes approval state, and deletes all downloaded videos, clips, and captions. Use this to start completely fresh.

## Viewing withheld clips

```bash
cutter withheld
```

Withheld clips are kept in `{data dir}/{video id}/withheld/` and are never deleted automatically.

## Data directory

All downloaded videos, clips, and state are stored in:

- **macOS:** `~/Library/Application Support/cutter/`
- **Linux:** `~/.local/share/cutter/`

Each YouTube video gets its own subfolder named after its video ID. The queue and approval state are stored as JSON files in the root of that directory.

## Logs

When running via cron, output is written to `cutter.log` in the project directory. Check it if something isn't posting as expected.

```bash
tail -f /path/to/cutter/cutter.log
```
