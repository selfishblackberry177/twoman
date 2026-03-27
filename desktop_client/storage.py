from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from desktop_client.models import ClientProfile, SharedSocksProxy
from desktop_client.paths import DesktopPaths

T = TypeVar("T")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class _JsonListStore(Generic[T]):
    def __init__(self, path: Path):
        self.path = path

    def _from_dict(self, payload: dict) -> T:
        raise NotImplementedError

    def load_all(self) -> list[T]:
        payload = _read_json(self.path, [])
        return [self._from_dict(item) for item in payload]

    def save_all(self, items: list[T]) -> None:
        _write_json(self.path, [item.to_dict() for item in items])


class ProfileStore(_JsonListStore[ClientProfile]):
    def __init__(self, paths: DesktopPaths):
        super().__init__(paths.profiles_file)

    def _from_dict(self, payload: dict) -> ClientProfile:
        return ClientProfile.from_dict(payload)


class ShareStore(_JsonListStore[SharedSocksProxy]):
    def __init__(self, paths: DesktopPaths):
        super().__init__(paths.shares_file)

    def _from_dict(self, payload: dict) -> SharedSocksProxy:
        return SharedSocksProxy.from_dict(payload)


@dataclass(slots=True)
class Selection:
    profile_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"profile_id": self.profile_id}

    @classmethod
    def from_dict(cls, payload: dict) -> "Selection":
        return cls(profile_id=payload.get("profile_id"))


class SelectionStore:
    def __init__(self, paths: DesktopPaths):
        self.path = paths.selection_file

    def load(self) -> Selection:
        return Selection.from_dict(_read_json(self.path, {}))

    def save(self, selection: Selection) -> None:
        _write_json(self.path, selection.to_dict())

