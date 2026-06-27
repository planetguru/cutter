"""Persistent approval state — queue, withheld clips, and daily pause."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional


@dataclass
class VideoState:
    video_id: str
    pending: list[str] = field(default_factory=list)    # reframed clip filenames, in order
    withheld: list[str] = field(default_factory=list)   # filenames moved to withheld/
    posted: list[str] = field(default_factory=list)     # filenames successfully posted


@dataclass
class AppState:
    no_more_until: Optional[str] = None      # ISO date string "YYYY-MM-DD"
    videos: dict[str, VideoState] = field(default_factory=dict)

    def is_paused_today(self) -> bool:
        if not self.no_more_until:
            return False
        return date.today().isoformat() <= self.no_more_until

    def pause_until_tomorrow(self) -> None:
        # Pause until end of today — resumed when today > no_more_until
        self.no_more_until = date.today().isoformat()

    def resume(self) -> None:
        self.no_more_until = None

    def get_or_create_video(self, video_id: str, clips: list[str]) -> VideoState:
        if video_id not in self.videos:
            self.videos[video_id] = VideoState(video_id=video_id, pending=list(clips))
        return self.videos[video_id]


class StateStore:
    def __init__(self, workdir: Path) -> None:
        self._path = workdir / "approval_state.json"
        self._state = self._load()

    def _load(self) -> AppState:
        if not self._path.exists():
            return AppState()
        try:
            raw = json.loads(self._path.read_text())
            videos = {
                vid: VideoState(**vs)
                for vid, vs in raw.get("videos", {}).items()
            }
            return AppState(
                no_more_until=raw.get("no_more_until"),
                videos=videos,
            )
        except Exception:
            return AppState()

    def save(self) -> None:
        data = {
            "no_more_until": self._state.no_more_until,
            "videos": {
                vid: asdict(vs)
                for vid, vs in self._state.videos.items()
            },
        }
        self._path.write_text(json.dumps(data, indent=2))

    @property
    def state(self) -> AppState:
        return self._state

    def withhold_clip(self, video_id: str, clip_name: str, reframed_dir: Path) -> None:
        """Move a clip to the withheld folder and update state."""
        vs = self._state.videos.get(video_id)
        if vs and clip_name in vs.pending:
            vs.pending.remove(clip_name)
            vs.withheld.append(clip_name)

        withheld_dir = reframed_dir.parent / "withheld"
        withheld_dir.mkdir(exist_ok=True)
        src = reframed_dir / clip_name
        dst = withheld_dir / clip_name
        if src.exists() and not dst.exists():
            src.rename(dst)

        self.save()

    def mark_posted(self, video_id: str, clip_name: str) -> None:
        vs = self._state.videos.get(video_id)
        if vs and clip_name in vs.pending:
            vs.pending.remove(clip_name)
            vs.posted.append(clip_name)
        self.save()
