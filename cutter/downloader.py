"""Download YouTube videos and extract metadata via yt-dlp."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp


class DownloadError(Exception):
    pass


@dataclass
class VideoAsset:
    video_id: str
    local_path: Path
    title: str
    description: str
    tags: list[str]
    comments: list[str]
    duration_secs: float


def download(url: str, workdir: Path) -> VideoAsset:
    """Download video and metadata; skip if already present in workdir."""
    # Fast path: if we can derive the video_id from the URL and everything is
    # already on disk, skip the network entirely.
    video_id = _video_id_from_url(url)
    if video_id:
        asset_dir = workdir / video_id
        meta_path = asset_dir / "metadata.json"
        video_path = asset_dir / "source.mp4"
        if meta_path.exists() and video_path.exists():
            meta = json.loads(meta_path.read_text())
            return VideoAsset(
                video_id=video_id,
                local_path=video_path,
                title=meta.get("title", ""),
                description=meta.get("description", ""),
                tags=meta.get("tags") or [],
                comments=_top_comments(meta.get("comments") or [], n=20),
                duration_secs=float(meta.get("duration") or 0),
            )

    # Not cached — fetch from YouTube.
    meta = _extract_metadata(url)
    video_id = meta["id"]

    asset_dir = workdir / video_id
    asset_dir.mkdir(parents=True, exist_ok=True)

    meta_path = asset_dir / "metadata.json"
    video_path = asset_dir / "source.mp4"

    if not meta_path.exists():
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    if not video_path.exists():
        _download_video(url, video_path)

    return VideoAsset(
        video_id=video_id,
        local_path=video_path,
        title=meta.get("title", ""),
        description=meta.get("description", ""),
        tags=meta.get("tags") or [],
        comments=_top_comments(meta.get("comments") or [], n=20),
        duration_secs=float(meta.get("duration") or 0),
    )


def _video_id_from_url(url: str) -> str | None:
    """Extract an 11-character YouTube video ID from a URL without a network request."""
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None


def _extract_metadata(url: str) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "writecomments": True,
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": ["50"]}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return ydl.sanitize_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"Failed to fetch metadata: {e}") from e


def _download_video(url: str, dest: Path) -> None:
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(dest),
        "quiet": True,
        "no_warnings": True,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
        "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"Failed to download video: {e}") from e


def _top_comments(comments: list[dict], n: int) -> list[str]:
    """Return top n comments by like count."""
    sorted_comments = sorted(comments, key=lambda c: c.get("like_count") or 0, reverse=True)
    return [c.get("text", "") for c in sorted_comments[:n] if c.get("text")]
