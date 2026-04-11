from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from twoman_control.installer import (
    _normalize_base_path,
    _install_local_hidden_server,
    build_broker_base_url,
    collect_install_args,
    state_path,
)
from twoman_control.models import BACKEND_BRIDGE, BACKEND_NODE, BACKEND_PASSENGER, InstallState


def _sample_state(control_root: Path) -> InstallState:
    return InstallState(
        version=1,
        backend=BACKEND_PASSENGER,
        public_origin="https://host.example.com",
        public_base_path="/sahar-honar-221b/payesh-asnad",
        broker_base_url="https://host.example.com/sahar-honar-221b/payesh-asnad",
        client_token="client-token",
        agent_token="agent-token",
        client_profile_name="Primary Host",
        profile_share_text="twoman://profile?data=abc",
        cpanel_base_url="https://host.example.com:2083",
        cpanel_username="cpanel-user",
        cpanel_password="cpanel-pass",
        cpanel_home="/home/cpanel-user",
        control_root=str(control_root),
        bundle_root=str(control_root / "bundle"),
        hidden_install_root="/opt/twoman-existing",
        hidden_service_name="twoman-agent.service",
        hidden_service_user="twoman",
        hidden_service_group="twoman",
        watchdog_service_name="twoman-agent-watchdog.service",
        watchdog_timer_name="twoman-agent-watchdog.timer",
        agent_peer_id="agent-main",
        verify_tls=False,
        client_http2_ctl=True,
        client_http2_data=False,
        client_http_port=18092,
        client_socks_port=11092,
        deployment_id="deadbeefcafe",
        site_name="سحر هنر",
        site_slug="sahar-honar-221b",
        bridge_public_base_path="/api/v1/telemetry",
        passenger_app_name="sahar_honar_221b",
        passenger_app_root="/home/cpanel-user/sahar_honar_221b",
        node_app_root="/home/cpanel-user/sahar_honar_221b_node",
        node_app_uri="/sahar-honar-221b/ertebat-negah",
        admin_script_name="sahar_honar_221b_negahban.php",
        hidden_upstream_proxy_url="socks5://127.0.0.1:1280",
        hidden_upstream_proxy_label="wireproxy",
    )


class TwomanInstallerTests(unittest.TestCase):
    def test_normalize_base_path_adds_leading_slash_and_removes_trailing_slash(self) -> None:
        self.assertEqual(_normalize_base_path("darvazeh"), "/darvazeh")
        self.assertEqual(_normalize_base_path("/api/v1/telemetry/"), "/api/v1/telemetry")
        self.assertEqual(_normalize_base_path(""), "/")

    def test_build_broker_base_url_uses_backend_specific_shape(self) -> None:
        self.assertEqual(
            build_broker_base_url("https://host.example.com", BACKEND_PASSENGER, "/darvazeh"),
            "https://host.example.com/darvazeh",
        )
        self.assertEqual(
            build_broker_base_url("https://host.example.com", BACKEND_NODE, "/darvazeh-node"),
            "https://host.example.com/darvazeh-node",
        )
        self.assertEqual(
            build_broker_base_url(
                "https://host.example.com",
                BACKEND_BRIDGE,
                "/rahkar",
                bridge_public_base_path="/api/v1/telemetry",
            ),
            "https://host.example.com/rahkar/api/v1/telemetry",
        )

    def test_collect_install_args_reuses_existing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_root = Path(temp_dir)
            _sample_state(control_root).save(state_path(control_root))
            args = collect_install_args(
                argparse.Namespace(
                    repo_root=Path("/tmp/repo"),
                    control_root=control_root,
                    install_root=None,
                    public_origin="",
                    cpanel_base_url="",
                    cpanel_username="",
                    cpanel_password="",
                    cpanel_home="",
                    site_name="",
                    backend="",
                    public_base_path="",
                    bridge_public_base_path="",
                    passenger_app_name="",
                    passenger_app_root="",
                    node_app_root="",
                    node_app_uri="",
                    admin_script_name="",
                    hidden_service_name="",
                    hidden_service_user="",
                    hidden_service_group="",
                    watchdog_service_name="",
                    watchdog_timer_name="",
                    hidden_upstream_proxy_url="",
                    hidden_upstream_proxy_label="",
                    non_interactive=True,
                    customize=False,
                    skip_helper_probe=False,
                    verify_tls=None,
                )
            )

        self.assertEqual(args.public_origin, "https://host.example.com")
        self.assertEqual(args.cpanel_base_url, "https://host.example.com:2083")
        self.assertEqual(args.cpanel_username, "cpanel-user")
        self.assertEqual(args.cpanel_password, "cpanel-pass")
        self.assertEqual(args.cpanel_home, "/home/cpanel-user")
        self.assertEqual(args.backend, BACKEND_PASSENGER)
        self.assertEqual(args.public_base_path, "/sahar-honar-221b/payesh-asnad")
        self.assertEqual(args.bridge_public_base_path, "/api/v1/telemetry")
        self.assertEqual(args.install_root, Path("/opt/twoman-existing"))
        self.assertFalse(args.verify_tls)
        self.assertEqual(args.hidden_upstream_proxy_url, "socks5://127.0.0.1:1280")
        self.assertEqual(args.hidden_upstream_proxy_label, "wireproxy")

    def test_collect_install_args_keeps_explicit_noninteractive_values_without_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = collect_install_args(
                argparse.Namespace(
                    repo_root=Path("/tmp/repo"),
                    control_root=Path(temp_dir) / "control",
                    install_root=Path("/opt/twoman"),
                    public_origin="https://host.example.com",
                    cpanel_base_url="https://host.example.com:2083",
                    cpanel_username="cpanel-user",
                    cpanel_password="cpanel-pass",
                    cpanel_home="/home/cpanel-user",
                    site_name="سحر هنر",
                    backend=BACKEND_PASSENGER,
                    public_base_path="/darvazeh",
                    bridge_public_base_path="/api/v1/telemetry",
                    passenger_app_name="darvazeh_app",
                    passenger_app_root="/home/cpanel-user/darvazeh_app",
                    node_app_root="",
                    node_app_uri="",
                    admin_script_name="",
                    hidden_service_name="twoman-agent.service",
                    hidden_service_user="twoman",
                    hidden_service_group="twoman",
                    watchdog_service_name="twoman-agent-watchdog.service",
                    watchdog_timer_name="twoman-agent-watchdog.timer",
                    hidden_upstream_proxy_url="",
                    hidden_upstream_proxy_label="",
                    non_interactive=True,
                    customize=False,
                    skip_helper_probe=False,
                    verify_tls=True,
                )
            )

        self.assertEqual(args.public_origin, "https://host.example.com")
        self.assertEqual(args.cpanel_base_url, "https://host.example.com:2083")
        self.assertEqual(args.cpanel_username, "cpanel-user")
        self.assertEqual(args.cpanel_password, "cpanel-pass")
        self.assertEqual(args.cpanel_home, "/home/cpanel-user")
        self.assertEqual(args.backend, BACKEND_PASSENGER)
        self.assertEqual(args.public_base_path, "/darvazeh")
        self.assertTrue(args.verify_tls)
        self.assertEqual(args.hidden_upstream_proxy_url, "")

    @patch("twoman_control.installer._run_script")
    def test_install_local_hidden_server_passes_hidden_proxy_env(self, run_script_mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = _sample_state(Path(temp_dir))
            run_script_mock.return_value = subprocess.CompletedProcess(
                args=["bash", "scripts/install_hidden_server_local.sh"],
                returncode=0,
                stdout="ok",
                stderr="",
            )

            _install_local_hidden_server(Path("/tmp/repo"), state)

        env = run_script_mock.call_args.args[1]
        self.assertEqual(env["TWOMAN_UPSTREAM_PROXY_URL"], "socks5://127.0.0.1:1280")
        self.assertEqual(env["TWOMAN_UPSTREAM_PROXY_LABEL"], "wireproxy")


if __name__ == "__main__":
    unittest.main()
