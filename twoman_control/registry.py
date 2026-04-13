from __future__ import annotations

import shutil
from pathlib import Path

from twoman_control.models import InstallState, InstanceRegistry, ManagedInstance


STATE_FILENAME = "install-state.json"
PROFILE_SHARE_FILENAME = "profile-share.txt"
REGISTRY_FILENAME = "instances.json"
INSTANCES_DIRNAME = "instances"
DEFAULT_INSTANCE_NAME = "default"


def normalize_instance_name(name: str) -> str:
    text = str(name or DEFAULT_INSTANCE_NAME).strip().lower()
    safe = "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in text)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or DEFAULT_INSTANCE_NAME


def registry_path(control_root: Path) -> Path:
    return control_root / REGISTRY_FILENAME


def instances_root(control_root: Path) -> Path:
    return control_root / INSTANCES_DIRNAME


def instance_root(control_root: Path, instance_name: str) -> Path:
    return instances_root(control_root) / normalize_instance_name(instance_name)


def state_path(control_root: Path, instance_name: str) -> Path:
    return instance_root(control_root, instance_name) / STATE_FILENAME


def profile_share_path(control_root: Path, instance_name: str) -> Path:
    return instance_root(control_root, instance_name) / PROFILE_SHARE_FILENAME


def legacy_state_path(control_root: Path) -> Path:
    return control_root / STATE_FILENAME


def legacy_profile_share_path(control_root: Path) -> Path:
    return control_root / PROFILE_SHARE_FILENAME


def managed_instance_from_state(control_root: Path, state: InstallState) -> ManagedInstance:
    name = normalize_instance_name(state.instance_name)
    return ManagedInstance(
        name=name,
        root=str(instance_root(control_root, name)),
        backend=state.backend,
        broker_base_url=state.broker_base_url,
        public_origin=state.public_origin,
        public_base_path=state.public_base_path,
        hidden_install_root=state.hidden_install_root,
        hidden_service_name=state.hidden_service_name,
        client_profile_name=state.client_profile_name,
        site_name=state.site_name,
    )


def _build_registry_from_instance_dirs(control_root: Path) -> InstanceRegistry:
    registry = InstanceRegistry()
    for candidate in sorted(instances_root(control_root).glob(f"*/{STATE_FILENAME}")):
        state = InstallState.load(candidate)
        state.instance_name = normalize_instance_name(state.instance_name)
        registry.upsert(managed_instance_from_state(control_root, state))
    if registry.instances and not registry.default_instance:
        registry.default_instance = registry.instances[0].name
    return registry


def _migrate_legacy_state(control_root: Path) -> None:
    legacy = legacy_state_path(control_root)
    if not legacy.exists():
        return
    state = InstallState.load(legacy)
    instance_name = normalize_instance_name(state.instance_name)
    state.instance_name = instance_name
    migrated_state_path = state_path(control_root, instance_name)
    if not migrated_state_path.exists():
        state.save(migrated_state_path)
    legacy_profile = legacy_profile_share_path(control_root)
    migrated_profile = profile_share_path(control_root, instance_name)
    if legacy_profile.exists() and not migrated_profile.exists():
        migrated_profile.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_profile, migrated_profile)
        migrated_profile.chmod(0o600)
    registry = InstanceRegistry(default_instance=instance_name)
    registry.upsert(managed_instance_from_state(control_root, state))
    registry.save(registry_path(control_root))


def load_registry(control_root: Path) -> InstanceRegistry:
    control_root.mkdir(parents=True, exist_ok=True)
    registry_file = registry_path(control_root)
    if registry_file.exists():
        return InstanceRegistry.load(registry_file)
    if legacy_state_path(control_root).exists():
        _migrate_legacy_state(control_root)
        return InstanceRegistry.load(registry_file)
    registry = _build_registry_from_instance_dirs(control_root)
    if registry.instances:
        registry.save(registry_file)
    return registry


def resolve_instance_name(control_root: Path, instance_name: str | None, *, allow_missing: bool = False) -> str:
    registry = load_registry(control_root)
    if instance_name:
        resolved = normalize_instance_name(instance_name)
        if allow_missing or registry.get(resolved) is not None:
            return resolved
        raise KeyError(f"unknown Twoman instance: {resolved}")
    if registry.default_instance:
        return normalize_instance_name(registry.default_instance)
    if allow_missing:
        return DEFAULT_INSTANCE_NAME
    raise KeyError("no Twoman instances have been installed yet")


def load_instance_state(control_root: Path, instance_name: str | None = None) -> InstallState:
    resolved = resolve_instance_name(control_root, instance_name)
    return InstallState.load(state_path(control_root, resolved))


def save_instance_state(control_root: Path, state: InstallState) -> None:
    instance_name = normalize_instance_name(state.instance_name)
    state.instance_name = instance_name
    state.save(state_path(control_root, instance_name))
    profile_path = profile_share_path(control_root, instance_name)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(f"{state.profile_share_text}\n", encoding="utf-8")
    profile_path.chmod(0o600)
    registry = load_registry(control_root)
    registry.upsert(managed_instance_from_state(control_root, state))
    if not registry.default_instance:
        registry.default_instance = instance_name
    registry.save(registry_path(control_root))


def set_default_instance(control_root: Path, instance_name: str) -> None:
    resolved = resolve_instance_name(control_root, instance_name)
    registry = load_registry(control_root)
    registry.default_instance = resolved
    registry.save(registry_path(control_root))


def remove_instance(control_root: Path, instance_name: str) -> None:
    resolved = resolve_instance_name(control_root, instance_name)
    registry = load_registry(control_root)
    registry.instances = [instance for instance in registry.instances if instance.name != resolved]
    if registry.default_instance == resolved:
        registry.default_instance = registry.instances[0].name if registry.instances else ""
    registry.save(registry_path(control_root))
