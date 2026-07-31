"""Tests for the 'no → next / pause' approval flow.

Covers the approver's reply→decision mapping (real code) and a faithful
simulation of how cutter daily crosses the video boundary based on RunOutcome.

Run with `pytest` or directly: `python tests/test_approval_flow.py`.
"""

from pathlib import Path

from cutter.approver import Decision, _handle_reply
from cutter.captioner import Caption
from cutter.pipeline import RunOutcome


class FakeWA:
    def __init__(self):
        self.sent = []

    def send(self, body="", media_url=None):
        self.sent.append(body)


def _cap():
    return Caption(tiktok_caption="tt", instagram_caption="ig", youtube_caption="yt",
                   hashtags=["a"], video_id="vid")


def _reply(text):
    wa = FakeWA()
    res = _handle_reply(text, _cap(), Path("clip_000.mp4"), 1, 3, wa)
    return res, wa


# --- reply → decision mapping -------------------------------------------------

def test_yes_approves():
    res, _ = _reply("yes")
    assert res is not None and res.decision == Decision.APPROVED


def test_no_asks_next_or_pause_and_waits():
    res, wa = _reply("no")
    assert res is None                                   # conversation stays open
    assert any("next" in s.lower() and "pause" in s.lower() for s in wa.sent)


def test_next_skips_to_next_candidate():
    res, _ = _reply("next")
    assert res is not None and res.decision == Decision.SKIP_NEXT


def test_pause_withholds_and_pauses():
    res, _ = _reply("pause")
    assert res is not None and res.decision == Decision.SKIP_PAUSE


def test_no_more_today_holds_and_pauses():
    res, _ = _reply("no more today")
    assert res is not None and res.decision == Decision.HOLD_PAUSE


def test_edit_keeps_conversation_open():
    wa = FakeWA()
    cap = _cap()
    res = _handle_reply("desc: brand new text", cap, Path("clip_000.mp4"), 1, 3, wa)
    assert res is None
    assert cap.tiktok_caption == "brand new text"
    assert cap.instagram_caption == "brand new text"
    assert cap.youtube_caption == "brand new text"


def test_unrecognised_shows_help():
    res, wa = _reply("wat")
    assert res is None
    assert any("Didn't understand" in s for s in wa.sent)


# --- daily cross-video simulation (mirrors cli.daily's while-loop) -------------

def _simulate_daily(videos, scripts):
    """videos: ordered queue of pending video ids.
    scripts: {vid: RunOutcome} — the outcome run() returns for that video.
    Returns the list of videos actually offered, in order."""
    offered = []
    first = True
    idx = 0
    used = set()
    while True:
        pending = [v for v in videos if v not in used]
        if not pending:
            break
        vid = pending[0]
        offered.append(vid)
        outcome = scripts[vid]
        # A video is "used" once its clips are done (posted-one or exhausted-all);
        # paused/timeout leave it with clips pending (not used).
        if outcome in (RunOutcome.POSTED, RunOutcome.EXHAUSTED):
            used.add(vid)
        first = False
        if outcome == RunOutcome.EXHAUSTED:
            continue
        break
    return offered


def test_daily_stops_after_a_post():
    offered = _simulate_daily(["A", "B"], {"A": RunOutcome.POSTED})
    assert offered == ["A"]


def test_daily_crosses_to_next_video_when_first_is_exhausted():
    # User said no→next through all of A's clips → daily offers B next.
    offered = _simulate_daily(["A", "B"], {"A": RunOutcome.EXHAUSTED, "B": RunOutcome.POSTED})
    assert offered == ["A", "B"]


def test_daily_stops_on_pause_without_crossing():
    offered = _simulate_daily(["A", "B"], {"A": RunOutcome.PAUSED})
    assert offered == ["A"]


def test_daily_stops_when_queue_exhausted():
    # Both videos skipped through → nothing posted, queue runs dry.
    offered = _simulate_daily(["A", "B"], {"A": RunOutcome.EXHAUSTED, "B": RunOutcome.EXHAUSTED})
    assert offered == ["A", "B"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
