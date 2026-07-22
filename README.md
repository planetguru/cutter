# cutter

Turns your YouTube videos into short vertical clips and posts them to **TikTok, Instagram Reels, YouTube Shorts, and Facebook Reels** — one clip per day, with approval and edits from your phone over Telegram.

You queue a YouTube URL; cutter downloads it, detects natural cut points, slices it into clips, reframes each to 9:16, writes platform-specific captions with Claude, and — once you approve on Telegram — publishes to whichever platforms you've configured.

## How it works

```
YouTube URL
  → downloader   source.mp4 + metadata.json   (yt-dlp)
  → detector     cut_points.json              (FFmpeg scene + silence detection)
  → slicer       raw/clip_NNN.mp4             (fast stream-copy)
  → reframer     reframed/clip_NNN.mp4        (9:16: blurred background or rotated)
  → captioner    captions.json               (Claude Haiku — per-platform captions)
  → approver     Telegram conversation per clip
  → posters      TikTok / Instagram / YouTube / Facebook
```

1. Add YouTube URLs to a queue (CLI or a Telegram message).
2. A daily cron job (9am) processes the next pending clip and sends it to you on Telegram — the video plus generated captions.
3. You reply **yes** to post, **no** to skip, **no more today** to pause, or edit the captions first.
4. Approved clips post to your configured platforms. Each video's clips go out one per day, in order.

## Architecture / where it runs

cutter is designed to run on an **always-on server** (so the daily job fires whether or not your laptop is on). Two wrinkles are worth knowing:

- **Downloads run on a residential machine, not the server.** YouTube blocks datacenter/server IPs from actual video streams. So a small helper (`cutter fetch`) runs on a home/residential machine (e.g. a Mac mini): it asks the server which queued videos it's missing, downloads them locally, and pushes the files back. The server then processes them without ever contacting YouTube. See [`docs/fetch_helper.md`](docs/fetch_helper.md).
- **A read-only Telegram assistant** (`cutter assistant`, a long-running service) lets you ask freeform questions from your phone — queue status, how things work, etc. It can add videos to the queue but cannot change code or post.

Both are optional: on a residential machine with a normal IP you can skip the fetch helper and let the server download directly.

## Platform notes

| Platform | Status |
|---|---|
| **YouTube Shorts** | Full auto-posting. Its own dedicated description + title. |
| **Instagram Reels** | Full auto-posting via the Instagram-login API (no Facebook Page needed). |
| **TikTok** | **Manual** by default — TikTok won't grant API access to personal apps, so the clip + caption are sent to Telegram for you to post by hand. (`inbox`/`direct` API modes exist but need an audited app.) |
| **Facebook Reels** | Implemented, but public posting needs Meta App Review + business verification; posts to a Facebook Page. |

## Reframing modes

Each queued video is cut to 9:16 in one of two ways:

- **`blur`** (default) — the landscape frame sits centred on a blurred fill background.
- **`rotate`** — the landscape frame is rotated 90° to fill the whole screen (the viewer turns their phone). Good for detailed visuals. Queue with the `queuev:` Telegram command or `--reframe rotate`.

## Setup

### On the server (or any machine that posts)

```bash
git clone https://github.com/planetguru/cutter
cd cutter
cp .env.example .env        # then fill it in — see .env.example for every key
bash setup.sh              # venv + deps + a daily 9am cron job
```

Authenticate each platform you want (opens a browser, writes tokens back to `.env`):

```bash
source .venv/bin/activate
cutter auth telegram        # capture your Telegram chat ID (needs TELEGRAM_BOT_TOKEN)
cutter auth youtube         # prints the channel name so you can confirm it's the right one
cutter auth instagram
cutter auth facebook
cutter auth tiktok          # only if using TikTok's API modes (not needed for manual)
```

Per-platform credential setup is documented under [`docs/`](docs/): `telegram_setup.md`, `tiktok_oauth.md`, `instagram_oauth.md`, `facebook_oauth.md`.

To run the read-only assistant as a service, run `cutter assistant` under a process manager (systemd/launchd). See `docs/fetch_helper.md` for a launchd example (the same pattern applies).

### On the residential download machine (optional but recommended)

Same clone + `setup.sh` + `ffmpeg`, then run `cutter fetch --server <you>@<server>` on a schedule (launchd/cron). Full instructions in [`docs/fetch_helper.md`](docs/fetch_helper.md).

## Managing the queue

```bash
cutter queue add "https://www.youtube.com/watch?v=..."               # blurred-background 9:16
cutter queue add "https://www.youtube.com/watch?v=..." --reframe rotate   # rotated full-screen
cutter queue list
```

Or from your phone, message the Telegram bot:

```
queue:https://www.youtube.com/watch?v=...      normal 9:16 cut
queuev:https://www.youtube.com/watch?v=...     rotated full-screen cut
```

The assistant actions these immediately; the daily run also picks them up.

## Running manually

```bash
source .venv/bin/activate

cutter daily                      # process the next queued clip (what cron runs)
cutter daily --no-approve         # post without asking
cutter daily --max-clips 3        # more than one clip this run (0 = no limit)
cutter daily --post youtube       # restrict platforms (tiktok|instagram|youtube|facebook|both|all|none)

cutter run --url "..." --post all --approve          # full pipeline on one URL, bypass the queue
cutter run --url "..." --reframe rotate --post all   # rotated full-screen

cutter detect --url "..."         # preview cut points only
cutter reframe path/to/clip.mp4   # reframe a single file to 9:16
cutter withheld                   # list clips you declined
cutter reset                      # kill running jobs, wipe queue/state/downloads (fresh start)
```

## Telegram approval

For each clip you get the video plus a prompt showing the TikTok, Instagram, and YouTube captions and tags. Reply:

| Reply | Effect |
|---|---|
| `yes` | Post the clip |
| `no` | Skip (moved to the withheld folder) |
| `no more today` | Stop for the day; resume tomorrow |
| `title: ...` | Set the title (used for YouTube + shown in the prompt) |
| `desc: ...` | Set **all** platform captions at once |
| `tiktok: ...` / `instagram: ...` / `youtube: ...` | Set one platform's caption |
| `tags: #a #b #c` | Replace the hashtags |

Edits re-show the prompt for confirmation. Each platform's caption is independent — editing the TikTok caption no longer affects YouTube.

## Data, state, and logs

Working data lives in the platform data dir — **macOS:** `~/Library/Application Support/cutter/`, **Linux:** `~/.local/share/cutter/`. Each video gets a subfolder (source, clips, captions); `queue.json` and `approval_state.json` sit at the root, and re-running is idempotent (it resumes where it left off).

Cron output goes to `cutter.log` in the project directory (or `/var/log/cutter.log` on the server). Nothing sensitive is ever committed — `.env` is gitignored; use `.env.example` as the template.
