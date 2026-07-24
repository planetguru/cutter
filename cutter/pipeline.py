"""Orchestrate the full download → detect → slice → reframe → caption → approve → post pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import platformdirs
from rich.console import Console

from . import captioner, detector, downloader, reframer, slicer
from .approver import Decision, approve_clip
from .captioner import Caption
from .config import ConfigError, Settings, check_ffmpeg, get_settings
from .poster.base import PostResult
from .state import StateStore
from .telegram import TelegramClient

console = Console()


@dataclass
class PipelineOptions:
    min_clip_secs: float = 25.0
    max_clip_secs: float = 55.0
    scene_threshold: float = 12.0
    silence_db: float = -40.0
    post: str = "none"          # "tiktok" | "instagram" | "youtube" | "both" | "all" | "none"
    reframe: str = "blur"       # "blur" (9:16 blurred background) or "rotate" (90° fill)
    approve: bool = False       # require Telegram approval before posting
    captions: bool = True
    keep_raw: bool = False
    force: bool = False
    max_clips: int | None = None  # cap clips processed per run (None = no limit)
    workdir: Path = field(
        default_factory=lambda: Path(platformdirs.user_data_dir("cutter"))
    )


@dataclass
class ClipResult:
    clip_path: Path
    caption: Caption | None
    post_results: list[PostResult] = field(default_factory=list)
    withheld: bool = False
    skipped_today: bool = False


def _manual_tiktok_handoff(
    clip_path: Path,
    cap: Caption | None,
    wa: TelegramClient | None,
    settings: Settings,
) -> PostResult:
    """Send the finished clip + caption to Telegram so the user posts it to
    TikTok by hand (the API is unusable for unaudited personal apps)."""
    from .poster.tiktok import build_caption

    if wa is None:
        try:
            wa = TelegramClient(settings)
        except Exception as e:
            return PostResult(
                platform="tiktok", clip_path=clip_path,
                error=f"manual TikTok mode requires Telegram: {e}",
            )

    wa.send_video(clip_path)
    wa.send(
        "📱 *TikTok — post this one manually*\n\n"
        "Save the video above, then paste this caption:\n\n"
        f"{build_caption(cap, clip_path)}"
    )
    return PostResult(platform="tiktok", clip_path=clip_path)


def run(url: str, options: PipelineOptions | None = None) -> list[ClipResult]:
    if options is None:
        options = PipelineOptions()

    check_ffmpeg()
    settings = get_settings()

    # Resolve target platforms, skipping any without credentials so a single
    # unconfigured platform doesn't sink the whole daily run.
    requested = {
        "all": {"tiktok", "instagram", "youtube", "facebook"},
        "both": {"tiktok", "instagram"},
        "none": set(),
    }.get(options.post, {options.post})
    platforms: set[str] = set()
    for platform in sorted(requested):
        try:
            if platform == "tiktok" and settings.tiktok_post_mode != "manual":
                settings.require_tiktok()
            elif platform == "instagram":
                settings.require_instagram()
            elif platform == "youtube":
                settings.require_youtube()
            elif platform == "facebook":
                settings.require_facebook()
            platforms.add(platform)
        except ConfigError as e:
            console.print(f"[yellow]Skipping {platform} (not configured): {e}[/yellow]")
    if requested and not platforms:
        console.print("[yellow]No posting platform is configured — clips will be approved but not posted.[/yellow]")
    if options.captions:
        settings.require_anthropic()

    # Download
    with console.status("Downloading video and metadata…"):
        asset = downloader.download(url, options.workdir)

    # Detect cut points
    with console.status("Detecting cut points…"):
        clips = detector.detect(
            asset.local_path,
            options.workdir,
            asset.video_id,
            scene_threshold=options.scene_threshold,
            silence_db=options.silence_db,
            min_clip_secs=options.min_clip_secs,
            max_clip_secs=options.max_clip_secs,
            force=options.force,
        )

    console.print(f"[dim]Found {len(clips)} clips.[/dim]")

    # Slice raw clips
    with console.status("Slicing clips…"):
        raw_clips = slicer.slice_all(
            asset.local_path, clips, options.workdir, asset.video_id
        )

    # Reframe to 9:16
    label = "Rotating to fill 9:16…" if options.reframe == "rotate" else "Reframing to 9:16…"
    with console.status(label):
        final_clips = reframer.reframe_all(
            raw_clips, options.workdir, asset.video_id, mode=options.reframe
        )

    # Generate captions
    captions_cache = options.workdir / asset.video_id / "captions.json"
    captions_list: list[Caption | None]
    if options.captions:
        with console.status("Generating captions…"):
            captions_list = captioner.generate_all(asset, final_clips, settings, captions_cache)
    else:
        captions_list = [None] * len(final_clips)

    # Load persistent approval state
    store = StateStore(options.workdir)
    app_state = store.state

    # Check daily pause
    if app_state.is_paused_today():
        console.print("[yellow]Paused for today (you said 'no more today' last time). Run again tomorrow.[/yellow]")
        return []

    # Seed the pending queue if this is a fresh run for this video
    clip_names = [c.name for c in final_clips]
    vs = app_state.get_or_create_video(asset.video_id, clip_names)
    store.save()

    # Filter to only pending clips (resume support). We offer them in order and
    # stop after posting `max_clips` of them — rejected ("no") clips don't count,
    # so daily keeps offering the next candidate until one is approved.
    pending_clips = [c for c in final_clips if c.name in vs.pending]
    reframed_dir = options.workdir / asset.video_id / "reframed"

    # Telegram client (only if approval mode)
    wa: TelegramClient | None = None
    if options.approve:
        wa = TelegramClient(settings)

    results: list[ClipResult] = []
    total = len(pending_clips)
    posted = 0

    for i, clip_path in enumerate(pending_clips, 1):
        cap = captions_list[clip_names.index(clip_path.name)] if clip_path.name in clip_names else None

        # --- Approval gate ---
        if options.approve and wa is not None and cap is not None:
            result = approve_clip(wa, clip_path, cap, i, total)
            cap = result.caption  # may have been edited

            if result.decision == Decision.WITHHELD:
                # "no" — this clip's a dud; withhold it and offer the next one.
                store.withhold_clip(asset.video_id, clip_path.name, reframed_dir)
                results.append(ClipResult(clip_path=clip_path, caption=cap, withheld=True))
                continue

            if result.decision == Decision.NO_MORE_TODAY:
                # Hold the current candidate (leave it pending) and stop for
                # today; tomorrow's run resumes from it.
                app_state.pause_until_tomorrow()
                store.save()
                for remaining in pending_clips[i - 1:]:
                    results.append(ClipResult(clip_path=remaining, caption=None, skipped_today=True))
                break

            if result.decision == Decision.TIMEOUT:
                # No response — hold this clip (leave it pending) and stop for today.
                for remaining in pending_clips[i - 1:]:
                    results.append(ClipResult(clip_path=remaining, caption=None, skipped_today=True))
                break

            # APPROVED — fall through to posting

        # --- Post ---
        # Stamp the canonical source video ID so every platform's description
        # gets the "Original video:" attribution (see captioner.append_attribution).
        if cap is not None:
            cap.video_id = asset.video_id
        clip_result = ClipResult(clip_path=clip_path, caption=cap)

        if "tiktok" in platforms:
            if settings.tiktok_post_mode == "manual":
                post_result = _manual_tiktok_handoff(clip_path, cap, wa, settings)
            else:
                from .poster.tiktok import TikTokPoster
                post_result = TikTokPoster(settings).post(clip_path, cap)
            clip_result.post_results.append(post_result)

        if "instagram" in platforms:
            from .poster.instagram import InstagramPoster
            post_result = InstagramPoster(settings).post(clip_path, cap)
            clip_result.post_results.append(post_result)

        if "youtube" in platforms:
            from .poster.youtube import YouTubePoster
            post_result = YouTubePoster(settings).post(clip_path, cap)
            clip_result.post_results.append(post_result)

        if "facebook" in platforms:
            from .poster.facebook import FacebookPoster
            post_result = FacebookPoster(settings).post(clip_path, cap)
            clip_result.post_results.append(post_result)

        # Only mark posted when something was actually sent to a platform
        if any(r.success for r in clip_result.post_results):
            store.mark_posted(asset.video_id, clip_path.name)

        # Notify via Telegram when posting succeeds
        if wa is not None and clip_result.post_results:
            successes = [r for r in clip_result.post_results if r.success]
            # Manual TikTok hand-off announces itself with the download link.
            announce = [
                r for r in successes
                if not (r.platform == "tiktok" and settings.tiktok_post_mode == "manual")
            ]
            if announce:
                # NB: a separate local name — do not shadow the `platforms` set.
                names = " & ".join(r.platform.title() for r in announce)
                wa.send(f"🚀 Clip {i}/{total} posted to {names}!")
            tiktok_inbox = settings.tiktok_post_mode == "inbox" and any(
                r.platform == "tiktok" for r in successes
            )
            if tiktok_inbox:
                from .poster.tiktok import build_caption
                wa.send(
                    "📥 TikTok clip is waiting in your app inbox — open the "
                    "notification to publish it. Caption to paste:\n\n"
                    f"{build_caption(cap, clip_path)}"
                )

        results.append(clip_result)

        # One posted clip per run by default (max_clips). "no" rejections don't
        # reach here, so we keep offering candidates until one is approved.
        posted += 1
        if options.max_clips is not None and posted >= options.max_clips:
            break

    # Clean up raw clips
    if not options.keep_raw:
        for raw in raw_clips:
            raw.unlink(missing_ok=True)

    return results
