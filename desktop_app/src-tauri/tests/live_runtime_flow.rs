use std::{
    env,
    fs,
    net::TcpListener,
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};

use desktop_app_lib::{
    models::{ClientProfile, ConnectionMode, ConnectionPhase, SharedProxy, SharedProxyProtocol},
    runtime::DesktopRuntime,
    storage::AppPaths,
};

fn reserve_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("failed to reserve ephemeral port")
        .local_addr()
        .expect("failed to read reserved port")
        .port()
}

fn temp_paths() -> AppPaths {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock drift")
        .as_millis();
    let root = env::temp_dir().join(format!("twoman-desktop-app-{nonce}"));
    let config_dir = root.join("config");
    let runtime_dir = root.join("runtime");
    let logs_dir = root.join("twoman-logs");
    for directory in [&config_dir, &runtime_dir, &logs_dir] {
        fs::create_dir_all(directory).expect("failed to create temp runtime dir");
    }
    AppPaths {
        profiles_file: config_dir.join("profiles.json"),
        shares_file: config_dir.join("shares.json"),
        settings_file: config_dir.join("settings.json"),
        config_dir,
        runtime_dir,
        logs_dir,
    }
}

fn live_profile() -> Option<ClientProfile> {
    let broker_base_url = env::var("TWOMAN_E2E_BROKER_BASE_URL").ok()?;
    let client_token = env::var("TWOMAN_E2E_CLIENT_TOKEN").ok()?;
    Some(ClientProfile {
        id: "live-profile".into(),
        name: "Live".into(),
        broker_base_url,
        client_token,
        verify_tls: env::var("TWOMAN_E2E_VERIFY_TLS")
            .ok()
            .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
            .unwrap_or(false),
        http2_ctl: true,
        http2_data: false,
        http_port: reserve_port(),
        socks_port: reserve_port(),
        http_timeout_seconds: 30,
        flush_delay_seconds: 0.01,
        max_batch_bytes: 65536,
        data_upload_max_batch_bytes: 65536,
        data_upload_flush_delay_seconds: 0.004,
        idle_repoll_ctl_seconds: 0.05,
        idle_repoll_data_seconds: 0.1,
        trace_enabled: false,
    })
}

fn curl_via_proxy(proxy: &str) -> String {
    for url in ["https://api.ipify.org", "http://api.ipify.org"] {
        let output = Command::new("curl")
            .args(["--max-time", "45", "--proxy", proxy, url])
            .output()
            .expect("failed to run curl");
        if output.status.success() {
            let result = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !result.is_empty() {
                return result;
            }
        }
    }
    panic!("curl failed for all probe urls via {proxy}");
}

#[test]
#[ignore = "requires live Twoman broker credentials"]
fn live_connect_share_disconnect_flow() {
    let Some(profile) = live_profile() else {
        eprintln!(
            "skipping live flow; set TWOMAN_E2E_BROKER_BASE_URL and TWOMAN_E2E_CLIENT_TOKEN"
        );
        return;
    };

    let paths = temp_paths();
    let temp_root = paths
        .config_dir
        .parent()
        .expect("temp config dir should have a parent")
        .to_path_buf();
    let runtime = DesktopRuntime::new(paths.clone(), None);
    runtime.save_profile(profile.clone()).expect("save_profile failed");
    runtime
        .set_selected_profile(Some(profile.id.clone()))
        .expect("set_selected_profile failed");
    runtime
        .set_mode(ConnectionMode::Proxy)
        .expect("set_mode failed");
    runtime.connect().expect("connect failed");

    let snapshot = runtime.snapshot().expect("snapshot failed after connect");
    assert_eq!(snapshot.connection.phase, ConnectionPhase::Connected);
    let active_http_port = snapshot
        .connection
        .http_port
        .expect("http port should be present while connected");
    let active_socks_port = snapshot
        .connection
        .socks_port
        .expect("socks port should be present while connected");

    let direct_ip = curl_via_proxy(&format!("socks5h://127.0.0.1:{active_socks_port}"));
    assert!(!direct_ip.is_empty(), "direct socks returned empty output");

    let share = SharedProxy {
        id: "share-socks".into(),
        name: "Share SOCKS".into(),
        protocol: SharedProxyProtocol::Socks,
        listen_host: "0.0.0.0".into(),
        listen_port: reserve_port(),
        username: "share-user".into(),
        password: "share-pass".into(),
    };
    runtime.save_share(share.clone()).expect("save_share failed");
    runtime.start_share(&share.id).expect("start_share failed");

    let share_snapshot = runtime.snapshot().expect("snapshot failed after share start");
    let share_status = share_snapshot
        .share_statuses
        .iter()
        .find(|entry| entry.share_id == share.id)
        .expect("share status should exist after start");
    assert!(share_status.running, "share should report running");
    assert_eq!(share_status.message, "Sharing");
    assert!(!share_status.addresses.is_empty(), "share should expose reachable addresses");

    let shared_ip = curl_via_proxy(&format!(
        "socks5h://{}:{}@127.0.0.1:{}",
        share.username, share.password, share.listen_port
    ));
    assert_eq!(direct_ip, shared_ip, "shared socks should exit through same IP");

    let http_share = SharedProxy {
        id: "share-http".into(),
        name: "Share HTTP".into(),
        protocol: SharedProxyProtocol::Http,
        listen_host: "0.0.0.0".into(),
        listen_port: reserve_port(),
        username: "http-user".into(),
        password: "http-pass".into(),
    };
    runtime.save_share(http_share.clone()).expect("save_share http failed");
    runtime
        .start_share(&http_share.id)
        .expect("start_share http failed");

    let shared_http_ip = curl_via_proxy(&format!(
        "http://{}:{}@127.0.0.1:{}",
        http_share.username, http_share.password, http_share.listen_port
    ));
    assert_eq!(direct_ip, shared_http_ip, "shared http proxy should exit through same IP");

    runtime.stop_share(&share.id).expect("stop_share failed");
    runtime
        .stop_share(&http_share.id)
        .expect("stop_share http failed");
    let stopped_snapshot = runtime.snapshot().expect("snapshot failed after share stop");
    let stopped_status = stopped_snapshot
        .share_statuses
        .iter()
        .find(|entry| entry.share_id == share.id)
        .expect("share status should exist after stop");
    assert!(!stopped_status.running, "share should report stopped");
    let stopped_http_status = stopped_snapshot
        .share_statuses
        .iter()
        .find(|entry| entry.share_id == http_share.id)
        .expect("http share status should exist after stop");
    assert!(!stopped_http_status.running, "http share should report stopped");
    runtime.disconnect().expect("disconnect failed");

    let final_snapshot = runtime.snapshot().expect("final snapshot failed");
    assert_eq!(final_snapshot.connection.phase, ConnectionPhase::Disconnected);
    TcpListener::bind(("127.0.0.1", active_http_port))
        .expect("http port should be free after disconnect");
    TcpListener::bind(("127.0.0.1", active_socks_port))
        .expect("socks port should be free after disconnect");

    runtime.connect().expect("reconnect failed");
    let reconnect_snapshot = runtime.snapshot().expect("reconnect snapshot failed");
    assert_eq!(reconnect_snapshot.connection.phase, ConnectionPhase::Connected);
    runtime.disconnect().expect("final disconnect failed");

    let _ = fs::remove_dir_all(temp_root);
}
