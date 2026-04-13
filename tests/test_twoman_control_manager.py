from __future__ import annotations

import json
import unittest
from pathlib import Path

from textual.widgets import Button, Static

from twoman_control.manager import ActionResult, TwomanManagerApp
from twoman_control.models import (
    BACKEND_BRIDGE,
    BACKEND_NODE,
    InstallState,
    InstanceRegistry,
    ManagedInstance,
)


def _build_state(
    *,
    instance_name: str,
    backend: str,
    broker_base_url: str,
    public_base_path: str,
    hidden_service_name: str,
    hidden_install_root: str,
    client_profile_name: str,
) -> InstallState:
    return InstallState(
        version=1,
        instance_name=instance_name,
        backend=backend,
        public_origin="https://host.example.com",
        public_base_path=public_base_path,
        broker_base_url=broker_base_url,
        client_token=f"{instance_name}-client-token",
        agent_token=f"{instance_name}-agent-token",
        client_profile_name=client_profile_name,
        profile_share_text=f"twoman://profile?data={instance_name}",
        cpanel_base_url="https://host.example.com:2083",
        cpanel_username="cpanel-user",
        cpanel_password="cpanel-pass",
        cpanel_home="/home/cpanel-user",
        control_root="/opt/twoman/control",
        bundle_root="/opt/twoman/control/bundle",
        hidden_install_root=hidden_install_root,
        hidden_service_name=hidden_service_name,
        hidden_service_user="twoman",
        hidden_service_group="twoman",
        watchdog_service_name=f"{instance_name}-watchdog.service",
        watchdog_timer_name=f"{instance_name}-watchdog.timer",
        agent_peer_id=f"agent-{instance_name}",
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


class _FakeController:
    def __init__(self) -> None:
        self.control_root = Path("/opt/twoman/control")
        self._states = {
            "node": _build_state(
                instance_name="node",
                backend=BACKEND_NODE,
                broker_base_url="https://host.example.com/parvaneh",
                public_base_path="/parvaneh",
                hidden_service_name="twoman-node.service",
                hidden_install_root="/opt/twoman-node",
                client_profile_name="Parvaneh Node",
            ),
            "bridge": _build_state(
                instance_name="bridge",
                backend=BACKEND_BRIDGE,
                broker_base_url="https://host.example.com/darvazeh",
                public_base_path="/darvazeh",
                hidden_service_name="twoman-bridge.service",
                hidden_install_root="/opt/twoman-bridge",
                client_profile_name="Darvazeh Bridge",
            ),
        }
        self._default = "node"
        self.instance_name = "node"
        self.state = self._states["node"]

    def registry(self) -> InstanceRegistry:
        return InstanceRegistry(
            default_instance=self._default,
            instances=[
                ManagedInstance(
                    name="bridge",
                    root="/opt/twoman/control/instances/bridge",
                    backend=BACKEND_BRIDGE,
                    broker_base_url=self._states["bridge"].broker_base_url,
                    public_origin=self._states["bridge"].public_origin,
                    public_base_path=self._states["bridge"].public_base_path,
                    hidden_install_root=self._states["bridge"].hidden_install_root,
                    hidden_service_name=self._states["bridge"].hidden_service_name,
                    client_profile_name=self._states["bridge"].client_profile_name,
                    site_name=self._states["bridge"].site_name,
                ),
                ManagedInstance(
                    name="node",
                    root="/opt/twoman/control/instances/node",
                    backend=BACKEND_NODE,
                    broker_base_url=self._states["node"].broker_base_url,
                    public_origin=self._states["node"].public_origin,
                    public_base_path=self._states["node"].public_base_path,
                    hidden_install_root=self._states["node"].hidden_install_root,
                    hidden_service_name=self._states["node"].hidden_service_name,
                    client_profile_name=self._states["node"].client_profile_name,
                    site_name=self._states["node"].site_name,
                ),
            ],
        )

    def switch_instance(self, instance_name: str | None) -> None:
        target = str(instance_name or self.instance_name)
        self.instance_name = target
        self.state = self._states[target]

    def set_default_instance(self, instance_name: str | None = None) -> None:
        self._default = str(instance_name or self.instance_name)
        self.switch_instance(self._default)

    def journal_tail(self) -> str:
        return f"log for {self.state.instance_name}\nsecond line"

    def capabilities_text(self) -> str:
        return f"{self.state.backend}: recommended"

    def hidden_route_text(self) -> str:
        return "WARP WireProxy via socks5h://127.0.0.1:1280"

    def outbound_route_text(self) -> str:
        return "WARP WireProxy via socks5h://127.0.0.1:1280"

    def install_command(self) -> list[str]:
        return ["/usr/local/bin/twoman", "install", "--instance", self.state.instance_name]

    def list_instances_text(self) -> str:
        registry = self.registry()
        lines = []
        for instance in registry.instances:
            marker = "*" if instance.name == registry.default_instance else " "
            lines.append(f"{marker} {instance.name}: {instance.backend} -> {instance.broker_base_url}")
        return "\n".join(lines)

    def verify(self) -> ActionResult:
        payload = {"ok": True, "instance": self.state.instance_name}
        return ActionResult(True, f"{self.state.instance_name} healthy", json.dumps(payload))

    def restart_agent(self) -> ActionResult:
        return ActionResult(True, f"{self.state.instance_name} restarted", "agent restarted")

    def restart_watchdog(self) -> ActionResult:
        return ActionResult(True, f"{self.state.instance_name} watchdog ran", "watchdog ran")

    def restart_upstream_proxy(self) -> ActionResult:
        return ActionResult(True, f"{self.state.instance_name} route proxy restarted", "wireproxy restarted")

    def redeploy_host(self) -> ActionResult:
        return ActionResult(True, f"{self.state.instance_name} host redeployed", "host redeployed")


class TwomanManagerAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_switches_instances_and_refreshes_logs(self) -> None:
        app = TwomanManagerApp(controller=_FakeController())
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await pilot.pause()

            deployment_detail = str(app.query_one("#deployment-detail", Static).content)
            log_output = str(app.query_one("#log-output", Static).content)
            self.assertIn("Broker URL: https://host.example.com/parvaneh", deployment_detail)
            self.assertIn("log for node", log_output)

            await pilot.click("#instance-bridge")
            await pilot.pause()
            await pilot.pause()

            deployment_detail = str(app.query_one("#deployment-detail", Static).content)
            log_output = str(app.query_one("#log-output", Static).content)
            self.assertIn("Broker URL: https://host.example.com/darvazeh", deployment_detail)
            self.assertIn("Hidden service: twoman-bridge.service", deployment_detail)
            self.assertIn("log for bridge", log_output)

    async def test_manager_sets_default_instance_from_sidebar(self) -> None:
        controller = _FakeController()
        app = TwomanManagerApp(controller=controller)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.click("#instance-bridge")
            await pilot.pause()
            await pilot.pause()
            await pilot.click("#action-set-default")
            await pilot.pause()

            bridge_button = app.query_one("#instance-bridge", Button)
            node_button = app.query_one("#instance-node", Button)
            result_output = str(app.query_one("#result-output", Static).content)

            self.assertEqual(controller._default, "bridge")
            self.assertIn("*> bridge", str(bridge_button.label))
            self.assertIn("node", str(node_button.label))
            self.assertIn("Default instance is now bridge.", result_output)

    async def test_manager_opens_capabilities_modal(self) -> None:
        app = TwomanManagerApp(controller=_FakeController())
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            modal_statics = [str(widget.content) for widget in app.screen.query(Static)]
            self.assertTrue(any("cloudlinux_node_selector: recommended" in item for item in modal_statics))

    async def test_reconfigure_sets_pending_command_and_exits_cleanly(self) -> None:
        app = TwomanManagerApp(controller=_FakeController())
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await pilot.click("#action-reconfigure")
            await pilot.pause()
            await pilot.click("#confirm-continue")
        self.assertEqual(app.pending_command, ["/usr/local/bin/twoman", "install", "--instance", "node"])

    async def test_verify_updates_result_output(self) -> None:
        app = TwomanManagerApp(controller=_FakeController())
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await pilot.click("#action-verify")
            await pilot.pause()
            await pilot.pause()
            result_output = str(app.query_one("#result-output", Static).content)
            self.assertIn('"instance": "node"', result_output)


if __name__ == "__main__":
    unittest.main()
