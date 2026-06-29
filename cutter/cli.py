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
    # Set last_whatsapp_scan to now so we don't re-ingest old WhatsApp queue messages.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    (workdir / "queue.json").write_text(
        json.dumps({"last_whatsapp_scan": now_iso, "items": []}, indent=2)
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
@click.option("--post", default="none", type=click.Choice(["tiktok", "instagram", "youtube", "both", "all", "none"]), show_default=True, help="Platform(s) to post to: tiktok, instagram, youtube, both (tiktok+instagram), all (all three), or none")
@click.option("--approve/--no-approve", default=False, help="Ask for WhatsApp approval before each post")
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
        captions=not no_captions,
        keep_raw=keep_raw,
        force=force,
        workdir=Path(workdir) if workdir else Path(platformdirs.user_data_dir("cutter")),
    )

    try:
        results = run(url, options)
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
        table.add_row(str(i), r.clip_path.name, status, tiktok_col, instagram_col, youtube_col)

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
def queue_add(url: str) -> None:
    """Add a YouTube URL to the processing queue."""
    from . import queue as q

    if q.add(url):
        console.print(f"[green]Queued:[/green] {url}")
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
    table.add_column("Added")
    table.add_column("Used")

    for item in items:
        if item.status == "pending":
            status = "[green]pending[/green]"
        else:
            status = "[dim]used[/dim]"
        added = item.added[:10]
        used = item.used[:10] if item.used else "—"
        table.add_row(status, item.url, added, used)

    console.print(table)

    pending = sum(1 for i in items if i.status == "pending")
    console.print(f"\n[green]{pending} pending[/green]  [dim]{len(items) - pending} used[/dim]")


# ---------------------------------------------------------------------------
# cutter daily
# ---------------------------------------------------------------------------

@main.command()
@click.option("--post", default="all",
              type=click.Choice(["tiktok", "instagram", "youtube", "both", "all", "none"]),
              show_default=True)
@click.option("--approve/--no-approve", default=True,
              help="Ask for WhatsApp approval before posting (default: on)")
@click.option("--max-clips", default=1, show_default=True,
              help="Maximum clips to process per run (0 = no limit)")
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

    # Check WhatsApp for commands (best-effort)
    try:
        from .whatsapp import WhatsAppClient
        wa = WhatsAppClient(settings)
        since = q.get_last_whatsapp_scan()

        # Reset command takes priority over everything else
        if wa.scan_for_reset(since=since):
            import glob
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
                json.dumps({"last_whatsapp_scan": now_iso, "items": []}, indent=2)
            )
            (workdir / "approval_state.json").write_text(
                json.dumps({"no_more_until": None, "videos": {}}, indent=2)
            )
            deleted = 0
            for item in workdir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                    deleted += 1
            wa.send(f"Reset complete. Cleared queue and {deleted} video folder(s). Send queue:URL to start again.")
            console.print("[green]Reset triggered via WhatsApp.[/green]")
            return

        new_urls = wa.scan_queue_messages(since=since)
        q.update_last_whatsapp_scan()
        for url in new_urls:
            if q.add(url):
                console.print(f"[dim]Queued from WhatsApp: {url}[/dim]")
    except Exception:
        pass

    url = q.next_pending()
    if url is None:
        console.print("[dim]Queue is empty — nothing to post today.[/dim]")
        return

    console.print(f"Processing: {url}")

    options = PipelineOptions(
        post=post,
        approve=approve,
        workdir=workdir,
        max_clips=None if max_clips == 0 else max_clips,
    )

    try:
        results = run(url, options)
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    # Mark URL as used once all its clips are processed.
    # Leave as pending if the user said "no more today" (clips remain in pending state).
    has_skipped = any(r.skipped_today for r in results)
    if not has_skipped:
        # Confirm no pending clips remain for this video (guards against the
        # "paused today from a previous run" case which returns an empty result list).
        m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', url)
        video_id = m.group(1) if m else None
        if video_id:
            store = StateStore(workdir)
            vs = store.state.videos.get(video_id)
            still_pending = vs.pending if vs else []
        else:
            still_pending = []

        if not still_pending:
            q.mark_used(url)
            console.print(f"[dim]Marked as used: {url}[/dim]")
