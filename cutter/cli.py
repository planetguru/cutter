"""CLI entry point for cutter."""

from __future__ import annotations

from pathlib import Path

import click
import platformdirs
from rich.console import Console
from rich.table import Table

from .config import ConfigError, check_ffmpeg, get_settings
from .pipeline import PipelineOptions, run

console = Console()


@click.group()
def main() -> None:
    """Cut YouTube videos into TikTok/Instagram short clips."""


# ---------------------------------------------------------------------------
# cutter reset
# ---------------------------------------------------------------------------

@main.command()
def reset() -> None:
    """Kill any running cutter processes and delete all data, ready for a fresh start."""
    import glob
    import json
    import os
    import shutil
    import signal
    import subprocess

    # Kill any other cutter processes (daily runs, pipelines, etc.)
    killed = 0
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cutter"],
            capture_output=True, text=True,
        )
        my_pid = os.getpid()
        for pid_str in result.stdout.splitlines():
            pid = int(pid_str.strip())
            if pid != my_pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                except ProcessLookupError:
                    pass
    except Exception:
        pass

    if killed:
        console.print(f"[yellow]Killed {killed} running process(es).[/yellow]")
    else:
        console.print("[dim]No running cutter processes found.[/dim]")

    workdir = Path(platformdirs.user_data_dir("cutter"))

    # Reset queue and approval state.
    # Set last_message_scan to now so we don't re-ingest old queue messages.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    (workdir / "queue.json").write_text(
        json.dumps({"last_message_scan": now_iso, "items": []}, indent=2)
    )
    (workdir / "approval_state.json").write_text(
        json.dumps({"no_more_until": None, "videos": {}}, indent=2)
    )

    # Delete all video data (downloads, clips, captions)
    deleted = 0
    for item in workdir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
            deleted += 1

    console.print(f"[green]Cleared queue, state, and {deleted} video folder(s).[/green]")
    console.print("[dim]Ready for a fresh start.[/dim]")


# ---------------------------------------------------------------------------
# cutter run
# ---------------------------------------------------------------------------

@main.command()
@click.option("--url", required=True, help="YouTube video URL")
@click.option("--post", default="none", type=click.Choice(["tiktok", "instagram", "youtube", "facebook", "both", "all", "none"]), show_default=True, help="Platform(s) to post to: tiktok, instagram, youtube, both (tiktok+instagram), all (all three), or none")
@click.option("--approve/--no-approve", default=False, help="Ask for Telegram approval before each post")
@click.option("--reframe", default="blur", type=click.Choice(["blur", "rotate"]), show_default=True,
              help="9:16 mode: blur (landscape on blurred background) or rotate (90° full-screen)")
@click.option("--min-clip", default=25, show_default=True, help="Minimum clip length in seconds")
@click.option("--max-clip", default=55, show_default=True, help="Maximum clip length in seconds")
@click.option("--scene-threshold", default=12.0, show_default=True, help="scdet threshold (0–100)")
@click.option("--silence-db", default=-40.0, show_default=True, help="Silence noise floor in dB")
@click.option("--no-captions", is_flag=True, help="Skip Claude caption generation")
@click.option("--keep-raw", is_flag=True, help="Keep intermediate raw (un-reframed) clips")
@click.option("--force", is_flag=True, help="Ignore cached detection and re-run all stages")
@click.option("--workdir", default=None, help="Override working directory")
def run_cmd(
    url: str,
    post: str,
    approve: bool,
    reframe: str,
    min_clip: int,
    max_clip: int,
    scene_threshold: float,
    silence_db: float,
    no_captions: bool,
    keep_raw: bool,
    force: bool,
    workdir: str | None,
) -> None:
    """Download a YouTube video and cut it into short clips."""
    try:
        check_ffmpeg()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    options = PipelineOptions(
        min_clip_secs=float(min_clip),
        max_clip_secs=float(max_clip),
        scene_threshold=scene_threshold,
        silence_db=silence_db,
        post=post,
        approve=approve,
        reframe=reframe,
        captions=not no_captions,
        keep_raw=keep_raw,
        force=force,
        workdir=Path(workdir) if workdir else Path(platformdirs.user_data_dir("cutter")),
    )

    try:
        results, _ = run(url, options)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    if not results:
        return

    table = Table(title=f"Results — {len(results)} clips")
    table.add_column("#", style="dim")
    table.add_column("File")
    table.add_column("Status")
    table.add_column("TikTok")
    table.add_column("Instagram")
    table.add_column("YouTube")
    table.add_column("Facebook")

    for i, r in enumerate(results, 1):
        if r.withheld:
            status = "[yellow]withheld[/yellow]"
        elif r.skipped_today:
            status = "[dim]skipped today[/dim]"
        else:
            status = "[green]approved[/green]" if r.post_results or options.post == "none" else "[blue]ready[/blue]"

        tiktok_col = _post_status(r.post_results, "tiktok")
        instagram_col = _post_status(r.post_results, "instagram")
        youtube_col = _post_status(r.post_results, "youtube")
        facebook_col = _post_status(r.post_results, "facebook")
        table.add_row(str(i), r.clip_path.name, status, tiktok_col, instagram_col, youtube_col, facebook_col)

    console.print(table)

    withheld = [r for r in results if r.withheld]
    skipped = [r for r in results if r.skipped_today]
    posted = [r for r in results if r.post_results and any(p.success for p in r.post_results)]

    console.print(
        f"\n[green]{len(posted)} posted[/green]  "
        f"[yellow]{len(withheld)} withheld[/yellow]  "
        f"[dim]{len(skipped)} held for tomorrow[/dim]"
    )
    if results:
        console.print(f"Clips folder: {results[0].clip_path.parent}")


def _post_status(post_results: list, platform: str) -> str:
    for r in post_results:
        if r.platform == platform:
            if r.success:
                return r.url or "[green]✓[/green]"
            return f"[red]✗[/red] {r.error[:50]}"
    return "—"


# ---------------------------------------------------------------------------
# cutter detect
# ---------------------------------------------------------------------------

@main.command()
@click.option("--url", required=True, help="YouTube video URL")
@click.option("--scene-threshold", default=12.0, show_default=True)
@click.option("--silence-db", default=-40.0, show_default=True)
@click.option("--min-clip", default=25, show_default=True)
@click.option("--max-clip", default=55, show_default=True)
def detect(
    url: str,
    scene_threshold: float,
    silence_db: float,
    min_clip: int,
    max_clip: int,
) -> None:
    """Download a video and print detected cut points (no clip generation)."""
    from . import detector, downloader

    workdir = Path(platformdirs.user_data_dir("cutter"))
    try:
        check_ffmpeg()
        with console.status("Downloading metadata…"):
            asset = downloader.download(url, workdir)
        with console.status("Detecting cut points…"):
            clips = detector.detect(
                asset.local_path,
                workdir,
                asset.video_id,
                scene_threshold=scene_threshold,
                silence_db=silence_db,
                min_clip_secs=float(min_clip),
                max_clip_secs=float(max_clip),
            )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    table = Table(title=f"Cut points — {len(clips)} clips")
    table.add_column("#")
    table.add_column("Start (s)")
    table.add_column("End (s)")
    table.add_column("Duration (s)")
    for i, (start, end) in enumerate(clips, 1):
        table.add_row(str(i), f"{start:.1f}", f"{end:.1f}", f"{end - start:.1f}")
    console.print(table)


# ---------------------------------------------------------------------------
# cutter reframe
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", default=None, help="Output path (default: <file>_reframed.mp4)")
def reframe(file: Path, output: str | None) -> None:
    """Reframe a single video file to 9:16 with blurred background."""
    from . import reframer

    out_path = Path(output) if output else file.with_stem(file.stem + "_reframed")
    try:
        with console.status(f"Reframing {file.name}…"):
            reframer.reframe(file, out_path)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
    console.print(f"[green]Done:[/green] {out_path}")


# ---------------------------------------------------------------------------
# cutter withheld
# ---------------------------------------------------------------------------

@main.command()
@click.option("--workdir", default=None, help="Override working directory")
def withheld(workdir: str | None) -> None:
    """List all withheld clips across all videos."""
    from .state import StateStore

    wd = Path(workdir) if workdir else Path(platformdirs.user_data_dir("cutter"))
    store = StateStore(wd)

    table = Table(title="Withheld clips")
    table.add_column("Video ID")
    table.add_column("Clip")
    table.add_column("File exists")

    any_found = False
    for vid, vs in store.state.videos.items():
        for clip_name in vs.withheld:
            clip_file = wd / vid / "withheld" / clip_name
            exists = "[green]yes[/green]" if clip_file.exists() else "[red]no[/red]"
            table.add_row(vid, clip_name, exists)
            any_found = True

    if any_found:
        console.print(table)
    else:
        console.print("[dim]No withheld clips.[/dim]")


# ---------------------------------------------------------------------------
# cutter auth
# ---------------------------------------------------------------------------

@main.group()
def auth() -> None:
    """Authenticate with posting platforms."""


@auth.command()
def tiktok() -> None:
    """Run TikTok OAuth flow and save tokens to .env."""
    from .poster.tiktok import TikTokError, run_oauth_flow

    settings = get_settings()
    try:
        run_oauth_flow(settings)
    except TikTokError as e:
        console.print(f"[red]TikTok auth error:[/red] {e}")
        raise SystemExit(1)


@auth.command()
@click.option("--refresh", is_flag=True, help="Refresh an existing long-lived token")
def instagram(refresh: bool) -> None:
    """Run Instagram OAuth flow and save token to .env."""
    from .poster.instagram import InstagramError, run_oauth_flow

    settings = get_settings()
    try:
        run_oauth_flow(settings, refresh=refresh)
    except InstagramError as e:
        console.print(f"[red]Instagram auth error:[/red] {e}")
        raise SystemExit(1)


@auth.command()
def telegram() -> None:
    """Discover and save your Telegram chat ID (needs TELEGRAM_BOT_TOKEN in .env)."""
    from .telegram import TelegramError, run_setup

    settings = get_settings()
    try:
        run_setup(settings)
    except TelegramError as e:
        console.print(f"[red]Telegram setup error:[/red] {e}")
        raise SystemExit(1)


@auth.command()
def facebook() -> None:
    """Run Facebook Login and save the Page token for Reels posting."""
    from .poster.facebook import FacebookError, run_oauth_flow

    settings = get_settings()
    try:
        run_oauth_flow(settings)
    except FacebookError as e:
        console.print(f"[red]Facebook auth error:[/red] {e}")
        raise SystemExit(1)


@auth.command()
def youtube() -> None:
    """Run YouTube OAuth flow and save tokens to .env."""
    from .poster.youtube import YouTubeError, run_oauth_flow

    settings = get_settings()
    try:
        run_oauth_flow(settings)
    except YouTubeError as e:
        console.print(f"[red]YouTube auth error:[/red] {e}")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# cutter queue
# ---------------------------------------------------------------------------

@main.group()
def queue() -> None:
    """Manage the video URL queue."""


@queue.command(name="add")
@click.argument("url")
@click.option("--reframe", default="blur", type=click.Choice(["blur", "rotate"]), show_default=True,
              help="9:16 mode: blur (landscape on blurred background) or rotate (90° full-screen)")
def queue_add(url: str, reframe: str) -> None:
    """Add a YouTube URL to the processing queue."""
    from . import queue as q

    if q.add(url, reframe=reframe):
        console.print(f"[green]Queued:[/green] {url} [dim]({reframe})[/dim]")
    else:
        console.print(f"[yellow]Already in queue:[/yellow] {url}")


@queue.command(name="list")
def queue_list() -> None:
    """Show all queued videos and their status."""
    from . import queue as q

    items = q.list_all()
    if not items:
        console.print("[dim]Queue is empty. Add a URL with: cutter queue add <url>[/dim]")
        return

    table = Table(title="Video queue")
    table.add_column("Status")
    table.add_column("URL")
    table.add_column("Mode")
    table.add_column("Added")
    table.add_column("Used")

    for item in items:
        if item.status == "pending":
            status = "[green]pending[/green]"
        else:
            status = "[dim]used[/dim]"
        added = item.added[:10]
        used = item.used[:10] if item.used else "—"
        mode = getattr(item, "reframe", "blur")
        table.add_row(status, item.url, mode, added, used)

    console.print(table)

    pending = sum(1 for i in items if i.status == "pending")
    console.print(f"\n[green]{pending} pending[/green]  [dim]{len(items) - pending} used[/dim]")


# ---------------------------------------------------------------------------
# cutter pending-downloads / cutter fetch  (residential download helper)
# ---------------------------------------------------------------------------

@main.command(name="pending-downloads")
def pending_downloads() -> None:
    """Print (as JSON) pending queued videos whose source isn't on this machine yet.

    Run on the server; the residential `cutter fetch` helper consumes this."""
    import json

    from . import queue as q
    from .downloader import _video_id_from_url

    workdir = Path(platformdirs.user_data_dir("cutter"))
    needed = []
    for item in q.list_all():
        if item.status != "pending":
            continue
        vid = _video_id_from_url(item.url)
        have = bool(vid) and (workdir / vid / "source.mp4").exists() and (workdir / vid / "metadata.json").exists()
        if not have:
            needed.append({"url": item.url, "video_id": vid})
    print(json.dumps(needed))


@main.command(name="fetch")
@click.option("--server", envvar="FETCH_SERVER", required=True,
              help="SSH target of the cutter server, e.g. root@chris.uk.com (or set FETCH_SERVER)")
@click.option("--remote-cutter", envvar="FETCH_REMOTE_CUTTER",
              default="HOME=/root /opt/cutter/.venv/bin/cutter",
              help="How to invoke cutter on the server")
@click.option("--remote-datadir", envvar="FETCH_REMOTE_DATADIR",
              default="/root/.local/share/cutter",
              help="cutter data dir on the server")
def fetch(server: str, remote_cutter: str, remote_datadir: str) -> None:
    """Download the server's pending videos here (residential IP) and push them back.

    Run this on a home/residential machine — YouTube blocks the server's datacenter
    IP from video streams, but a normal home connection works fine. The server's
    downloader then finds source.mp4 already present and skips its own download."""
    import json
    import shutil
    import subprocess

    from . import downloader

    try:
        check_ffmpeg()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
    SCP = ["scp", "-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]

    # 1. Ask the server what it still needs.
    res = subprocess.run(SSH + [server, f"{remote_cutter} pending-downloads"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        console.print(f"[red]Could not reach server:[/red] {res.stderr.strip()[:300]}")
        raise SystemExit(1)
    try:
        items = json.loads(res.stdout.strip() or "[]")
    except json.JSONDecodeError:
        console.print(f"[red]Unexpected server response:[/red] {res.stdout.strip()[:300]}")
        raise SystemExit(1)
    if not items:
        console.print("[dim]Nothing to fetch — server has all pending sources.[/dim]")
        return

    local = Path(platformdirs.user_data_dir("cutter")) / "fetch"
    console.print(f"[dim]{len(items)} video(s) to fetch.[/dim]")

    for it in items:
        url = it["url"]
        try:
            console.print(f"Downloading {url} …")
            asset = downloader.download(url, local)
        except Exception as e:
            console.print(f"[red]Download failed[/red] {url}: {str(e)[:200]}")
            continue

        vid = asset.video_id
        d = local / vid
        src, meta = d / "source.mp4", d / "metadata.json"
        if not (src.exists() and meta.exists()):
            console.print(f"[red]Missing files after download[/red] {vid}")
            continue

        remote_dir = f"{remote_datadir}/{vid}"
        try:
            subprocess.run(SSH + [server, f"mkdir -p {remote_dir}"], check=True,
                           capture_output=True, text=True)
            subprocess.run(SCP + [str(src), f"{server}:{remote_dir}/source.mp4.part"], check=True,
                           capture_output=True, text=True)
            subprocess.run(SCP + [str(meta), f"{server}:{remote_dir}/metadata.json.part"], check=True,
                           capture_output=True, text=True)
            # Atomically swap both into place so the server never sees a half file.
            subprocess.run(
                SSH + [server,
                       f"mv {remote_dir}/source.mp4.part {remote_dir}/source.mp4 && "
                       f"mv {remote_dir}/metadata.json.part {remote_dir}/metadata.json"],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Push failed[/red] {vid}: {(e.stderr or '').strip()[:200]}")
            continue

        console.print(f"[green]Pushed[/green] {vid} → server")
        shutil.rmtree(d, ignore_errors=True)  # local scratch no longer needed


# ---------------------------------------------------------------------------
# cutter assistant
# ---------------------------------------------------------------------------

@main.command()
def assistant() -> None:
    """Run the read-only Telegram Q&A assistant (long-lived; for systemd)."""
    from .assistant import run_assistant

    try:
        run_assistant()
    except KeyboardInterrupt:
        console.print("[dim]assistant stopped.[/dim]")


# ---------------------------------------------------------------------------
# cutter daily
# ---------------------------------------------------------------------------

@main.command()
@click.option("--post", default="all",
              type=click.Choice(["tiktok", "instagram", "youtube", "facebook", "both", "all", "none"]),
              show_default=True)
@click.option("--approve/--no-approve", default=True,
              help="Ask for Telegram approval before posting (default: on)")
@click.option("--max-clips", default=1, show_default=True,
              help="Max clips to POST per run (0 = no limit); rejected ('no') clips don't count")
def daily(post: str, approve: bool, max_clips: int) -> None:
    """Process the next queued video. Designed to run from cron once a day."""
    import json
    import os
    import re

    from . import queue as q
    from .config import check_ffmpeg
    from .state import StateStore

    try:
        check_ffmpeg()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    settings = get_settings()
    workdir = Path(platformdirs.user_data_dir("cutter"))

    # Check Telegram for commands (best-effort)
    try:
        from .telegram import TelegramClient
        wa = TelegramClient(settings)
        since = q.get_last_message_scan()

        # Reset command takes priority — but honour any queue: messages sent after it
        reset_at = wa.scan_for_reset(since=since)
        if reset_at is not None:
            import shutil
            import signal
            import subprocess as _sp
            killed = 0
            try:
                result = _sp.run(["pgrep", "-f", "cutter"], capture_output=True, text=True)
                my_pid = os.getpid()
                for pid_str in result.stdout.splitlines():
                    pid = int(pid_str.strip())
                    if pid != my_pid:
                        try:
                            os.kill(pid, signal.SIGTERM)
                            killed += 1
                        except ProcessLookupError:
                            pass
            except Exception:
                pass
            from datetime import datetime, timezone as _tz
            now_iso = datetime.now(_tz.utc).isoformat()
            (workdir / "queue.json").write_text(
                json.dumps({"last_message_scan": now_iso, "items": []}, indent=2)
            )
            (workdir / "approval_state.json").write_text(
                json.dumps({"no_more_until": None, "videos": {}}, indent=2)
            )
            deleted = 0
            for item in workdir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                    deleted += 1
            # Pick up any queue: messages sent after the reset message
            post_reset_urls = wa.scan_queue_messages(since=reset_at)
            for url, reframe in post_reset_urls:
                if q.add(url, reframe=reframe):
                    console.print(f"[dim]Queued from message (post-reset): {url} ({reframe})[/dim]")
            reply = "Reset complete. Cleared queue and {} video folder(s).".format(deleted)
            if post_reset_urls:
                reply += " Picked up {} queued URL(s) sent after the reset.".format(len(post_reset_urls))
            else:
                reply += " Send queue:URL to start again."
            wa.send(reply)
            console.print("[green]Reset triggered via message.[/green]")
            return

        new_urls = wa.scan_queue_messages(since=since)
        q.update_last_message_scan()
        for url, reframe in new_urls:
            if q.add(url, reframe=reframe):
                console.print(f"[dim]Queued from message: {url} ({reframe})[/dim]")
    except Exception:
        pass

    from .pipeline import RunOutcome

    # Best-effort messenger for cross-video / end-of-queue notes.
    notify = None
    try:
        from .telegram import TelegramClient as _TG
        notify = _TG(settings)
    except Exception:
        notify = None

    def _mark_used_if_done(url: str) -> None:
        """Mark the queue URL used once its approval-state pending list is empty."""
        m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', url)
        vid = m.group(1) if m else None
        vs = StateStore(workdir).state.videos.get(vid) if vid else None
        if not (vs.pending if vs else []):
            q.mark_used(url)
            console.print(f"[dim]Marked as used: {url}[/dim]")

    # Offer clips until one is posted, the user pauses, or the queue runs dry.
    # A "no → next" that exhausts one video's clips advances to the next video.
    first = True
    seen: set[str] = set()
    while True:
        item = q.next_pending_item()
        if item is None:
            console.print("[dim]Queue is empty — nothing to offer.[/dim]")
            if not first and notify:
                try:
                    notify.send("That's the whole queue — nothing more to offer today.")
                except Exception:
                    pass
            break

        url = item.url
        if not first and notify:
            try:
                notify.send("👍 Fetching the next video — one moment while I prepare its clips…")
            except Exception:
                pass
        console.print(f"Processing: {url} (reframe: {item.reframe})")

        options = PipelineOptions(
            post=post, approve=approve, reframe=item.reframe,
            workdir=workdir, max_clips=None if max_clips == 0 else max_clips,
        )

        try:
            results, outcome = run(url, options)
        except ConfigError as e:
            console.print(f"[red]Config error:[/red] {e}")
            raise SystemExit(1)
        except Exception as e:
            # e.g. the next video isn't downloaded on the server yet.
            console.print(f"[red]Error processing {url}:[/red] {e}")
            if not first and notify:
                try:
                    notify.send("Couldn't prepare the next video (it may not be downloaded yet). "
                                "I'll try again on the next run.")
                except Exception:
                    pass
            break

        _mark_used_if_done(url)
        first = False

        # EXHAUSTED (skipped every clip) or NOTHING (no offerable clips) → try the
        # next video; POSTED / PAUSED / TIMEOUT → done for now. The `seen` guard
        # ensures we never revisit a URL, so this can't loop forever.
        if outcome in (RunOutcome.EXHAUSTED, RunOutcome.NOTHING) and url not in seen:
            seen.add(url)
            continue
        break
