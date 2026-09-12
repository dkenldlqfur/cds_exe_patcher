"""Embedded VLC preview for the original CDS III AVI discovery movies."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import tkinter as tk


_vlc = None


def _vlc_runtime_dir() -> Path | None:
    """Find this patcher's bundled VLC runtime in source and frozen builds."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    root = Path(frozen_root) / "Resources" / "vlc" if frozen_root else Path(__file__).resolve().parent.parent / "vlc"
    return root if (root / "libvlc.dll").is_file() else None


class AviPreview:
    """Render an AVI into an existing Tk label with VLC's memory-video output."""

    def __init__(self, owner: tk.Misc, label: tk.Label, *, width: int = 320, height: int = 240) -> None:
        self._owner = owner
        self._label = label
        self._width = width
        self._height = height
        self._instance = None
        self._player = None
        self._dll_directory = None
        self._path: str | None = None
        self._buffer = None
        self._lock_callback = None
        self._display_callback = None
        self._frame_ready = False
        self._photo = None
        self._render_job = None
        self._end_callback = None

    def show(self, video_path: str | Path) -> bool:
        """Start (or replace) the looping video; return False when unavailable."""
        path = str(Path(video_path))
        if not Path(path).is_file():
            self.stop()
            return False
        try:
            if self._player is None:
                self._create_player()
            assert self._player is not None and self._instance is not None
            self._frame_ready = False
            self._player.set_media(self._instance.media_new(path))
            self._path = path
            self._label.configure(image=self._photo, text="")
            if self._player.play() == -1:
                raise RuntimeError("VLC 재생을 시작할 수 없습니다.")
            if self._render_job is None:
                self._render()
            return True
        except Exception:
            self.stop()
            return False

    def _create_player(self) -> None:
        global _vlc
        runtime_dir = _vlc_runtime_dir()
        if runtime_dir is None:
            raise RuntimeError("VLC 런타임을 찾을 수 없습니다.")
        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(runtime_dir))
        os.environ["VLC_PLUGIN_PATH"] = str(runtime_dir / "plugins")
        if _vlc is None:
            os.environ["PYTHON_VLC_LIB_PATH"] = str(runtime_dir / "libvlc.dll")
            import vlc as vlc_module
            _vlc = vlc_module
        self._instance = _vlc.Instance(
            "--vout=vmem", "--avcodec-hw=none", "--no-video-title-show",
            "--quiet", "--no-audio", "--input-repeat=-1",
        )
        self._player = self._instance.media_player_new()
        pitch = self._width * 4
        self._buffer = (ctypes.c_ubyte * (self._height * pitch))()
        lock_type = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        )
        display_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
        self._lock_callback = lock_type(
            lambda _opaque, planes: (
                planes.__setitem__(0, ctypes.cast(self._buffer, ctypes.c_void_p).value), None
            )[1]
        )
        self._display_callback = display_type(
            lambda _opaque, _picture: setattr(self, "_frame_ready", True)
        )
        self._player.video_set_callbacks(
            self._lock_callback, None, self._display_callback, None,
        )
        self._player.video_set_format("RV32", self._width, self._height, pitch)
        self._photo = tk.PhotoImage(width=self._width, height=self._height)
        # libVLC invokes events on its decoder thread.  Hand the replay back
        # to Tk's main thread before changing the media object.
        self._end_callback = lambda _event: self._owner.after(150, self._restart)
        self._player.event_manager().event_attach(
            _vlc.EventType.MediaPlayerEndReached, self._end_callback,
        )

    def _render(self) -> None:
        if self._player is None or not self._owner.winfo_exists():
            return
        if self._frame_ready and self._buffer is not None and self._photo is not None:
            self._frame_ready = False
            source = bytes(self._buffer)
            rgb = bytearray(self._width * self._height * 3)
            rgb[0::3] = source[2::4]
            rgb[1::3] = source[1::4]
            rgb[2::3] = source[0::4]
            header = f"P6\n{self._width} {self._height}\n255\n".encode("ascii")
            self._photo.configure(data=header + bytes(rgb), format="PPM")
        self._render_job = self._owner.after(33, self._render)

    def _restart(self) -> None:
        if self._player is None or self._instance is None or self._path is None:
            return
        try:
            self._player.set_media(self._instance.media_new(self._path))
            self._player.play()
        except Exception:
            pass

    def stop(self) -> None:
        """Stop the decoder and release its native resources."""
        if self._render_job is not None:
            try:
                self._owner.after_cancel(self._render_job)
            except tk.TclError:
                pass
            self._render_job = None
        if self._player is not None:
            try:
                self._player.stop()
                self._player.release()
            except Exception:
                pass
            self._player = None
        if self._instance is not None:
            try:
                self._instance.release()
            except Exception:
                pass
            self._instance = None
        if self._dll_directory is not None:
            try:
                self._dll_directory.close()
            except Exception:
                pass
            self._dll_directory = None
        self._path = self._buffer = self._photo = None
        self._lock_callback = self._display_callback = self._end_callback = None
        self._frame_ready = False
