<?php

return [
    'storage_path' => __DIR__ . '/../storage',
    'public_base_path' => '/rahkar',
    'offload_relative_path' => 'offload',
    'offload_ttl_seconds' => 3600,
    'client_tokens' => [
        'replace-with-client-token',
    ],
    'agent_tokens' => [
        'replace-with-agent-token',
    ],
    'reverse_keys' => [
        'replace-with-reverse-key',
    ],
    'max_request_body_bytes' => 8 * 1024 * 1024,
    'poll_wait_ms' => 20000,
    'reverse_wait_ms' => 45000,
    'poll_sleep_us' => 200000,
    'job_lease_seconds' => 30,
    'bridge_local_port' => 18093,
    'bridge_use_unix_socket' => false,
    'bridge_local_socket_path' => __DIR__ . '/../runtime/bridge.sock',
    'bridge_public_base_path' => '/api/v1/telemetry',
    'bridge_route_template' => '/{lane}/{direction}',
    'bridge_health_template' => '/health',
    'bridge_binary_media_type' => 'image/webp',
    'bridge_session_ttl_seconds' => 300,
    'bridge_max_agent_idle_seconds' => 90,
    'bridge_max_streams_per_peer_session' => 256,
    'bridge_max_open_rate_per_peer_session' => 120,
    'bridge_open_rate_window_seconds' => 10,
    'bridge_max_peer_buffered_bytes' => 32 * 1024 * 1024,
];
