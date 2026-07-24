"""Tests for the source-video attribution line appended to every description.

Runnable with `pytest` or directly: `python tests/test_attribution.py`.
"""

from pathlib import Path

from cutter.captioner import Caption, append_attribution, source_url
from cutter.poster.tiktok import build_caption

VID = "pgJJ3Log95w"
LINE = f"Original video: https://youtu.be/{VID}"


def _count(text: str, needle: str = "Original video:") -> int:
    return text.count(needle)


def test_source_url_is_clean_short_form():
    assert source_url(VID) == "https://youtu.be/pgJJ3Log95w"
    # No query params or fragments ever, regardless of the ID we were given.
    assert "?" not in source_url(VID) and "#" not in source_url(VID)


def test_appends_single_line():
    out = append_attribution("A caption.", VID)
    assert out == f"A caption.\n{LINE}"
    assert _count(out) == 1


def test_dedup_when_text_already_has_attribution():
    # A caption the user pasted that already carries an attribution line
    # (here with a messy full URL) must end with exactly one clean line.
    messy = (
        "My caption.\n"
        "Original video: https://www.youtube.com/watch?v=pgJJ3Log95w&is=ABC#frag"
    )
    out = append_attribution(messy, VID)
    assert _count(out) == 1, out
    assert out.endswith(LINE)
    # The messy full URL / query junk is gone.
    assert "watch?v=" not in out and "?is=" not in out and "#frag" not in out


def test_idempotent_across_repeated_edits():
    out = append_attribution("Edited caption.", VID)
    out = append_attribution(out, VID)          # e.g. re-run / re-edit
    out = append_attribution(out, VID)
    assert _count(out) == 1
    assert out == f"Edited caption.\n{LINE}"


def test_empty_video_id_adds_nothing():
    assert append_attribution("Just a caption.", "") == "Just a caption."


def test_max_len_preserves_attribution():
    long_body = "x" * 5000
    out = append_attribution(long_body, VID, max_len=2200)
    assert len(out) <= 2200
    assert out.endswith(LINE)          # attribution survives; body is truncated
    assert _count(out) == 1


def test_build_caption_survives_title_truncation():
    # TikTok uses tiktok_caption[:150] as the title; a long caption must still
    # come out with the attribution intact (it's appended after assembly).
    cap = Caption(
        tiktok_caption="word " * 60,   # ~300 chars, well over the 150 title cap
        instagram_caption="ig",
        hashtags=["a", "b"],
        video_id=VID,
    )
    out = build_caption(cap, Path("clip_000.mp4"))
    assert out.endswith(LINE)
    assert _count(out) == 1
    assert len(out) <= 2200


def test_build_caption_without_caption_has_no_attribution():
    out = build_caption(None, Path("clip_000.mp4"))
    assert "Original video:" not in out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
