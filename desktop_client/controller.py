from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from desktop_client.models import ClientProfile, SharedSocksProxy
from desktop_client.paths import DesktopPaths
from desktop_client.runtime import HelperProcessManager, HelperStatus, ShareProcessManager, ShareStatus
from desktop_client.storage import ProfileStore, Selection, SelectionStore, ShareStore


class DesktopController:
    """Shared application logic used by the TUI and end-to-end tests."""

    def __init__(self, paths: DesktopPaths | None = None) -> None:
        self.paths = (paths or DesktopPaths.default()).ensure()
        self.profile_store = ProfileStore(self.paths)
        self.share_store = ShareStore(self.paths)
        self.selection_store = SelectionStore(self.paths)
        self.helper_manager = HelperProcessManager(self.paths)
        self.share_manager = ShareProcessManager(self.paths)

    def list_profiles(self) -> list[ClientProfile]:
        return sorted(self.profile_store.load_all(), key=lambda item: item.name.lower())

    def get_profile(self, profile_id: str | None) -> ClientProfile | None:
        if not profile_id:
            return None
        return next((profile for profile in self.list_profiles() if profile.id == profile_id), None)

    def save_profile(self, profile: ClientProfile) -> ClientProfile:
        profile.validate()
        profiles = self.list_profiles()
        replaced = False
        for index, existing in enumerate(profiles):
            if existing.id == profile.id:
                profiles[index] = profile
                replaced = True
                break
        if not replaced:
            profiles.append(profile)
        self.profile_store.save_all(profiles)
        selection = self.selection_store.load()
        if selection.profile_id is None:
            self.selection_store.save(Selection(profile_id=profile.id))
        return profile

    def delete_profile(self, profile_id: str) -> None:
        status = self.helper_status()
        if status.running and status.profile_id == profile_id:
            self.disconnect()
        profiles = [profile for profile in self.list_profiles() if profile.id != profile_id]
        self.profile_store.save_all(profiles)
        selection = self.selection_store.load()
        if selection.profile_id == profile_id:
            next_id = profiles[0].id if profiles else None
            self.selection_store.save(Selection(profile_id=next_id))

    def import_profile_text(self, text: str) -> ClientProfile:
        profile = ClientProfile.from_share_text(text)
        return self.save_profile(profile)

    def export_profile_text(self, profile_id: str) -> str:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError("Selected profile not found")
        return profile.to_share_text()

    def selection(self) -> Selection:
        selection = self.selection_store.load()
        profiles = self.list_profiles()
        if selection.profile_id and any(item.id == selection.profile_id for item in profiles):
            return selection
        next_id = profiles[0].id if profiles else None
        normalized = Selection(profile_id=next_id)
        self.selection_store.save(normalized)
        return normalized

    def set_selected_profile(self, profile_id: str | None) -> None:
        self.selection_store.save(Selection(profile_id=profile_id))

    def helper_status(self) -> HelperStatus:
        return self.helper_manager.status()

    def connect(self, profile_id: str) -> HelperStatus:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError("Selected profile not found")
        self.set_selected_profile(profile.id)
        return self.helper_manager.start(profile)

    def disconnect(self) -> HelperStatus:
        for share in self.list_shares():
            share_status = self.share_status(share.id)
            if share_status.running:
                self.stop_share(share.id)
        return self.helper_manager.stop()

    def list_shares(self) -> list[SharedSocksProxy]:
        return sorted(self.share_store.load_all(), key=lambda item: item.name.lower())

    def get_share(self, share_id: str | None) -> SharedSocksProxy | None:
        if not share_id:
            return None
        return next((share for share in self.list_shares() if share.id == share_id), None)

    def new_share_template(self, target_port: int) -> SharedSocksProxy:
        return SharedSocksProxy(name="New share", listen_port=31167, target_port=target_port)

    def save_share(self, share: SharedSocksProxy) -> SharedSocksProxy:
        share.validate()
        shares = self.list_shares()
        replaced = False
        for index, existing in enumerate(shares):
            if existing.id == share.id:
                was_running = self.share_status(existing.id).running
                shares[index] = share
                replaced = True
                self.share_store.save_all(shares)
                if was_running:
                    self.stop_share(existing.id)
                    self.start_share(share.id)
                return share
        shares.append(share)
        self.share_store.save_all(shares)
        return share

    def delete_share(self, share_id: str) -> None:
        share = self.get_share(share_id)
        if share is None:
            return
        if self.share_status(share_id).running:
            self.stop_share(share_id)
        shares = [item for item in self.list_shares() if item.id != share_id]
        self.share_store.save_all(shares)

    def share_status(self, share_id: str) -> ShareStatus:
        share = self.get_share(share_id)
        if share is None:
            raise ValueError("Selected share not found")
        return self.share_manager.status(share)

    def start_share(self, share_id: str) -> ShareStatus:
        share = self.get_share(share_id)
        if share is None:
            raise ValueError("Selected share not found")
        return self.share_manager.start(share)

    def stop_share(self, share_id: str) -> ShareStatus:
        share = self.get_share(share_id)
        if share is None:
            raise ValueError("Selected share not found")
        return self.share_manager.stop(share)

