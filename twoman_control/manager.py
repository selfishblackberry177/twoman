from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from twoman_http import httpx_request
from twoman_control.installer import LAUNCHER_PATH
from twoman_control.models import (
    BACKEND_BRIDGE,
    BACKEND_NODE,
    BACKEND_PASSENGER,
    InstanceRegistry,
    InstallState,
)
from twoman_control.registry import (
    load_instance_state,
    load_registry,
    resolve_instance_name,
    set_default_instance as registry_set_default_instance,
)

try:
    from textual import work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Header, Static
    from textual.worker import Worker, WorkerState

    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


@dataclass(slots=True)
class ActionResult:
    ok: bool
    summary: str
    details: str = ""


@dataclass(slots=True)
class WorkerPayload:
    kind: str
    value: str | ActionResult


class ManagerController:
    def __init__(self, control_root: Path, instance_name: str | None = None) -> None:
        self.control_root = control_root
        self.instance_name = ""
        self.state: InstallState
        self.switch_instance(instance_name)

    def registry(self) -> InstanceRegistry:
        return load_registry(self.control_root)

    def switch_instance(self, instance_name: str | None) -> None:
        self.instance_name = resolve_instance_name(self.control_root, instance_name)
        self.state = load_instance_state(self.control_root, self.instance_name)

    def set_default_instance(self, instance_name: str | None = None) -> None:
        resolved = resolve_instance_name(self.control_root, instance_name or self.instance_name)
        registry_set_default_instance(self.control_root, resolved)
        self.switch_instance(resolved)

    def list_instances_text(self) -> str:
        registry = self.registry()
        lines = []
        for instance in registry.instances:
            marker = "*" if instance.name == registry.default_instance else " "
            lines.append(f"{marker} {instance.name}: {instance.backend} -> {instance.broker_base_url}")
        return "\n".join(lines) or "No Twoman instances are installed."

    @property
    def bundle_root(self) -> Path:
        return Path(self.state.bundle_root)

    def _run(self, command: list[str]) -> ActionResult:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        details = result.stdout.strip()
        if result.stderr.strip():
            details = f"{details}\n{result.stderr.strip()}".strip()
        return ActionResult(result.returncode == 0, details.splitlines()[0] if details else "ok", details)

    def hidden_route_text(self) -> str:
        if not self.state.hidden_upstream_proxy_url:
            return "direct"
        if self.state.hidden_upstream_proxy_label == "wireproxy":
            return f"WARP WireProxy via {self.state.hidden_upstream_proxy_url}"
        return f"custom upstream proxy via {self.state.hidden_upstream_proxy_url}"

    def outbound_route_text(self) -> str:
        if not self.state.hidden_outbound_proxy_url:
            return "direct"
        if self.state.hidden_outbound_proxy_label == "wireproxy":
            return f"WARP WireProxy via {self.state.hidden_outbound_proxy_url}"
        return f"custom outbound proxy via {self.state.hidden_outbound_proxy_url}"

    def verify(self) -> ActionResult:
        service_state = self._run(["systemctl", "is-active", self.state.hidden_service_name])
        timer_state = self._run(["systemctl", "is-active", self.state.watchdog_timer_name])
        route_state = None
        if "wireproxy" in {self.state.hidden_upstream_proxy_label, self.state.hidden_outbound_proxy_label}:
            route_state = self._run(["systemctl", "is-active", "wireproxy.service"])
        try:
            response = httpx_request(
                "GET",
                f"{self.state.broker_base_url.rstrip('/')}/health",
                headers={"Authorization": f"Bearer {self.state.client_token}"},
                timeout=20.0,
                verify=self.state.verify_tls,
                proxy_url=self.state.hidden_upstream_proxy_url or None,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            ok = bool(payload.get("ok")) and service_state.ok and timer_state.ok
            if route_state is not None:
                ok = ok and route_state.ok
            summary = "healthy" if ok else "degraded"
            details_payload = {
                "service": service_state.summary,
                "watchdog": timer_state.summary,
                "host_route": self.hidden_route_text(),
                "outbound_route": self.outbound_route_text(),
                "broker_ok": payload.get("ok"),
                "peers": payload.get("stats", {}).get("peers"),
                "streams": payload.get("stats", {}).get("streams"),
            }
            if route_state is not None:
                details_payload["route_proxy_service"] = route_state.summary
            details = json.dumps(details_payload, indent=2)
            return ActionResult(ok, summary, details)
        except Exception as error:
            return ActionResult(False, "health check failed", str(error))

    def restart_agent(self) -> ActionResult:
        return self._run(["systemctl", "restart", self.state.hidden_service_name])

    def restart_watchdog(self) -> ActionResult:
        return self._run(["systemctl", "start", self.state.watchdog_service_name])

    def restart_upstream_proxy(self) -> ActionResult:
        if "wireproxy" not in {self.state.hidden_upstream_proxy_label, self.state.hidden_outbound_proxy_label}:
            return ActionResult(False, "no managed WARP proxy", "This deployment is not using managed WARP WireProxy.")
        return self._run(["systemctl", "restart", "wireproxy.service"])

    def journal_tail(self) -> str:
        result = subprocess.run(
            ["journalctl", "-u", self.state.hidden_service_name, "-n", "120", "--no-pager"],
            text=True,
            capture_output=True,
            check=False,
        )
        return (result.stdout or result.stderr or "No logs available.").strip()

    def capabilities_text(self) -> str:
        if not self.state.host_capabilities:
            return "No capability data was recorded during installation."
        lines = []
        for capability in self.state.host_capabilities:
            status = "recommended" if capability.recommended else "available" if capability.available else "unavailable"
            lines.append(f"{capability.label}: {status}")
            if capability.reason:
                lines.append(f"  {capability.reason}")
        return "\n".join(lines)

    def redeploy_host(self) -> ActionResult:
        state = self.state
        if state.backend == BACKEND_PASSENGER:
            env = {
                "TWOMAN_CPANEL_BASE_URL": state.cpanel_base_url,
                "TWOMAN_CPANEL_USERNAME": state.cpanel_username,
                "TWOMAN_CPANEL_PASSWORD": state.cpanel_password,
                "TWOMAN_CPANEL_HOME": state.cpanel_home,
                "TWOMAN_PUBLIC_ORIGIN": state.public_origin,
                "TWOMAN_PUBLIC_BASE_PATH": state.public_base_path,
                "TWOMAN_APP_NAME": state.passenger_app_name,
                "TWOMAN_APP_ROOT": state.passenger_app_root,
                "TWOMAN_CLIENT_TOKEN": state.client_token,
                "TWOMAN_AGENT_TOKEN": state.agent_token,
                "TWOMAN_CAMOUFLAGE_SITE_ENABLED": "true",
                "TWOMAN_CAMOUFLAGE_DEPLOYMENT_ID": state.deployment_id,
                "TWOMAN_CAMOUFLAGE_SITE_NAME": state.site_name,
            }
            script = self.bundle_root / "scripts" / "deploy_host_passenger.sh"
        elif state.backend == BACKEND_NODE:
            env = {
                "TWOMAN_CPANEL_BASE_URL": state.cpanel_base_url,
                "TWOMAN_CPANEL_USERNAME": state.cpanel_username,
                "TWOMAN_CPANEL_PASSWORD": state.cpanel_password,
                "TWOMAN_CPANEL_HOME": state.cpanel_home,
                "TWOMAN_PUBLIC_HOST": state.public_origin.replace("https://", "").replace("http://", "").strip("/"),
                "TWOMAN_NODE_APP_ROOT": state.node_app_root,
                "TWOMAN_NODE_APP_URI": state.node_app_uri,
                "TWOMAN_ADMIN_SCRIPT_NAME": state.admin_script_name,
                "TWOMAN_CLIENT_TOKEN": state.client_token,
                "TWOMAN_AGENT_TOKEN": state.agent_token,
                "TWOMAN_CAMOUFLAGE_SITE_ENABLED": "true",
                "TWOMAN_CAMOUFLAGE_DEPLOYMENT_ID": state.deployment_id,
                "TWOMAN_CAMOUFLAGE_SITE_NAME": state.site_name,
            }
            script = self.bundle_root / "scripts" / "deploy_host_node_selector.sh"
        else:
            env = {
                "TWOMAN_CPANEL_BASE_URL": state.cpanel_base_url,
                "TWOMAN_CPANEL_USERNAME": state.cpanel_username,
                "TWOMAN_CPANEL_PASSWORD": state.cpanel_password,
                "TWOMAN_CPANEL_HOME": state.cpanel_home,
                "TWOMAN_PUBLIC_ORIGIN": state.public_origin,
                "TWOMAN_PUBLIC_BASE_PATH": state.public_base_path,
                "TWOMAN_BRIDGE_PUBLIC_BASE_PATH": state.bridge_public_base_path,
                "TWOMAN_CLIENT_TOKEN": state.client_token,
                "TWOMAN_AGENT_TOKEN": state.agent_token,
                "TWOMAN_CAMOUFLAGE_SITE_ENABLED": "true",
                "TWOMAN_CAMOUFLAGE_DEPLOYMENT_ID": state.deployment_id,
                "TWOMAN_CAMOUFLAGE_SITE_NAME": state.site_name,
            }
            script = self.bundle_root / "scripts" / "deploy_host.sh"
        merged_env = os.environ.copy()
        merged_env.update(env)
        result = subprocess.run(
            ["bash", str(script)],
            cwd=self.bundle_root,
            env=merged_env,
            text=True,
            capture_output=True,
            check=False,
        )
        details = f"{result.stdout}\n{result.stderr}".strip()
        summary = "host redeployed" if result.returncode == 0 else "host redeploy failed"
        return ActionResult(result.returncode == 0, summary, details)

    def install_command(self) -> list[str]:
        return [str(LAUNCHER_PATH), "install", "--instance", self.state.instance_name]


def _print_result(result: ActionResult) -> None:
    print("")
    print(result.summary)
    if result.details:
        print(result.details)


def run_basic_manager(control_root: Path, instance_name: str | None = None) -> None:
    controller = ManagerController(control_root, instance_name)
    while True:
        state = controller.state
        print("")
        print("Twoman")
        print("-------")
        print(f"Instance: {state.instance_name}")
        print(f"Broker: {state.broker_base_url}")
        print(f"Hidden service: {state.hidden_service_name}")
        print(f"Install root: {state.hidden_install_root}")
        print(f"Hidden route: {controller.hidden_route_text()}")
        print(f"Outbound route: {controller.outbound_route_text()}")
        print("")
        print("1. Verify health")
        print("2. Restart hidden agent")
        print("3. Restart upstream proxy")
        print("4. Run watchdog")
        print("5. Redeploy public host")
        print("6. Show import text")
        print("7. Show host capabilities")
        print("8. Show instances")
        print("9. Show recent logs")
        print("10. Reconfigure")
        print("11. Quit")
        choice = input("Choose an action [1-11]: ").strip()
        if choice == "1":
            _print_result(controller.verify())
        elif choice == "2":
            _print_result(controller.restart_agent())
        elif choice == "3":
            _print_result(controller.restart_upstream_proxy())
        elif choice == "4":
            _print_result(controller.restart_watchdog())
        elif choice == "5":
            _print_result(controller.redeploy_host())
        elif choice == "6":
            print("")
            print(state.profile_share_text)
        elif choice == "7":
            print("")
            print(controller.capabilities_text())
        elif choice == "8":
            print("")
            print(controller.list_instances_text())
        elif choice == "9":
            print("")
            print(controller.journal_tail())
        elif choice == "10":
            subprocess.run(controller.install_command(), check=False)
            return
        elif choice == "11":
            return
        else:
            print("Choose one of the listed numbers.")


if TEXTUAL_AVAILABLE:
    APP_CSS = """
Screen {
    background: $surface;
    color: $text;
}

#layout {
    height: 1fr;
    padding: 1;
}

#sidebar {
    width: 34;
    min-width: 30;
    max-width: 42;
    border: round $accent;
    padding: 1;
    margin-right: 1;
}

#content {
    padding-right: 1;
}

Button {
    width: 1fr;
    margin-bottom: 1;
}

#sidebar Button {
    margin-bottom: 1;
    height: auto;
}

ModalScreen {
    align: center middle;
    background: $background 60%;
}

.dialog {
    width: 90;
    height: auto;
    background: $surface;
    color: $text;
    border: round $accent;
    padding: 1 2;
}

.dialog-body {
    max-height: 18;
    overflow-y: auto;
    margin-bottom: 1;
}

.panel {
    border: round $accent;
    padding: 1;
    margin-bottom: 1;
    height: auto;
}

.section-title {
    text-style: bold;
    color: $accent;
    margin-bottom: 1;
}

#status-banner {
    border: round $success;
}

#result-output,
#log-output,
#client-detail,
#deployment-detail {
    background: $boost;
    border: tall $panel;
    padding: 1;
    height: auto;
}

#result-output {
    min-height: 6;
}

#log-output {
    min-height: 14;
}
"""


    class TextScreen(ModalScreen[None]):
        def __init__(self, title: str, body: str) -> None:
            super().__init__()
            self.title = title
            self.body = body

        def compose(self) -> ComposeResult:
            with Vertical(classes="dialog"):
                yield Static(self.title, classes="section-title")
                with VerticalScroll(classes="dialog-body"):
                    yield Static(self.body)
                yield Button("Close", id="close", variant="primary")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "close":
                self.dismiss(None)


    class ConfirmCommandScreen(ModalScreen[None]):
        def __init__(self, title: str, body: str, command: list[str]) -> None:
            super().__init__()
            self.title = title
            self.body = body
            self.command = list(command)

        def compose(self) -> ComposeResult:
            with Vertical(classes="dialog"):
                yield Static(self.title, classes="section-title")
                with VerticalScroll(classes="dialog-body"):
                    yield Static(self.body)
                with Horizontal():
                    yield Button("Continue", id="confirm-continue", variant="warning")
                    yield Button("Cancel", id="confirm-cancel")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "confirm-cancel":
                self.dismiss(None)
                return
            if event.button.id == "confirm-continue":
                app = self.app
                if hasattr(app, "pending_command"):
                    app.pending_command = list(self.command)
                app.exit()


    class TwomanManagerApp(App[None]):
        TITLE = "Twoman"
        SUB_TITLE = "Server control"
        CSS = APP_CSS
        BINDINGS = [
            Binding("q", "quit", "Quit", show=True),
            Binding("v", "verify", "Verify", show=True),
            Binding("l", "refresh_logs", "Logs", show=True),
            Binding("r", "restart_agent", "Restart agent", show=True),
            Binding("p", "restart_upstream_proxy", "Restart route proxy", show=True),
            Binding("c", "show_config", "Config", show=True),
            Binding("h", "show_capabilities", "Capabilities", show=True),
            Binding("d", "set_default", "Set default", show=True),
        ]

        def __init__(
            self,
            control_root: Path | None = None,
            controller: ManagerController | None = None,
            instance_name: str | None = None,
        ) -> None:
            super().__init__()
            if controller is not None:
                self.controller = controller
            elif control_root is not None:
                self.controller = ManagerController(control_root, instance_name)
            else:
                raise ValueError("TwomanManagerApp requires either control_root or controller")
            self.last_result = ActionResult(False, "Not checked yet", "")
            self.last_output = "Select an action to inspect or control the active instance."
            self.log_output = "Loading logs..."
            self.busy_action: str | None = None
            self.pending_command: list[str] | None = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Horizontal(id="layout"):
                with Vertical(id="sidebar"):
                    yield Static("Instances", classes="section-title")
                    for instance in self.controller.registry().instances:
                        yield Button("", id=f"instance-{instance.name}")
                    yield Static("Instance Actions", classes="section-title")
                    yield Button("Set Default", id="action-set-default")
                    yield Button("Refresh Logs", id="action-refresh-logs")
                    yield Button("Show Config", id="action-show-config")
                    yield Button("Reconfigure", id="action-reconfigure", variant="warning")
                    yield Button("Quit", id="action-quit", variant="error")
                with VerticalScroll(id="content"):
                    with Vertical(id="status-banner", classes="panel"):
                        yield Static("Twoman deployment", id="status-text")
                        yield Static("", id="status-detail")
                    with Vertical(classes="panel"):
                        yield Static("Deployment", classes="section-title")
                        yield Static("", id="deployment-detail")
                    with Vertical(classes="panel"):
                        yield Static("Operations", classes="section-title")
                        with Horizontal():
                            yield Button("Verify", id="action-verify", variant="primary")
                            yield Button("Restart Agent", id="action-restart-agent")
                            yield Button("Restart Route Proxy", id="action-restart-upstream")
                        with Horizontal():
                            yield Button("Run Watchdog", id="action-restart-watchdog")
                            yield Button("Redeploy Host", id="action-redeploy")
                            yield Button("Capabilities", id="action-show-capabilities")
                    with Vertical(classes="panel"):
                        yield Static("Client Config", classes="section-title")
                        yield Static("", id="client-detail")
                    with Vertical(classes="panel"):
                        yield Static("Result", classes="section-title")
                        yield Static("", id="result-output")
                    with Vertical(classes="panel"):
                        yield Static("Recent Logs", classes="section-title")
                        yield Static("", id="log-output")
            yield Footer()

        def on_mount(self) -> None:
            self.refresh_view()
            self.start_task("logs")

        def update_instance_buttons(self) -> None:
            registry = self.controller.registry()
            selected = self.controller.state.instance_name
            for instance in registry.instances:
                button = self.query_one(f"#instance-{instance.name}", Button)
                markers = []
                markers.append("*" if instance.name == registry.default_instance else " ")
                markers.append(">" if instance.name == selected else " ")
                button.label = f"{''.join(markers)} {instance.name}"
                if instance.name == selected:
                    button.variant = "primary"
                elif instance.name == registry.default_instance:
                    button.variant = "success"
                else:
                    button.variant = "default"

        def set_busy_state(self, busy: bool) -> None:
            button_ids = [
                "action-set-default",
                "action-refresh-logs",
                "action-show-config",
                "action-reconfigure",
                "action-verify",
                "action-restart-agent",
                "action-restart-upstream",
                "action-restart-watchdog",
                "action-redeploy",
                "action-show-capabilities",
            ]
            for button_id in button_ids:
                self.query_one(f"#{button_id}", Button).disabled = busy
            for instance in self.controller.registry().instances:
                self.query_one(f"#instance-{instance.name}", Button).disabled = busy

        def refresh_view(self) -> None:
            state = self.controller.state
            if self.busy_action:
                summary = f"{state.backend} · running {self.busy_action}"
            else:
                summary = f"{state.backend} · {self.last_result.summary}"
            with self.batch_update():
                self.query_one("#status-text", Static).update(summary)
                self.query_one("#status-detail", Static).update(
                    f"instance={state.instance_name} · {state.broker_base_url}\n"
                    f"service={state.hidden_service_name} · watchdog={state.watchdog_timer_name}"
                )
                self.query_one("#deployment-detail", Static).update(
                    "\n".join(
                        [
                            f"Public origin: {state.public_origin}",
                            f"Broker URL: {state.broker_base_url}",
                            f"Base path: {state.public_base_path}",
                            f"Bridge broker path: {state.bridge_public_base_path or '(same as base path)'}",
                            f"Hidden root: {state.hidden_install_root}",
                            f"Hidden service: {state.hidden_service_name}",
                            f"Agent peer: {state.agent_peer_id}",
                            f"Hidden route: {self.controller.hidden_route_text()}",
                            f"Outbound route: {self.controller.outbound_route_text()}",
                            f"TLS verify: {state.verify_tls}",
                        ]
                    )
                )
                self.query_one("#client-detail", Static).update(
                    "\n".join(
                        [
                            f"Profile: {state.client_profile_name}",
                            f"HTTP port: {state.client_http_port}",
                            f"SOCKS port: {state.client_socks_port}",
                            f"Launcher: {LAUNCHER_PATH}",
                        ]
                    )
                )
                self.query_one("#result-output", Static).update(self.last_output)
                self.query_one("#log-output", Static).update(self.log_output)
                self.update_instance_buttons()
                self.set_busy_state(self.busy_action is not None)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id
            if button_id and button_id.startswith("instance-"):
                self.switch_instance(button_id[len("instance-") :])
                return
            if button_id == "action-quit":
                self.exit()
                return
            if button_id == "action-set-default":
                self.action_set_default()
                return
            if button_id == "action-refresh-logs":
                self.action_refresh_logs()
                return
            if button_id == "action-show-config":
                self.push_screen(TextScreen("Twoman Import Text", self.controller.state.profile_share_text))
                return
            if button_id == "action-show-capabilities":
                self.push_screen(TextScreen("Detected Host Capabilities", self.controller.capabilities_text()))
                return
            if button_id == "action-reconfigure":
                self.push_screen(
                    ConfirmCommandScreen(
                        "Run the installer?",
                        "Twoman will leave the TUI and start the install wizard for the selected instance.\n\n"
                        f"Instance: {self.controller.state.instance_name}\n"
                        f"Command: {' '.join(self.controller.install_command())}",
                        self.controller.install_command(),
                    )
                )
                return
            if button_id == "action-verify":
                self.action_verify()
                return
            if button_id == "action-restart-agent":
                self.action_restart_agent()
                return
            if button_id == "action-restart-upstream":
                self.action_restart_upstream_proxy()
                return
            if button_id == "action-restart-watchdog":
                self.action_restart_watchdog()
                return
            if button_id == "action-redeploy":
                self.action_redeploy()

        def switch_instance(self, instance_name: str) -> None:
            if self.busy_action:
                self.notify("Wait for the current action to finish before switching instances.", severity="warning")
                return
            self.controller.switch_instance(instance_name)
            self.last_result = ActionResult(True, f"active instance set to {instance_name}", "")
            self.last_output = f"Selected instance: {instance_name}"
            self.log_output = "Loading logs..."
            self.refresh_view()
            self.start_task("logs")

        def start_task(self, kind: str) -> None:
            if self.busy_action:
                self.notify("Another action is still running.", severity="warning")
                return
            self.busy_action = kind
            self.refresh_view()
            self.run_task(kind)

        def action_verify(self) -> None:
            self.start_task("verify")

        def action_restart_agent(self) -> None:
            self.start_task("restart-agent")

        def action_restart_upstream_proxy(self) -> None:
            self.start_task("restart-upstream-proxy")

        def action_show_config(self) -> None:
            self.push_screen(TextScreen("Twoman Import Text", self.controller.state.profile_share_text))

        def action_show_capabilities(self) -> None:
            self.push_screen(TextScreen("Detected Host Capabilities", self.controller.capabilities_text()))

        def action_refresh_logs(self) -> None:
            self.start_task("logs")

        def action_set_default(self) -> None:
            state = self.controller.state
            self.controller.set_default_instance(state.instance_name)
            self.last_result = ActionResult(True, f"default instance set to {state.instance_name}", "")
            self.last_output = f"Default instance is now {state.instance_name}."
            self.refresh_view()

        @work(thread=True, exclusive=True)
        def run_task(self, kind: str) -> WorkerPayload:
            if kind == "logs":
                return WorkerPayload(kind="logs", value=self.controller.journal_tail())
            handlers = {
                "verify": self.controller.verify,
                "restart-agent": self.controller.restart_agent,
                "restart-upstream-proxy": self.controller.restart_upstream_proxy,
                "restart-watchdog": self.controller.restart_watchdog,
                "redeploy": self.controller.redeploy_host,
            }
            return WorkerPayload(kind=kind, value=handlers[kind]())

        def action_restart_watchdog(self) -> None:
            self.start_task("restart-watchdog")

        def action_redeploy(self) -> None:
            self.start_task("redeploy")

        def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
            if event.state == WorkerState.ERROR:
                error = getattr(event.worker, "error", None) or getattr(event.worker, "exception", None) or "unknown error"
                self.busy_action = None
                self.last_result = ActionResult(False, "task failed", str(error))
                self.last_output = str(error)
                self.refresh_view()
                self.notify("Task failed", severity="error")
                return
            if event.state != WorkerState.SUCCESS:
                return
            payload = event.worker.result
            self.busy_action = None
            if isinstance(payload, WorkerPayload) and payload.kind == "logs":
                self.log_output = str(payload.value or "No logs available.").strip() or "No logs available."
                self.refresh_view()
                return
            if isinstance(payload, WorkerPayload) and isinstance(payload.value, ActionResult):
                result = payload.value
                self.last_result = result
                self.last_output = result.details or result.summary
                severity = "information" if result.ok else "error"
                self.refresh_view()
                self.notify(result.summary, severity=severity)
                self.start_task("logs")


def launch_manager(control_root: Path, instance_name: str | None = None) -> None:
    if TEXTUAL_AVAILABLE:
        app = TwomanManagerApp(control_root, instance_name=instance_name)
        app.run()
        if app.pending_command:
            subprocess.run(app.pending_command, check=False)
        return
    run_basic_manager(control_root, instance_name)
