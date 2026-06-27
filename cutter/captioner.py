"""Generate TikTok/Instagram captions and hashtags via Claude Haiku."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Settings
from .downloader import VideoAsset

MODEL = "claude-haiku-4-5-20251001"


@dataclass
class Caption:
    tiktok_caption: str
    instagram_caption: str
    hashtags: list[str]

    @property
    def hashtag_string(self) -> str:
        return " ".join(f"#{tag.lstrip('#')}" for tag in self.hashtags)


def generate_all(
    asset: VideoAsset,
    clips: list[Path],
    settings: Settings,
    captions_cache: Path,
) -> list[Caption]:
    """Generate captions for all clips. Loads from cache if present."""
    if captions_cache.exists():
        raw = json.loads(captions_cache.read_text())
        return [Caption(**item) for item in raw]

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    captions = [
        _generate_one(client, asset, i, len(clips))
        for i in range(len(clips))
    ]

    captions_cache.write_text(
        json.dumps([c.__dict__ for c in captions], ensure_ascii=False, indent=2)
    )
    return captions


@retry(wait=wait_exponential(min=1, max=30), stop=stop_after_attempt(5), reraise=True)
def _generate_one(
    client: anthropic.Anthropic,
    asset: VideoAsset,
    clip_index: int,
    total_clips: int,
) -> Caption:
    prompt = _build_prompt(asset, clip_index, total_clips)
    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    data = json.loads(text)
    return Caption(
        tiktok_caption=data["tiktok_caption"],
        instagram_caption=data["instagram_caption"],
        hashtags=data["hashtags"],
    )


def _build_prompt(asset: VideoAsset, clip_index: int, total_clips: int) -> str:
    comments_xml = "\n".join(
        f"  <comment>{c[:200]}</comment>"
        for c in asset.comments[:10]
    )
    return f"""You are a social media content strategist.

<video_metadata>
  <title>{asset.title}</title>
  <description>{asset.description[:800]}</description>
  <tags>{", ".join(asset.tags[:15])}</tags>
  <top_comments>
{comments_xml}
  </top_comments>
  <clip_index>{clip_index + 1} of {total_clips}</clip_index>
</video_metadata>

Generate captions and hashtags for this clip suitable for both TikTok and Instagram Reels.

Requirements:
- TikTok caption: max 2,200 characters. The FIRST LINE is critical — it must be a compelling hook
  that stops the scroll (a question, a bold claim, or a surprising statement). Keep it conversational.
  End with a call-to-action (follow, comment, share).
- Instagram caption: max 2,200 characters. First line still needs to hook (Instagram truncates after
  ~125 chars before "more"). Slightly more polished than TikTok. Use paragraph breaks for readability.
- Hashtags: 10-15 tags, mix of broad (#fyp, #reels) and niche tags tightly relevant to the content,
  no # prefix. Do not pad with generic unrelated tags.
- Both captions should feel native to short-form vertical video - not like a YouTube description.
- Do not use em dashes (--) anywhere. Use hyphens (-) if a dash is needed.
- Use singular first-person voice throughout (I, my, me). Never use we, our, or us.
- Do not fabricate claims not supported by the metadata.

Respond with valid JSON only, no markdown fences:
{{
  "tiktok_caption": "...",
  "instagram_caption": "...",
  "hashtags": ["tag1", "tag2"]
}}"""
