#!/usr/bin/env python3
"""Assemble a portable Windows Twoman desktop bundle.

This script copies a built Windows Tauri executable plus bundled sidecars into
the repo's private handoff folder and writes the portable-mode markers that the
app requires to keep state beside the executable.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "desktop_app"
TAURI_CONFIG_PATH = APP_ROOT / "src-tauri/tauri.conf.json"
WINDOWS_BUILD_ROOT = Path(
    "/mnt/c/Users/Shaha/AppData/Local/Temp/mintm-win-build/desktop_app/src-tauri/target/release"
)
WINDOWS_BUNDLE_ROOT = WINDOWS_BUILD_ROOT / "bundle"
WINDOWS_SIDECAR_CANDIDATES = [
    Path(
        "/mnt/c/Users/Shaha/AppData/Local/Temp/mintm-win-build/desktop_app/build/windows-sidecars/dist"
    ),
    Path(
        "/mnt/c/Users/Shaha/AppData/Local/Temp/mintm-win-build/desktop_app/src-tauri/resources/sidecars/windows"
    ),
    REPO_ROOT / "desktop_app/src-tauri/resources/sidecars/windows",
    REPO_ROOT / "private_handoff/desktop_app/windows/portable-ui/Twoman/sidecars/windows",
]
HANDOFF_ROOT = REPO_ROOT / "private_handoff/desktop_app/windows"
PORTABLE_ROOT = HANDOFF_ROOT / "portable-ui/Twoman"
PORTABLE_DATA_ROOT = PORTABLE_ROOT / "portable-data"
SIDECAR_ROOT = PORTABLE_ROOT / "sidecars/windows"


def load_app_version() -> str:
    config = json.loads(TAURI_CONFIG_PATH.read_text(encoding="utf-8"))
    version = str(config.get("version", "")).strip()
    if not version:
        raise ValueError(f"missing version in {TAURI_CONFIG_PATH}")
    return version


APP_VERSION = load_app_version()


def windows_artifact_name(suffix: str) -> str:
    return f"Twoman_{APP_VERSION}_x64{suffix}"


def copy_required_file(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"missing required artifact: {source}")
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def resolve_sidecar(name: str) -> Path:
    for root in WINDOWS_SIDECAR_CANDIDATES:
        candidate = root / name
        if candidate.exists():
            return candidate
    searched = "\n".join(str(path) for path in WINDOWS_SIDECAR_CANDIDATES)
    raise FileNotFoundError(f"missing sidecar {name}; searched:\n{searched}")


def write_portable_markers() -> None:
    PORTABLE_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    (PORTABLE_ROOT / "twoman-portable").write_text(
        "Portable mode marker for Twoman desktop.\n",
        encoding="utf-8",
    )
    (PORTABLE_DATA_ROOT / "README.txt").write_text(
        (
            "Twoman portable runtime data lives here.\n"
            "- config/: saved routes, shares, and settings\n"
            "- runtime/: generated helper/share configs\n"
            "- twoman-logs/: helper, tunnel, and shared proxy logs\n"
        ),
        encoding="utf-8",
    )


def build_zip() -> Path:
    zip_path = HANDOFF_ROOT / windows_artifact_name("-portable.zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PORTABLE_ROOT.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(HANDOFF_ROOT / "portable-ui"))
    return zip_path


def main() -> None:
    HANDOFF_ROOT.mkdir(parents=True, exist_ok=True)
    copy_required_file(
        WINDOWS_BUILD_ROOT / "desktop_app.exe",
        PORTABLE_ROOT / "Twoman.exe",
    )
    copy_required_file(
        WINDOWS_BUNDLE_ROOT / f"nsis/{windows_artifact_name('-setup.exe')}",
        HANDOFF_ROOT / windows_artifact_name("-setup.exe"),
    )
    copy_required_file(
        WINDOWS_BUNDLE_ROOT / f"msi/{windows_artifact_name('_en-US.msi')}",
        HANDOFF_ROOT / windows_artifact_name("_en-US.msi"),
    )
    copy_required_file(
        resolve_sidecar("twoman-helper.exe"),
        SIDECAR_ROOT / "twoman-helper.exe",
    )
    copy_required_file(
        resolve_sidecar("twoman-gateway.exe"),
        SIDECAR_ROOT / "twoman-gateway.exe",
    )
    copy_required_file(
        resolve_sidecar("twoman-tunnel.exe"),
        SIDECAR_ROOT / "twoman-tunnel.exe",
    )
    write_portable_markers()
    zip_path = build_zip()
    print(f"portable zip ready: {zip_path}")


if __name__ == "__main__":
    main()
