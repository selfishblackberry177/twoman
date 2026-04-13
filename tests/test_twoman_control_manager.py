from __future__ import annotations

import unittest

from textual.widgets import Static

from twoman_control.manager import ActionResult, TwomanManagerApp
from twoman_control.models import BACKEND_PASSENGER, InstallState


class _FakeController:
    def __init__(self) -> None:
        self.state = InstallState(
            version=1,
            instance_name="node",
            backend=BACKEND_PASSENGER,
            public_origin="https://host.example.com",
            public_base_path="/darvazeh",
            broker_base_url="https://host.example.com/darvazeh",
            client_token="client-token",
            agent_token="agent-token",
            client_profile_name="Primary Host",
            profile_share_text="twoman://profile?data=abc",
            cpanel_base_url="https://host.example.com:2083",
            cpanel_username="cpanel-user",
            cpanel_password="cpanel-pass",
            cpanel_home="/home/cpanel-user",
            control_root="/opt/twoman/control",
            bundle_root="/opt/twoman/control/bundle",
            hidden_install_root="/opt/twoman",
            hidden_service_name="twoman-agent.service",
            hidden_service_user="twoman",
            hidden_service_group="twoman",
            watchdog_service_name="twoman-agent-watchdog.service",
            watchdog_timer_name="twoman-agent-watchdog.timer",
            agent_peer_id="agent-main",
            verify_tls=True,
            client_http2_ctl=True,
            client_http2_data=False,
            client_http_port=18092,
            client_socks_port=11092,
            deployment_id="deadbeefcafe",
            site_name="دروازه",
            site_slug="darvazeh",
            hidden_upstream_proxy_url="socks5h://127.0.0.1:1280",
            hidden_upstream_proxy_label="wireproxy",
            hidden_outbound_proxy_url="socks5h://127.0.0.1:1280",
            hidden_outbound_proxy_label="wireproxy",
        )

    def journal_tail(self) -> str:
        return "line one\nline two"

    def capabilities_text(self) -> str:
        return "Passenger Python: recommended"

    def hidden_route_text(self) -> str:
        return "WARP WireProxy via socks5h://127.0.0.1:1280"

    def outbound_route_text(self) -> str:
        return "WARP WireProxy via socks5h://127.0.0.1:1280"

    def install_command(self) -> list[str]:
        return ["/usr/local/bin/twoman", "install", "--instance", "node"]

    def list_instances_text(self) -> str:
        return "* node: passenger_python -> https://host.example.com/darvazeh"

    def verify(self) -> ActionResult:
        return ActionResult(True, "healthy", '{"ok": true}')

    def restart_agent(self) -> ActionResult:
        return ActionResult(True, "restarted", "agent restarted")

    def restart_watchdog(self) -> ActionResult:
        return ActionResult(True, "watchdog ran", "watchdog ran")

    def restart_upstream_proxy(self) -> ActionResult:
        return ActionResult(True, "route proxy restarted", "wireproxy restarted")

    def redeploy_host(self) -> ActionResult:
        return ActionResult(True, "host redeployed", "host redeployed")


class TwomanManagerAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_renders_state_and_opens_capabilities_modal(self) -> None:
        app = TwomanManagerApp(controller=_FakeController())
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            deployment_detail = str(app.query_one("#deployment-detail", Static).content)
            log_output = str(app.query_one("#log-output", Static).content)
            self.assertIn("Public origin: https://host.example.com", deployment_detail)
            self.assertIn("Instance: node", deployment_detail)
            self.assertIn("Hidden route: WARP WireProxy via socks5h://127.0.0.1:1280", deployment_detail)
            self.assertIn("Outbound route: WARP WireProxy via socks5h://127.0.0.1:1280", deployment_detail)
            self.assertIn("line one", log_output)

            await pilot.click("#action-show-capabilities")
            await pilot.pause()
            modal_statics = [str(widget.content) for widget in app.screen.query(Static)]
            self.assertTrue(any("Passenger Python: recommended" in item for item in modal_statics))


if __name__ == "__main__":
    unittest.main()
