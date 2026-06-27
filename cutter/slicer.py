"""Slice a video into raw clips using FFmpeg stream-copy (fast, lossless)."""

from __future__ import annotations

import subprocess
from pathlib import Path


class SliceError(Exception):
    pass


def slice_all(
    video_path: Path,
    clips: list[tuple[float, float]],
    workdir: Path,
    video_id: str,
    *,
    pad_secs: float = 0.1,
) -> list[Path]:
    """Return paths to raw clip files, skipping ones that already exist."""
    raw_dir = workdir / video_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for i, (start, end) in enumerate(clips):
        out = raw_dir / f"clip_{i:03d}.mp4"
        if not out.exists():
            _slice(video_path, out, max(0.0, start - pad_secs), end + pad_secs)
        paths.append(out)
    return paths


def _slice(video_path: Path, out_path: Path, start: float, end: float) -> None:
    cmd = [
        "ffmpeg",
        "-ss", str(start),
        "-to", str(end),
        "-i", str(video_path),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-y",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SliceError(f"FFmpeg slice failed for {out_path.name}:\n{result.stderr[-800:]}")
