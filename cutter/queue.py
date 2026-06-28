"""Video URL queue — persistent list processed one per day by `cutter daily`."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import platformdirs

QueueStatus = Literal["pending", "used"]

QUEUE_PATH = Path(platformdirs.user_data_dir("cutter")) / "queue.json"


@dataclass
class QueueItem:
    url: str
    status: QueueStatus = "pending"
    added: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    used: str | None = None


@dataclass
class QueueFile:
    items: list[QueueItem] = field(default_factory=list)
    last_whatsapp_scan: str | None = None


def _load() -> QueueFile:
    if not QUEUE_PATH.exists():
        return QueueFile()
    try:
        raw = json.loads(QUEUE_PATH.read_text())
        items = [QueueItem(**i) for i in raw.get("items", [])]
        return QueueFile(items=items, last_whatsapp_scan=raw.get("last_whatsapp_scan"))
    except Exception:
        return QueueFile()


def _save(qf: QueueFile) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_whatsapp_scan": qf.last_whatsapp_scan,
        "items": [asdict(i) for i in qf.items],
    }
    QUEUE_PATH.write_text(json.dumps(data, indent=2))


def add(url: str) -> bool:
    """Add a URL to the queue. Returns False if already present (pending or used)."""
    qf = _load()
    if any(i.url == url for i in qf.items):
        return False
    qf.items.append(QueueItem(url=url))
    _save(qf)
    return True


def next_pending() -> str | None:
    """Return the next pending URL, or None if the queue is empty."""
    for item in _load().items:
        if item.status == "pending":
            return item.url
    return None


def mark_used(url: str) -> None:
    """Mark a URL as used after its video has been fully processed."""
    qf = _load()
    for item in qf.items:
        if item.url == url and item.status == "pending":
            item.status = "used"
            item.used = datetime.now(timezone.utc).isoformat()
    _save(qf)


def get_last_whatsapp_scan() -> datetime | None:
    qf = _load()
    if not qf.last_whatsapp_scan:
        return None
    try:
        return datetime.fromisoformat(qf.last_whatsapp_scan)
    except ValueError:
        return None


def update_last_whatsapp_scan() -> None:
    qf = _load()
    qf.last_whatsapp_scan = datetime.now(timezone.utc).isoformat()
    _save(qf)


def list_all() -> list[QueueItem]:
    return _load().items
