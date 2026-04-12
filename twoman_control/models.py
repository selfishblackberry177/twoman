from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


BACKEND_BRIDGE = "cpanel_runtime_bridge"
BACKEND_PASSENGER = "passenger_python"
BACKEND_NODE = "cloudlinux_node_selector"


@dataclass(slots=True)
class BackendCapability:
    key: str
    label: str
    available: bool
    reason: str = ""
    recommended: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BackendCapability":
        return cls(
            key=str(payload.get("key", "")).strip(),
            label=str(payload.get("label", "")).strip(),
            available=bool(payload.get("available", False)),
            reason=str(payload.get("reason", "")).strip(),
            recommended=bool(payload.get("recommended", False)),
            details=dict(payload.get("details") or {}),
        )


@dataclass(slots=True)
class GeneratedDefaults:
    deployment_id: str
    site_name: str
    site_slug: str
    passenger_base_path: str
    node_base_path: str
    passenger_app_name: str
    passenger_app_root: str
    node_app_root: str
    admin_script_name: str
    client_token: str
    agent_token: str
    agent_peer_id: str
    hidden_install_root: str
    hidden_service_name: str
    hidden_service_user: str
    hidden_service_group: str
    watchdog_service_name: str
    watchdog_timer_name: str
    client_http_port: int = 18092
    client_socks_port: int = 11092
    verify_tls: bool = True
    client_http2_ctl: bool = True
    client_http2_data: bool = False


@dataclass(slots=True)
class InstallState:
    version: int
    backend: str
    public_origin: str
    public_base_path: str
    broker_base_url: str
    client_token: str
    agent_token: str
    client_profile_name: str
    profile_share_text: str
    cpanel_base_url: str
    cpanel_username: str
    cpanel_password: str
    cpanel_home: str
    control_root: str
    bundle_root: str
    hidden_install_root: str
    hidden_service_name: str
    hidden_service_user: str
    hidden_service_group: str
    watchdog_service_name: str
    watchdog_timer_name: str
    agent_peer_id: str
    verify_tls: bool
    client_http2_ctl: bool
    client_http2_data: bool
    client_http_port: int
    client_socks_port: int
    deployment_id: str
    site_name: str
    site_slug: str
    bridge_public_base_path: str = ""
    passenger_app_name: str = ""
    passenger_app_root: str = ""
    node_app_root: str = ""
    node_app_uri: str = ""
    admin_script_name: str = ""
    hidden_upstream_proxy_url: str = ""
    hidden_upstream_proxy_label: str = ""
    host_capabilities: list[BackendCapability] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["host_capabilities"] = [item.to_dict() for item in self.host_capabilities]
        return payload

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        path.chmod(0o600)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InstallState":
        return cls(
            version=int(payload.get("version", 1)),
            backend=str(payload.get("backend", "")).strip(),
            public_origin=str(payload.get("public_origin", "")).strip(),
            public_base_path=str(payload.get("public_base_path", "")).strip(),
            broker_base_url=str(payload.get("broker_base_url", "")).strip(),
            client_token=str(payload.get("client_token", "")).strip(),
            agent_token=str(payload.get("agent_token", "")).strip(),
            client_profile_name=str(payload.get("client_profile_name", "")).strip(),
            profile_share_text=str(payload.get("profile_share_text", "")).strip(),
            cpanel_base_url=str(payload.get("cpanel_base_url", "")).strip(),
            cpanel_username=str(payload.get("cpanel_username", "")).strip(),
            cpanel_password=str(payload.get("cpanel_password", "")).strip(),
            cpanel_home=str(payload.get("cpanel_home", "")).strip(),
            control_root=str(payload.get("control_root", "")).strip(),
            bundle_root=str(payload.get("bundle_root", "")).strip(),
            hidden_install_root=str(payload.get("hidden_install_root", "")).strip(),
            hidden_service_name=str(payload.get("hidden_service_name", "")).strip(),
            hidden_service_user=str(payload.get("hidden_service_user", "")).strip(),
            hidden_service_group=str(payload.get("hidden_service_group", "")).strip(),
            watchdog_service_name=str(payload.get("watchdog_service_name", "")).strip(),
            watchdog_timer_name=str(payload.get("watchdog_timer_name", "")).strip(),
            agent_peer_id=str(payload.get("agent_peer_id", "")).strip(),
            verify_tls=bool(payload.get("verify_tls", True)),
            client_http2_ctl=bool(payload.get("client_http2_ctl", True)),
            client_http2_data=bool(payload.get("client_http2_data", False)),
            client_http_port=int(payload.get("client_http_port", 18092)),
            client_socks_port=int(payload.get("client_socks_port", 11092)),
            deployment_id=str(payload.get("deployment_id", "")).strip(),
            site_name=str(payload.get("site_name", "")).strip(),
            site_slug=str(payload.get("site_slug", "")).strip(),
            bridge_public_base_path=str(payload.get("bridge_public_base_path", "")).strip(),
            passenger_app_name=str(payload.get("passenger_app_name", "")).strip(),
            passenger_app_root=str(payload.get("passenger_app_root", "")).strip(),
            node_app_root=str(payload.get("node_app_root", "")).strip(),
            node_app_uri=str(payload.get("node_app_uri", "")).strip(),
            admin_script_name=str(payload.get("admin_script_name", "")).strip(),
            hidden_upstream_proxy_url=str(payload.get("hidden_upstream_proxy_url", "")).strip(),
            hidden_upstream_proxy_label=str(payload.get("hidden_upstream_proxy_label", "")).strip(),
            host_capabilities=[
                BackendCapability.from_dict(item)
                for item in list(payload.get("host_capabilities") or [])
            ],
            notes=[str(item) for item in list(payload.get("notes") or [])],
        )

    @classmethod
    def load(cls, path: Path) -> "InstallState":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
