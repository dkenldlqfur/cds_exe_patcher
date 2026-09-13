"""Extract DISCOVER.CDS frames and encode game-compatible AVI candidates.

The original animation frames are 240x176 indexed images.  PNG frames keep
that exact size, while each AVI frame is centered without scaling on the
320x240 canvas used by the game's existing ``AVI/Ixx_0000.AVI`` files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_MODULES = PROJECT_ROOT / "Resources" / "py"
if str(RESOURCE_MODULES) not in sys.path:
    sys.path.insert(0, str(RESOURCE_MODULES))

from discover_animation_reader import (  # noqa: E402
    FRAME_HEIGHT,
    FRAME_WIDTH,
    discover_animation_count,
    read_discover_animation,
)
from patch_cds_integrated import read_discovery_records  # noqa: E402


AVI_WIDTH = 320
AVI_HEIGHT = 240
DEFAULT_FPS = 15
DEFAULT_FIRST_AVI_ID = 70


def _ffmpeg_executable(explicit: str | None) -> str:
    if explicit:
        candidate = Path(explicit).expanduser().resolve(strict=True)
        return str(candidate)
    installed = shutil.which("ffmpeg")
    if installed:
        return installed
    try:
        import imageio_ffmpeg
    except ImportError as error:
        raise RuntimeError(
            "FFmpeg를 찾을 수 없습니다. ffmpeg를 PATH에 추가하거나 "
            "'py -3.14 -m pip install imageio-ffmpeg'를 실행하십시오."
        ) from error
    return imageio_ffmpeg.get_ffmpeg_exe()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_cinepak(
    ffmpeg: str,
    frames: tuple[Image.Image, ...],
    output_path: Path,
    fps: int,
) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "rawvideo",
        "-pixel_format", "rgb24",
        "-video_size", f"{AVI_WIDTH}x{AVI_HEIGHT}",
        "-framerate", str(fps),
        "-i", "pipe:0",
        "-an",
        "-c:v", "cinepak",
        "-pix_fmt", "rgb24",
        "-vtag", "cvid",
        "-max_strips", "3",
        str(output_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    try:
        left = (AVI_WIDTH - FRAME_WIDTH) // 2
        top = (AVI_HEIGHT - FRAME_HEIGHT) // 2
        for frame in frames:
            canvas = Image.new("RGB", (AVI_WIDTH, AVI_HEIGHT), "black")
            canvas.paste(frame.convert("RGB"), (left, top))
            process.stdin.write(canvas.tobytes())
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr is not None else b""
        return_code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    if return_code:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg Cinepak 인코딩 실패: {message}")


def export(executable: Path, output_root: Path, ffmpeg: str, fps: int, first_avi_id: int) -> None:
    executable = executable.resolve(strict=True)
    discover_path = executable.with_name("DISCOVER.CDS")
    discover_path.resolve(strict=True)
    part_count = discover_animation_count(executable)
    if part_count != 29:
        raise RuntimeError(f"DISCOVER.CDS 파트 수가 예상값 29와 다릅니다: {part_count}")

    frames_root = output_root / "frames"
    avi_root = output_root / "avi"
    frames_root.mkdir(parents=True, exist_ok=True)
    avi_root.mkdir(parents=True, exist_ok=True)

    records_by_part = {
        record.animation_part: record
        for record in read_discovery_records(executable)
        if record.animation_part is not None
    }
    manifest_parts: list[dict[str, object]] = []
    for part in range(part_count):
        frames = read_discover_animation(executable, animation_part=part)
        part_frames = frames_root / f"part_{part:02d}"
        part_frames.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(frames):
            frame.convert("RGB").save(part_frames / f"frame_{index:03d}.png", optimize=True)

        avi_id = first_avi_id + part
        avi_path = avi_root / f"I{avi_id:02d}_0000.AVI"
        _encode_cinepak(ffmpeg, frames, avi_path, fps)
        record = records_by_part.get(part)
        manifest_parts.append({
            "discover_part": part,
            "avi_id": avi_id,
            "avi_file": avi_path.name,
            "discovery_record": record.identifier if record is not None else None,
            "discovery_name": record.name if record is not None else None,
            "frame_count": len(frames),
            "duration_seconds": len(frames) / fps,
            "avi_size": avi_path.stat().st_size,
            "avi_sha256": _sha256(avi_path),
        })
        print(
            f"part {part:02d}: {len(frames):2d} frames -> "
            f"{avi_path.name} ({avi_path.stat().st_size:,} bytes)"
        )

    manifest = {
        "source": {
            "discover_file": discover_path.name,
            "discover_sha256": _sha256(discover_path),
            "executable_file": executable.name,
            "executable_sha256": _sha256(executable),
        },
        "frame": {
            "source_width": FRAME_WIDTH,
            "source_height": FRAME_HEIGHT,
            "avi_width": AVI_WIDTH,
            "avi_height": AVI_HEIGHT,
            "placement": "centered_without_scaling",
            "fps": fps,
        },
        "codec": {
            "container": "AVI",
            "video_codec": "Cinepak",
            "fourcc": "cvid",
            "pixel_format": "rgb24",
        },
        "parts": manifest_parts,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path, help="DISCOVER.CDS와 같은 폴더의 게임 EXE")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "research" / "discover_export",
        help="PNG 프레임과 AVI를 저장할 폴더",
    )
    parser.add_argument("--ffmpeg", help="사용할 FFmpeg 실행 파일 경로")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--first-avi-id", type=int, default=DEFAULT_FIRST_AVI_ID)
    args = parser.parse_args()
    if not 1 <= args.fps <= 60:
        parser.error("--fps는 1~60이어야 합니다.")
    if not 0 <= args.first_avi_id <= 999:
        parser.error("--first-avi-id는 0~999여야 합니다.")
    export(
        args.executable,
        args.output.resolve(),
        _ffmpeg_executable(args.ffmpeg),
        args.fps,
        args.first_avi_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
