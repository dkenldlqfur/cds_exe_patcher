"""Tk preview playback for the original frame-based DISCOVER.CDS animations."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk

from PIL import ImageTk

from discover_animation_reader import DiscoverAnimationReadError, read_discover_animation


class DiscoverAnimationPreview:
    """Loop a decoded discovery animation in an existing Tk label."""

    def __init__(self, owner: tk.Misc, label: tk.Label, *, frame_interval_ms: int = 80) -> None:
        self._owner = owner
        self._label = label
        self._frame_interval_ms = frame_interval_ms
        self._frames: tuple[ImageTk.PhotoImage, ...] = ()
        self._frame_index = 0
        self._render_job: str | None = None

    def show(self, executable_path: str | Path, animation_part: int) -> bool:
        """Decode and begin looping the requested animation, if available."""
        self.stop()
        try:
            images = read_discover_animation(
                executable_path, animation_part=animation_part,
            )
            self._frames = tuple(ImageTk.PhotoImage(image.convert("RGBA")) for image in images)
        except (DiscoverAnimationReadError, tk.TclError):
            self._frames = ()
            return False
        if not self._frames:
            return False
        self._frame_index = 0
        self._render_frame()
        return True

    def _render_frame(self) -> None:
        if not self._frames or not self._owner.winfo_exists():
            return
        self._label.configure(image=self._frames[self._frame_index], text="")
        self._frame_index = (self._frame_index + 1) % len(self._frames)
        self._render_job = self._owner.after(self._frame_interval_ms, self._render_frame)

    def stop(self) -> None:
        """Cancel playback and release the current frame images."""
        if self._render_job is not None:
            try:
                self._owner.after_cancel(self._render_job)
            except tk.TclError:
                pass
        self._render_job = None
        self._frame_index = 0
        self._frames = ()
