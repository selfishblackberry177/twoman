from __future__ import annotations

import argparse
import os
from pathlib import Path

from twoman_control.installer import install
from twoman_control.registry import load_registry, resolve_instance_name, set_default_instance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="twoman")
    parser.add_argument("--instance", dest="global_instance", default="")
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="Run the Twoman deployment wizard")
    install_parser.add_argument("--instance", default="")
    install_parser.add_argument("--repo-root", type=Path)
    install_parser.add_argument("--control-root", type=Path, default=Path("/opt/twoman/control"))
    install_parser.add_argument("--install-root", type=Path, default=None)
    install_parser.add_argument("--public-origin", default="")
    install_parser.add_argument("--cpanel-base-url", default="")
    install_parser.add_argument("--cpanel-username", default="")
    install_parser.add_argument("--cpanel-password", default="")
    install_parser.add_argument("--cpanel-home", default="")
    install_parser.add_argument("--site-name", default="")
    install_parser.add_argument("--backend", default="")
    install_parser.add_argument("--public-base-path", default="")
    install_parser.add_argument("--bridge-public-base-path", default="")
    install_parser.add_argument("--passenger-app-name", default="")
    install_parser.add_argument("--passenger-app-root", default="")
    install_parser.add_argument("--node-app-root", default="")
    install_parser.add_argument("--node-app-uri", default="")
    install_parser.add_argument("--admin-script-name", default="")
    install_parser.add_argument("--hidden-service-name", default="")
    install_parser.add_argument("--hidden-service-user", default="")
    install_parser.add_argument("--hidden-service-group", default="")
    install_parser.add_argument("--watchdog-service-name", default="")
    install_parser.add_argument("--watchdog-timer-name", default="")
    install_parser.add_argument("--hidden-upstream-proxy-url", default="")
    install_parser.add_argument("--hidden-upstream-proxy-label", default="")
    install_parser.add_argument("--hidden-outbound-proxy-url", default="")
    install_parser.add_argument("--hidden-outbound-proxy-label", default="")
    install_parser.add_argument("--non-interactive", action="store_true")
    install_parser.add_argument("--customize", action="store_true")
    install_parser.add_argument("--skip-helper-probe", action="store_true")
    tls_group = install_parser.add_mutually_exclusive_group()
    tls_group.add_argument("--verify-tls", dest="verify_tls", action="store_true")
    tls_group.add_argument("--no-verify-tls", dest="verify_tls", action="store_false")
    install_parser.set_defaults(verify_tls=None)

    for name, help_text in [
        ("verify", "Run a non-interactive health check"),
        ("logs", "Print the hidden-agent journal tail"),
        ("show-config", "Print the Twoman client import text"),
        ("restart-agent", "Restart the hidden-agent service"),
        ("restart-upstream-proxy", "Restart the managed hidden-server route proxy"),
        ("run-watchdog", "Run the watchdog service immediately"),
        ("redeploy-host", "Redeploy the public host backend with the saved state"),
    ]:
        action_parser = subparsers.add_parser(name, help=help_text)
        action_parser.add_argument("--instance", default="")
    list_parser = subparsers.add_parser("list", help="List installed Twoman instances")
    list_parser.add_argument("--instance", default="")
    default_parser = subparsers.add_parser("set-default", help="Set the default Twoman instance")
    default_parser.add_argument("instance_name")
    return parser


def _control_root() -> Path:
    return Path(os.environ.get("TWOMAN_CONTROL_ROOT", "/opt/twoman/control"))


def _selected_instance(args: argparse.Namespace) -> str:
    return str(getattr(args, "instance", "") or getattr(args, "global_instance", "")).strip()


def _run_action(controller: ManagerController, command: str) -> int:
    if command == "verify":
        result = controller.verify()
    elif command == "logs":
        print(controller.journal_tail())
        return 0
    elif command == "show-config":
        print(controller.state.profile_share_text)
        return 0
    elif command == "restart-agent":
        result = controller.restart_agent()
    elif command == "restart-upstream-proxy":
        result = controller.restart_upstream_proxy()
    elif command == "run-watchdog":
        result = controller.restart_watchdog()
    elif command == "redeploy-host":
        result = controller.redeploy_host()
    else:
        raise ValueError(f"unknown command: {command}")
    print(result.details or result.summary)
    return 0 if result.ok else 1


def _run_list(control_root: Path) -> int:
    registry = load_registry(control_root)
    if not registry.instances:
        print("No Twoman instances are installed.")
        return 0
    for instance in registry.instances:
        marker = "*" if instance.name == registry.default_instance else " "
        print(
            f"{marker} {instance.name}\t{instance.backend}\t{instance.broker_base_url}\t"
            f"{instance.hidden_service_name}\t{instance.hidden_install_root}"
        )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "tui"
    if command == "install":
        install(args)
        return 0
    from twoman_control.manager import ManagerController, launch_manager

    control_root = _control_root()
    instance_name = _selected_instance(args)
    if command == "list":
        return _run_list(control_root)
    if command == "set-default":
        set_default_instance(control_root, args.instance_name)
        print(f"default instance set to {resolve_instance_name(control_root, args.instance_name)}")
        return 0
    if command != "tui":
        controller = ManagerController(control_root, instance_name or None)
        return _run_action(controller, command)
    launch_manager(control_root, instance_name or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
