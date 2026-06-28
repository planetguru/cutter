"""Detect natural cut points using FFmpeg scene and silence detection."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


class DetectionError(Exception):
    pass


def detect(
    video_path: Path,
    workdir: Path,
    video_id: str,
    *,
    scene_threshold: float = 12.0,
    silence_db: float = -40.0,
    silence_duration: float = 0.5,
    min_clip_secs: float = 25.0,
    max_clip_secs: float = 55.0,
    proximity_merge_secs: float = 2.0,
    force: bool = False,
) -> list[tuple[float, float]]:
    """Return (start, end) pairs for all clips in the video."""
    cache = workdir / video_id / "cut_points.json"
    if cache.exists() and not force:
        data = json.loads(cache.read_text())
        return [tuple(pair) for pair in data]  # type: ignore[return-value]

    duration = _get_duration(video_path)
    scene_times = _detect_scenes(video_path, scene_threshold)
    silence_midpoints = _detect_silence(video_path, silence_db, silence_duration)
    clips = _merge_cut_points(
        scene_times,
        silence_midpoints,
        duration,
        min_clip_secs=min_clip_secs,
        max_clip_secs=max_clip_secs,
        proximity_merge_secs=proximity_merge_secs,
    )

    cache.write_text(json.dumps(clips))
    return clips


def _get_duration(video_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def _detect_scenes(video_path: Path, threshold: float) -> list[float]:
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vf", f"scdet=t={threshold}:s=1",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "No such file" in result.stderr:
        raise DetectionError(f"Video not found: {video_path}")

    times: list[float] = []
    for line in result.stderr.splitlines():
        if "lavfi.scd" not in line:
            continue
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            times.append(float(m.group(1)))
    return times


def _detect_silence(video_path: Path, noise_db: float, min_duration: float) -> list[float]:
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-af", f"silencedetect=n={noise_db}dB:d={min_duration}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )

    starts: list[float] = []
    ends: list[float] = []
    for line in result.stderr.splitlines():
        ms = re.search(r"silence_start:\s*([\d.]+)", line)
        me = re.search(r"silence_end:\s*([\d.]+)", line)
        if ms:
            starts.append(float(ms.group(1)))
        if me:
            ends.append(float(me.group(1)))

    # Use midpoint of each complete silence region
    midpoints: list[float] = []
    for s, e in zip(starts, ends):
        midpoints.append((s + e) / 2.0)
    return midpoints


def _merge_cut_points(
    scene_times: list[float],
    silence_midpoints: list[float],
    duration: float,
    min_clip_secs: float,
    max_clip_secs: float,
    proximity_merge_secs: float,
) -> list[tuple[float, float]]:
    # Tag each candidate: (time, is_silence)
    candidates: list[tuple[float, bool]] = (
        [(t, False) for t in scene_times] +
        [(t, True) for t in silence_midpoints]
    )
    candidates.sort(key=lambda x: x[0])

    # Deduplicate candidates within proximity_merge_secs — prefer silence
    deduped: list[float] = []
    for t, is_silence in candidates:
        if deduped and abs(t - deduped[-1]) < proximity_merge_secs:
            # Replace previous with silence midpoint if this one is silence
            if is_silence:
                deduped[-1] = t
        else:
            deduped.append(t)

    # Build boundaries: anchors at 0 and duration
    boundaries = sorted({0.0, duration} | set(deduped))

    # Walk segments and enforce min/max clip length
    segments = _enforce_clip_lengths(boundaries, min_clip_secs, max_clip_secs)
    return segments


def _enforce_clip_lengths(
    boundaries: list[float],
    min_secs: float,
    max_secs: float,
) -> list[tuple[float, float]]:
    """Merge short segments and split long ones to stay within [min, max]."""
    # First pass: build initial segments from boundaries
    raw = list(zip(boundaries, boundaries[1:]))

    # Merge segments that are too short.
    # Short segments absorb into the previous one; if there is no previous
    # (i.e. the very first boundary is tiny), carry the start forward and
    # merge with the next segment instead.
    merged: list[tuple[float, float]] = []
    pending_start: float | None = None

    for start, end in raw:
        if pending_start is not None:
            start = pending_start
            pending_start = None

        if (end - start) < min_secs:
            if merged:
                merged[-1] = (merged[-1][0], end)
            else:
                # First segment is too short — carry its start forward
                pending_start = start
        else:
            merged.append((start, end))

    # If pending_start is still set the entire video is shorter than min_secs
    if pending_start is not None:
        if merged:
            merged[-1] = (merged[-1][0], raw[-1][1])
        else:
            merged.append((pending_start, raw[-1][1]))

    # Second pass: split segments that are too long
    final: list[tuple[float, float]] = []
    for start, end in merged:
        gap = end - start
        if gap <= max_secs:
            final.append((start, end))
        else:
            # Split into equal chunks of at most max_secs
            n_chunks = int(gap // max_secs) + (1 if gap % max_secs > min_secs else 0)
            chunk_size = gap / max(n_chunks, 1)
            pos = start
            while pos < end - 0.1:
                chunk_end = min(pos + chunk_size, end)
                final.append((pos, chunk_end))
                pos = chunk_end

    return final
