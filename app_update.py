"""GitHub Releases based self-update support for the CDS EXE patcher.

The feature is deliberately dormant while ``update.repository`` in
``Resources/data/app_config.json`` is blank.  Once a GitHub repository is
chosen, the GUI can use :class:`GitHubReleaseUpdater` to check, download and
replace its packaged executable using the same flow as DISEV Editor.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


@dataclass(frozen=True)
class UpdateConfig:
    """Version and GitHub Release settings bundled with the application."""

    version: str
    repository: str
    asset_name: str
    executable_name: str

    @property
    def enabled(self) -> bool:
        return bool(self.repository)

    @property
    def latest_url(self) -> str:
        return f"https://api.github.com/repos/{self.repository}/releases/latest" if self.enabled else ""


def bundled_resource_path(*parts: str) -> Path:
    """Return a resource path in both source and PyInstaller builds."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def load_update_config() -> UpdateConfig:
    """Read the packaged configuration, falling back to a safely disabled setup."""
    config_path = bundled_resource_path("Resources", "data", "app_config.json")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    update = raw.get("update", {})
    if not isinstance(update, dict):
        update = {}
    return UpdateConfig(
        version=str(raw.get("version", "0.0.0")).strip() or "0.0.0",
        repository=str(update.get("repository", "")).strip(),
        asset_name=str(update.get("asset_name", "CDS_EXE_Patcher_v{version}.zip")).strip(),
        executable_name=str(update.get("executable_name", "CDS_EXE_Patcher.exe")).strip(),
    )


def parse_release_version(value: object) -> tuple[int, int, int] | None:
    """Convert a ``v1.2.3`` GitHub tag to a sortable three-part version."""
    pieces = str(value).strip().lstrip("vV").split(".")
    if not 1 <= len(pieces) <= 3 or not all(piece.isdigit() for piece in pieces):
        return None
    return tuple(int(piece) for piece in (*pieces, "0", "0")[:3])


class UpdateError(RuntimeError):
    """A download, archive, or replacement preparation error."""


class GitHubReleaseUpdater:
    """Network and file operations used by the future GUI update controls.

    The class has no Tk dependency; the GUI must run its network methods in a
    worker thread, obtain user consent, then call :meth:`launch_replacer`.
    """

    def __init__(self, config: UpdateConfig | None = None) -> None:
        self.config = config or load_update_config()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def fetch_latest_release(self, timeout: int = 8) -> dict[str, Any] | None:
        """Fetch the newest GitHub Release, or return ``None`` when disabled."""
        if not self.enabled:
            return None
        request = Request(self.config.latest_url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CDS-EXE-Patcher/{self.config.version}",
        })
        try:
            with urlopen(request, timeout=timeout) as response:
                release = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateError(f"업데이트 정보를 가져오지 못했습니다: {exc}") from exc
        if not isinstance(release, dict):
            raise UpdateError("업데이트 서버 응답 형식이 올바르지 않습니다.")
        return release

    def is_newer_release(self, release: dict[str, Any]) -> bool:
        remote = parse_release_version(release.get("tag_name", ""))
        local = parse_release_version(self.config.version)
        return remote is not None and local is not None and remote > local

    def release_asset(self, release: dict[str, Any]) -> dict[str, Any] | None:
        """Find this version's ZIP asset, with a ZIP-only fallback like DISEV."""
        version = str(release.get("tag_name", "")).strip().lstrip("vV")
        expected = self.config.asset_name.format(version=version) if version else self.config.asset_name
        assets = release.get("assets", [])
        if not isinstance(assets, list):
            return None
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == expected:
                return asset
        return next(
            (asset for asset in assets
             if isinstance(asset, dict) and str(asset.get("name", "")).lower().endswith(".zip")),
            None,
        )

    def download_and_extract(self, asset: dict[str, Any], timeout: int = 30) -> Path:
        """Download a release ZIP, check its GitHub SHA-256 when supplied, and extract its EXE."""
        url = str(asset.get("browser_download_url", "")).strip()
        if not url:
            raise UpdateError("업데이트 ZIP의 다운로드 주소가 없습니다.")
        asset_name = os.path.basename(str(asset.get("name", self.config.asset_name))) or self.config.asset_name
        partial_path = Path(tempfile.gettempdir()) / f"{asset_name}.{os.getpid()}.part"
        archive_path = partial_path.with_suffix("")
        try:
            digest = hashlib.sha256()
            request = Request(url, headers={
                "Accept": "application/octet-stream",
                "User-Agent": f"CDS-EXE-Patcher/{self.config.version}",
            })
            with urlopen(request, timeout=timeout) as response, partial_path.open("wb") as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(chunk)
                    digest.update(chunk)
            expected = str(asset.get("digest", ""))
            if expected.startswith("sha256:") and digest.hexdigest().lower() != expected[7:].lower():
                raise UpdateError("다운로드한 업데이트 파일의 SHA-256 검증에 실패했습니다.")
            os.replace(partial_path, archive_path)
            executable = self._extract_executable(archive_path)
            archive_path.unlink(missing_ok=True)
            return executable
        except UpdateError:
            partial_path.unlink(missing_ok=True)
            archive_path.unlink(missing_ok=True)
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, zipfile.BadZipFile) as exc:
            partial_path.unlink(missing_ok=True)
            archive_path.unlink(missing_ok=True)
            raise UpdateError(f"업데이트 파일을 준비하지 못했습니다: {exc}") from exc

    def _extract_executable(self, archive_path: Path) -> Path:
        """Extract exactly one expected EXE, rejecting traversal paths."""
        extract_dir = Path(tempfile.mkdtemp(prefix="CDS_EXE_Patcher_update_"))
        try:
            with zipfile.ZipFile(archive_path) as archive:
                candidates = [
                    entry for entry in archive.infolist()
                    if not entry.is_dir()
                    and Path(entry.filename).name.casefold() == self.config.executable_name.casefold()
                ]
                if len(candidates) != 1:
                    raise UpdateError("업데이트 ZIP에는 패처 EXE가 정확히 하나 있어야 합니다.")
                entry = candidates[0]
                destination = (extract_dir / entry.filename).resolve()
                if os.path.commonpath((str(extract_dir.resolve()), str(destination))) != str(extract_dir.resolve()):
                    raise UpdateError("업데이트 ZIP에 허용되지 않는 경로가 포함되어 있습니다.")
                archive.extract(entry, extract_dir)
            if not destination.is_file():
                raise UpdateError("업데이트 EXE를 ZIP에서 추출하지 못했습니다.")
            return destination
        except (UpdateError, OSError, ValueError, zipfile.BadZipFile):
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise

    def launch_replacer(self, replacement: Path, release: dict[str, Any]) -> None:
        """Start an ephemeral batch file to replace this frozen EXE after exit."""
        if not getattr(sys, "frozen", False):
            raise UpdateError("자동 업데이트는 배포된 EXE에서만 실행할 수 있습니다.")
        target = Path(sys.executable).resolve()
        script_path = Path(tempfile.gettempdir()) / f"CDS_EXE_Patcher_update_{os.getpid()}.cmd"
        notice_path = Path(tempfile.gettempdir()) / f"CDS_EXE_Patcher_update_notice_{os.getpid()}.json"
        try:
            notice_path.write_text(json.dumps({
                "version": str(release.get("tag_name", "")).lstrip("vV"),
                "notes": str(release.get("body", "")).strip(),
            }, ensure_ascii=False), encoding="utf-8")
            script = "\r\n".join((
                "@echo off", "setlocal",
                f'set "UPDATE_SOURCE={replacement}"', f'set "UPDATE_TARGET={target}"',
                f'set "UPDATE_NOTICE={notice_path}"', f'set "UPDATE_DIRECTORY={replacement.parent}"',
                ":replace_patcher", 'move /Y "%UPDATE_SOURCE%" "%UPDATE_TARGET%" >nul 2>nul',
                "if errorlevel 1 (", "  timeout /t 1 /nobreak >nul", "  goto replace_patcher", ")",
                'set "PYINSTALLER_RESET_ENVIRONMENT=1"',
                'start "" "%UPDATE_TARGET%" --update-notice "%UPDATE_NOTICE%"',
                'rmdir "%UPDATE_DIRECTORY%" 2>nul', 'del "%~f0"',
            ))
            script_path.write_text(script, encoding="mbcs", newline="")
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(["cmd.exe", "/d", "/c", str(script_path)], close_fds=True, creationflags=flags)
        except OSError as exc:
            raise UpdateError(f"업데이트 교체 작업을 시작하지 못했습니다: {exc}") from exc
