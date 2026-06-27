"""Abstract poster protocol and shared result type."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..captioner import Caption


@dataclass
class PostResult:
    platform: str
    clip_path: Path
    url: str | None = None
    publish_id: str | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


class Poster(Protocol):
    def post(self, clip_path: Path, caption: Caption | None) -> PostResult:
        ...
