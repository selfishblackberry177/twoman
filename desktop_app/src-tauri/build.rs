use std::env;

fn env_or_default(name: &str, default: &str) -> String {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default.to_string())
}

fn main() {
    for (name, default_value) in [
        ("TWOMAN_DESKTOP_DISPLAY_NAME", "Local Network Bridge"),
        ("TWOMAN_TUNNEL_INTERFACE_NAME", "Standard System Adapter"),
        ("TWOMAN_HELPER_BINARY_BASENAME", "local-network-helper"),
        ("TWOMAN_GATEWAY_BINARY_BASENAME", "local-network-bridge"),
        ("TWOMAN_TUNNEL_BINARY_BASENAME", "standard-system-adapter"),
    ] {
        println!("cargo:rerun-if-env-changed={name}");
        println!("cargo:rustc-env={name}={}", env_or_default(name, default_value));
    }
    tauri_build::build()
}
