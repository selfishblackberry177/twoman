from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_state_dir() -> Path:
    override = os.environ.get("TWOMAN_DESKTOP_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "TwomanDesktop"
        return Path.home() / "AppData" / "Roaming" / "TwomanDesktop"
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "twoman-desktop"
    return Path.home() / ".config" / "twoman-desktop"


@dataclass(slots=True)
class DesktopPaths:
    base_dir: Path

    @classmethod
    def default(cls) -> "DesktopPaths":
        return cls(_default_state_dir())

    def ensure(self) -> "DesktopPaths":
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def runtime_dir(self) -> Path:
        return self.base_dir / "runtime"

    @property
    def profiles_file(self) -> Path:
        return self.base_dir / "profiles.json"

    @property
    def shares_file(self) -> Path:
        return self.base_dir / "shares.json"

    @property
    def selection_file(self) -> Path:
        return self.base_dir / "selection.json"

    @property
    def helper_config_file(self) -> Path:
        return self.runtime_dir / "helper-config.json"

    @property
    def helper_state_file(self) -> Path:
        return self.runtime_dir / "helper-state.json"

    @property
    def helper_log_file(self) -> Path:
        return self.logs_dir / "helper.log"

    def share_config_file(self, share_id: str) -> Path:
        return self.runtime_dir / f"share-{share_id}.json"

    def share_state_file(self, share_id: str) -> Path:
        return self.runtime_dir / f"share-{share_id}-state.json"

    def share_log_file(self, share_id: str) -> Path:
        return self.logs_dir / f"share-{share_id}.log"

