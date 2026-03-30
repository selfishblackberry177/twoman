# Twoman Desktop App

`desktop_app/` is the new desktop client shell for Twoman.

Stack:
- Tauri 2
- React
- shadcn/ui

What it does:
- saved Twoman profiles
- profile import/export text compatible with the Android client
- `Proxy` mode for local SOCKS + HTTP listeners
- `System proxy` mode on Windows, implemented as Windows system proxy management
- authenticated public SOCKS and HTTP proxies that forward into the local Twoman helper
- copyable helper/share logs inside the app

Important scope:
- this app is the user-facing desktop shell
- the Twoman tunnel logic still lives in the Python helper and SOCKS gateway
- release builds bundle those runtimes as sidecars

## Running from Source

Requirements:
- Node.js 22+
- Rust toolchain
- Python 3 for source-mode helper execution

Install dependencies:

```bash
cd desktop_app
npm install
```

Run the GUI in development:

```bash
npm run tauri dev
```

The app starts with no profiles. Add one profile in the UI or import share text.

## Windows Config Fields

For the current live method, the user-facing fields are:
- `Broker URL`
- `Client token`
- `Verify TLS`
- `HTTP/2 control`
- `HTTP/2 data`
- `HTTP port`
- `SOCKS port`

`System proxy` mode on Windows means:
- start the Twoman local helper
- point the current user’s Windows system HTTP/HTTPS proxy settings at the helper’s local HTTP port

It is not a kernel VPN.

## Sidecar Builds

Build Linux sidecars:

```bash
cd desktop_app
./scripts/build_sidecars_linux.sh
```

Build Windows sidecars from Windows Python:

```bat
cd desktop_app
scripts\build_sidecars_windows.bat
```

The sidecars are written to:
- `src-tauri/resources/sidecars/linux/`
- `src-tauri/resources/sidecars/windows/`

## Portable Windows Build

Portable Windows distribution should be shipped as a folder or zip, not just the installer.

Portable layout:
- `Twoman.exe`
- `sidecars/windows/twoman-helper.exe`
- `sidecars/windows/twoman-gateway.exe`
- `portable-data/`
- `twoman-portable`

Portable mode only activates when one of these exists beside `Twoman.exe`:
- `portable-data/`
- `twoman-portable`

When portable mode is active, the app keeps:
- profiles
- shared proxy entries
- logs
- runtime files

inside that folder instead of the normal Windows app-data directories.

Portable data paths:
- `portable-data/config/profiles.json`
- `portable-data/config/shares.json`
- `portable-data/config/settings.json`
- `portable-data/runtime/helper.json`
- `portable-data/twoman-logs/helper.log`

Package the portable handoff after a Windows build:

```bash
python3 desktop_app/scripts/package_windows_portable.py
```

That script:
- copies the fresh Windows `Twoman.exe`
- copies the current Windows sidecars
- writes `portable-data/README.txt`
- writes the `twoman-portable` marker
- rebuilds `private_handoff/desktop_app/windows/Twoman_0.1.0_x64-portable.zip`

Regenerate the Windows icon set after changing `src/assets/logo.png`:

```bash
python3 desktop_app/scripts/generate_windows_icons.py
```

That script emits exact small Windows icon layers so the taskbar/titlebar does
not depend on one blurred downscale from a large raster.

## Packaging

Linux bundle:

```bash
cd desktop_app
npm run tauri build
```

The app looks for bundled sidecars first. If no bundled sidecar exists, development builds fall back to the repo’s Python source runtime.

## Validation

Backend runtime validation:

```bash
cd desktop_app/src-tauri
TWOMAN_E2E_BROKER_BASE_URL='https://<public-host>/<path>' \
TWOMAN_E2E_CLIENT_TOKEN='<client-token>' \
cargo test live_connect_share_disconnect_flow -- --ignored --nocapture
```

That test covers:
- connect
- local SOCKS egress
- authenticated shared SOCKS egress
- authenticated shared HTTP proxy egress
- disconnect

## Learning Notes

The desktop app is intentionally split into:
- a modern GUI shell
- a separately managed tunnel runtime

That keeps the UI replaceable without rewriting the helper protocol path each time.

## Why This Matters

The previous Windows client kept failing because UI lifecycle and process lifecycle were tangled together inside one Python desktop shell. This Tauri app keeps the connection state machine in one place and treats helper/share processes as managed runtime components, which is the right production pattern for a desktop network client.
