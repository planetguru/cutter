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
# cutter run
# ---------------------------------------------------------------------------

@main.command()
@click.option("--url", required=True, help="YouTube video URL")
@click.option("--post", default="none", type=click.Choice(["tiktok", "instagram", "both", "none"]), show_default=True)
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

    for i, r in enumerate(results, 1):
        if r.withheld:
            status = "[yellow]withheld[/yellow]"
        elif r.skipped_today:
            status = "[dim]skipped today[/dim]"
        else:
            status = "[green]approved[/green]" if r.post_results or options.post == "none" else "[blue]ready[/blue]"

        tiktok_col = _post_status(r.post_results, "tiktok")
        instagram_col = _post_status(r.post_results, "instagram")
        table.add_row(str(i), r.clip_path.name, status, tiktok_col, instagram_col)

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
