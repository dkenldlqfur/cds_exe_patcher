"""Tests for thread-safe AVI preview shutdown behavior."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Resources" / "py"))

import avi_preview  # noqa: E402
from avi_preview import AviPreview  # noqa: E402


class _Owner:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.after_calls: list[tuple] = []

    def after_cancel(self, job: str) -> None:
        self.cancelled.append(job)

    def after(self, *args) -> str:
        self.after_calls.append(args)
        return "unexpected"


class _EventManager:
    def __init__(self) -> None:
        self.detached: list[object] = []

    def event_detach(self, event_type: object) -> None:
        self.detached.append(event_type)


class _Player:
    def __init__(self) -> None:
        self.events = _EventManager()
        self.stopped = False
        self.released = False

    def event_manager(self) -> _EventManager:
        return self.events

    def stop(self) -> None:
        self.stopped = True

    def release(self) -> None:
        self.released = True


class _Instance:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class AviPreviewShutdownTests(unittest.TestCase):
    def test_vlc_event_does_not_call_tk_from_decoder_thread(self) -> None:
        owner = _Owner()
        preview = AviPreview(owner, object())

        preview._request_restart(None)

        self.assertTrue(preview._restart_requested)
        self.assertEqual(owner.after_calls, [])

    def test_stop_detaches_event_before_releasing_player(self) -> None:
        owner = _Owner()
        preview = AviPreview(owner, object())
        player = _Player()
        instance = _Instance()
        preview._render_job = "render-job"
        preview._player = player
        preview._instance = instance
        previous_vlc = avi_preview._vlc
        avi_preview._vlc = SimpleNamespace(
            EventType=SimpleNamespace(MediaPlayerEndReached="ended"),
        )
        try:
            preview.stop()
        finally:
            avi_preview._vlc = previous_vlc

        self.assertEqual(owner.cancelled, ["render-job"])
        self.assertEqual(player.events.detached, ["ended"])
        self.assertTrue(player.stopped)
        self.assertTrue(player.released)
        self.assertTrue(instance.released)


if __name__ == "__main__":
    unittest.main()
