from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from twoman_control.models import BACKEND_PASSENGER, InstallState
from twoman_control.registry import (
    DEFAULT_INSTANCE_NAME,
    legacy_state_path,
    load_instance_state,
    load_registry,
    profile_share_path,
    resolve_instance_name,
    save_instance_state,
    set_default_instance,
    state_path,
)


def _sample_state(control_root: Path) -> InstallState:
    return InstallState(
        version=1,
        instance_name=DEFAULT_INSTANCE_NAME,
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
        cpanel_proxy_url="",
        public_proxy_url="",
        hidden_server_host="",
        hidden_server_port=22,
        hidden_server_user="",
        hidden_server_password="",
        hidden_server_ssh_key="",
        control_root=str(control_root),
        bundle_root=str(control_root / "bundle"),
        hidden_install_root="/opt/twoman-existing",
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
    )


class TwomanRegistryTests(unittest.TestCase):
    def test_save_instance_state_updates_registry_and_profile_share(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_root = Path(temp_dir)
            state = _sample_state(control_root)
            state.instance_name = "node"
            state.hidden_install_root = "/opt/twoman-node"

            save_instance_state(control_root, state)

            registry = load_registry(control_root)
            self.assertEqual(registry.default_instance, "node")
            self.assertEqual(resolve_instance_name(control_root, None), "node")
            self.assertTrue(state_path(control_root, "node").exists())
            self.assertTrue(profile_share_path(control_root, "node").exists())
            loaded = load_instance_state(control_root, "node")
            self.assertEqual(loaded.hidden_install_root, "/opt/twoman-node")

    def test_legacy_state_is_migrated_into_default_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_root = Path(temp_dir)
            state = _sample_state(control_root)
            state.save(legacy_state_path(control_root))

            registry = load_registry(control_root)

            self.assertEqual(registry.default_instance, DEFAULT_INSTANCE_NAME)
            self.assertTrue(state_path(control_root, DEFAULT_INSTANCE_NAME).exists())
            self.assertEqual(load_instance_state(control_root).client_token, state.client_token)

    def test_set_default_instance_switches_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_root = Path(temp_dir)
            default_state = _sample_state(control_root)
            save_instance_state(control_root, default_state)
            node_state = _sample_state(control_root)
            node_state.instance_name = "node"
            node_state.hidden_install_root = "/opt/twoman-node"
            save_instance_state(control_root, node_state)

            set_default_instance(control_root, "node")

            self.assertEqual(resolve_instance_name(control_root, None), "node")


if __name__ == "__main__":
    unittest.main()
