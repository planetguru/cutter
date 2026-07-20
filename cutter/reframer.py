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
    mode: str = "blur",
) -> list[Path]:
    """Return paths to reframed clip files, skipping ones that already exist.

    mode: "blur" (landscape shrunk onto a blurred 9:16 background) or "rotate"
    (landscape spun 90° to fill the 9:16 frame — viewer turns their phone).
    """
    out_dir = workdir / video_id / "reframed"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for clip in raw_clips:
        out = out_dir / clip.name
        if not out.exists():
            reframe(clip, out, mode=mode)
        paths.append(out)
    return paths


# FFmpeg transpose direction for rotate mode: 1 = 90° clockwise, 2 = counter-clockwise.
ROTATE_TRANSPOSE = 1


def reframe(clip_path: Path, out_path: Path, mode: str = "blur") -> None:
    """Convert a single clip to 1080x1920 in the given mode."""
    if mode == "rotate":
        # Rotate the landscape frame 90° and scale-to-fill the 9:16 frame.
        # A 16:9 source rotates to exactly 9:16 (no crop); other aspects are
        # covered and centre-cropped so the frame is always filled.
        filtergraph = (
            f"[0:v]transpose={ROTATE_TRANSPOSE},"
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},setsar=1[v]"
        )
    else:
        # Background: scale up to fill 1080x1920, crop to exact size, apply heavy blur
        # Foreground: scale down to fit inside 1080x1920 preserving aspect ratio
        # Overlay: centre foreground on blurred background
        filtergraph = (
            f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},"
            f"boxblur=luma_radius=30:luma_power=3:chroma_radius=30:chroma_power=3[bg];"
            f"[0:v]scale=w='min({TARGET_W},iw)':h='min({TARGET_H},ih)'"
            f":force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1[v]"
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
