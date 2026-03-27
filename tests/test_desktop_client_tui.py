from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop_client.controller import DesktopController
from desktop_client.models import ClientProfile
from desktop_client.paths import DesktopPaths
from desktop_client.tui import DesktopClientApp
from textual.widgets import Input


class DesktopClientTuiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.paths = DesktopPaths(Path(self.temp_dir.name))
        self.controller = DesktopController(self.paths)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_profile_add_modal_saves_profile(self) -> None:
        app = DesktopClientApp(controller=self.controller)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.click("#profile-add")
            await pilot.pause()
            app.screen.query_one("#profile-name", Input).value = "Primary"
            app.screen.query_one("#profile-url", Input).value = "https://host.example.com/bridge/v2"
            app.screen.query_one("#profile-token", Input).value = "token-123"
            await pilot.click("#profile-save")
            await pilot.pause()

        profiles = self.controller.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "Primary")

    async def test_profile_import_modal_accepts_android_share_text(self) -> None:
        share_text = ClientProfile(
            name="Imported",
            broker_base_url="https://example.com/twoman",
            client_token="token-import",
        ).to_share_text()

        app = DesktopClientApp(controller=self.controller)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.click("#profile-import")
            await pilot.pause()
            app.screen.query_one("#import-text", Input).value = share_text
            await pilot.click("#import-save")
            await pilot.pause()

        profiles = self.controller.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "Imported")

    async def test_share_add_modal_saves_share(self) -> None:
        profile = self.controller.save_profile(
            ClientProfile(
                name="Primary",
                broker_base_url="https://host.example.com/bridge/v2",
                client_token="token-123",
            )
        )
        self.controller.set_selected_profile(profile.id)

        app = DesktopClientApp(controller=self.controller)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.click("#share-add")
            await pilot.pause()
            app.screen.query_one("#share-name", Input).value = "Public share"
            app.screen.query_one("#share-listen-port", Input).value = "31167"
            app.screen.query_one("#share-username", Input).value = "user-a"
            app.screen.query_one("#share-password", Input).value = "pass-a"
            await pilot.click("#share-save")
            await pilot.pause()

        shares = self.controller.list_shares()
        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0].name, "Public share")


if __name__ == "__main__":
    unittest.main()
