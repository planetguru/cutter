"""Reformat clips to 9:16 vertical with blurred background using FFmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path


class ReframeError(Exception):
    pass


TARGET_W = 1080
TARGET_H = 1920


def reframe_all(
    raw_clips: list[Path],
    workdir: Path,
    video_id: str,
) -> list[Path]:
    """Return paths to reframed clip files, skipping ones that already exist."""
    out_dir = workdir / video_id / "reframed"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for clip in raw_clips:
        out = out_dir / clip.name
        if not out.exists():
            reframe(clip, out)
        paths.append(out)
    return paths


def reframe(clip_path: Path, out_path: Path) -> None:
    """Convert a single clip to 1080x1920 with blurred background."""
    # Background: scale up to fill 1080x1920, crop to exact size, apply heavy blur
    # Foreground: scale down to fit inside 1080x1920 preserving aspect ratio
    # Overlay: centre foreground on blurred background
    filtergraph = (
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},"
        f"boxblur=luma_radius=30:luma_power=3:chroma_radius=30:chroma_power=3[bg];"
        f"[0:v]scale=w='min({TARGET_W},iw)':h='min({TARGET_H},ih)'"
        f":force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2[v]"
    )
    cmd = [
        "ffmpeg",
        "-i", str(clip_path),
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-y",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ReframeError(f"FFmpeg reframe failed for {clip_path.name}:\n{result.stderr[-800:]}")
