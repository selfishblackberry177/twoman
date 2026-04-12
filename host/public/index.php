<?php

$candidatePaths = [
    __DIR__ . '/../app/bridge_runtime.php',
    __DIR__ . '/app/bridge_runtime.php',
];

foreach ($candidatePaths as $candidatePath) {
    if (file_exists($candidatePath)) {
        require_once $candidatePath;
        break;
    }
}

if (!function_exists('bridge_runtime_proxy_current_request')) {
    http_response_code(500);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Missing bridge runtime bootstrap']);
    exit;
}

try {
    bridge_runtime_proxy_current_request();
} catch (Throwable $error) {
    bridge_runtime_send_camouflage_response(502);
}
