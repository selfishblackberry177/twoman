<?php

require_once __DIR__ . '/bootstrap.php';

function bridge_runtime_dir()
{
    return dirname(__DIR__) . '/runtime';
}

function bridge_runtime_logs_dir()
{
    return bridge_runtime_dir() . '/logs';
}

function bridge_runtime_local_host()
{
    return '127.0.0.1';
}

function bridge_runtime_local_port()
{
    $config = relay_config();
    return isset($config['bridge_local_port']) ? (int) $config['bridge_local_port'] : 18093;
}

function bridge_runtime_socket_path()
{
    $config = relay_config();
    if (isset($config['bridge_local_socket_path']) && trim((string) $config['bridge_local_socket_path']) !== '') {
        return (string) $config['bridge_local_socket_path'];
    }
    return bridge_runtime_dir() . '/bridge.sock';
}

function bridge_runtime_use_unix_socket()
{
    $config = relay_config();
    return !empty($config['bridge_use_unix_socket']);
}

function bridge_runtime_daemon_script()
{
    return bridge_runtime_dir() . '/http_broker_daemon.py';
}

function bridge_runtime_daemon_pid_path()
{
    return bridge_runtime_dir() . '/bridge.pid';
}

function bridge_runtime_daemon_config_path()
{
    return bridge_runtime_dir() . '/bridge-config.json';
}

function bridge_runtime_max_lane_bytes()
{
    $config = relay_config();
    return isset($config['bridge_max_lane_bytes']) ? max(65536, (int) $config['bridge_max_lane_bytes']) : (16 * 1024 * 1024);
}

function bridge_runtime_is_process_alive($pid)
{
    $pid = (int) $pid;
    if ($pid < 2) {
        return false;
    }

    return function_exists('posix_kill') ? @posix_kill($pid, 0) : file_exists('/proc/' . $pid);
}

function bridge_runtime_read_pid($path)
{
    if (!file_exists($path)) {
        return 0;
    }

    $content = trim((string) @file_get_contents($path));
    return preg_match('/^[0-9]+$/', $content) ? (int) $content : 0;
}

function bridge_runtime_write_pid($path, $pid)
{
    @file_put_contents($path, (string) (int) $pid, LOCK_EX);
}

function bridge_runtime_kill_matching_processes()
{
    if (!function_exists('shell_exec')) {
        return;
    }
    $script = bridge_runtime_daemon_script();
    if (bridge_runtime_use_unix_socket()) {
        $selector = '--unix-socket ' . bridge_runtime_socket_path();
    } else {
        $selector = '--listen-port ' . (int) bridge_runtime_local_port();
    }
    $command = "ps -eo pid=,args= | grep " . escapeshellarg($script)
        . " | grep -- " . escapeshellarg($selector)
        . " | grep -v grep | awk '{print \$1}'";
    $output = trim((string) @shell_exec($command));
    if ($output === '') {
        return;
    }

    foreach (preg_split('/\s+/', $output) as $candidate) {
        if ($candidate === '' || !preg_match('/^[0-9]+$/', $candidate)) {
            continue;
        }
        bridge_runtime_stop_pid((int) $candidate);
    }
}

function bridge_runtime_spawn_command($command)
{
    if (function_exists('proc_open')) {
        $descriptors = [
            0 => ['pipe', 'r'],
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w'],
        ];
        $process = @proc_open(['/bin/sh', '-lc', $command], $descriptors, $pipes);
        if (is_resource($process)) {
            if (isset($pipes[0]) && is_resource($pipes[0])) {
                fclose($pipes[0]);
            }
            $stdout = isset($pipes[1]) && is_resource($pipes[1]) ? stream_get_contents($pipes[1]) : '';
            $stderr = isset($pipes[2]) && is_resource($pipes[2]) ? stream_get_contents($pipes[2]) : '';
            if (isset($pipes[1]) && is_resource($pipes[1])) {
                fclose($pipes[1]);
            }
            if (isset($pipes[2]) && is_resource($pipes[2])) {
                fclose($pipes[2]);
            }
            @proc_close($process);
            if (trim(strval($stderr)) !== '') {
                error_log(trim(strval($stderr)));
            }
            return trim(strval($stdout));
        }
    }
    if (function_exists('shell_exec')) {
        return trim(strval(@shell_exec($command)));
    }
    return '';
}

function bridge_runtime_stop_pid($pid)
{
    $pid = (int) $pid;
    if ($pid < 2) {
        return;
    }

    if (function_exists('posix_kill')) {
        @posix_kill($pid, 15);
        usleep(300000);
        if (bridge_runtime_is_process_alive($pid)) {
            @posix_kill($pid, 9);
        }
    } else {
        @shell_exec('kill ' . $pid . ' >/dev/null 2>&1 || true');
    }
}

function bridge_runtime_bootstrap_files()
{
    relay_ensure_dir(bridge_runtime_dir());
    relay_ensure_dir(bridge_runtime_logs_dir());
}

function bridge_runtime_write_config()
{
    $config = relay_config();
    $payload = [
        'backend_family' => isset($config['backend_family']) ? (string) $config['backend_family'] : 'bridge_runtime',
        'client_tokens' => isset($config['client_tokens']) ? array_values($config['client_tokens']) : [],
        'agent_tokens' => isset($config['agent_tokens']) ? array_values($config['agent_tokens']) : [],
        'session_ttl_seconds' => isset($config['bridge_session_ttl_seconds']) ? (int) $config['bridge_session_ttl_seconds'] : 300,
        'peer_ttl_seconds' => isset($config['bridge_max_agent_idle_seconds']) ? (int) $config['bridge_max_agent_idle_seconds'] : 90,
        'stream_ttl_seconds' => isset($config['bridge_session_ttl_seconds']) ? (int) $config['bridge_session_ttl_seconds'] : 300,
        'max_lane_bytes' => bridge_runtime_max_lane_bytes(),
        'max_streams_per_peer_session' => isset($config['bridge_max_streams_per_peer_session']) ? (int) $config['bridge_max_streams_per_peer_session'] : 256,
        'max_open_rate_per_peer_session' => isset($config['bridge_max_open_rate_per_peer_session']) ? (int) $config['bridge_max_open_rate_per_peer_session'] : 120,
        'open_rate_window_seconds' => isset($config['bridge_open_rate_window_seconds']) ? (int) $config['bridge_open_rate_window_seconds'] : 10,
        'max_peer_buffered_bytes' => isset($config['bridge_max_peer_buffered_bytes']) ? (int) $config['bridge_max_peer_buffered_bytes'] : min(bridge_runtime_max_lane_bytes() * 2, 32 * 1024 * 1024),
        'down_wait_ms' => isset($config['down_wait_ms']) && is_array($config['down_wait_ms']) ? $config['down_wait_ms'] : ['ctl' => 250, 'data' => 250],
        'down_wait_ms_by_role' => isset($config['down_wait_ms_by_role']) && is_array($config['down_wait_ms_by_role']) ? $config['down_wait_ms_by_role'] : [],
        'streaming_ctl_down_helper' => !empty($config['streaming_ctl_down_helper']),
        'streaming_data_down_helper' => !empty($config['streaming_data_down_helper']),
        'helper_down_combined_data_lane' => !empty($config['helper_down_combined_data_lane']),
        'streaming_ctl_down_agent' => !empty($config['streaming_ctl_down_agent']),
        'streaming_data_down_agent' => !empty($config['streaming_data_down_agent']),
        'agent_down_combined_data_lane' => !empty($config['agent_down_combined_data_lane']),
        'lane_profiles' => isset($config['lane_profiles']) && is_array($config['lane_profiles']) ? $config['lane_profiles'] : [],
        'base_uri' => isset($config['bridge_public_base_path']) ? (string) $config['bridge_public_base_path'] : '',
        'binary_media_type' => isset($config['bridge_binary_media_type']) ? (string) $config['bridge_binary_media_type'] : 'image/webp',
        'route_template' => isset($config['bridge_route_template']) ? (string) $config['bridge_route_template'] : '/{lane}/{direction}',
        'health_template' => isset($config['bridge_health_template']) ? (string) $config['bridge_health_template'] : '/health',
    ];

    $json = json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    if ($json === false) {
        relay_fail(500, 'Unable to encode bridge config');
    }

    if (@file_put_contents(bridge_runtime_daemon_config_path(), $json, LOCK_EX) === false) {
        relay_fail(500, 'Unable to write bridge config');
    }
}

function bridge_runtime_tcp_is_open($host, $port)
{
    $errno = 0;
    $errstr = '';
    $socket = @fsockopen($host, (int) $port, $errno, $errstr, 1.0);
    if (!is_resource($socket)) {
        return false;
    }

    fclose($socket);
    return true;
}

function bridge_runtime_socket_is_open($path)
{
    $socket = @stream_socket_client('unix://' . $path, $errno, $errstr, 1.0);
    if (!is_resource($socket)) {
        return false;
    }
    fclose($socket);
    return true;
}

function bridge_runtime_http_get($path, $timeoutSeconds)
{
    if (bridge_runtime_use_unix_socket()) {
        $socket = @stream_socket_client('unix://' . bridge_runtime_socket_path(), $errno, $errstr, $timeoutSeconds);
    } else {
        $errno = 0;
        $errstr = '';
        $socket = @fsockopen(
            bridge_runtime_local_host(),
            bridge_runtime_local_port(),
            $errno,
            $errstr,
            $timeoutSeconds
        );
    }
    if (!is_resource($socket)) {
        return false;
    }

    stream_set_timeout($socket, (int) ceil($timeoutSeconds));
    $request = "GET " . $path . " HTTP/1.1\r\n"
        . "Host: " . bridge_runtime_local_host() . "\r\n"
        . "Connection: close\r\n\r\n";
    fwrite($socket, $request);

    $response = '';
    while (!feof($socket)) {
        $chunk = fread($socket, 8192);
        if ($chunk === false) {
            fclose($socket);
            return false;
        }
        $response .= $chunk;
    }
    fclose($socket);

    $parts = explode("\r\n\r\n", $response, 2);
    return count($parts) === 2 ? $parts[1] : false;
}

function bridge_runtime_camouflage_html($statusCode)
{
    $candidatePaths = [];
    $explicitPath = strval(getenv('TWOMAN_CAMOUFLAGE_404_PATH') ?: '');
    if (trim($explicitPath) !== '') {
        $candidatePaths[] = $explicitPath;
    }
    $candidatePaths[] = bridge_runtime_logs_dir() . '/../camouflage_404.html';
    $candidatePaths[] = dirname(__DIR__) . '/runtime/camouflage_404.html';
    $candidatePaths[] = dirname(__DIR__) . '/camouflage_404.html';
    $candidatePaths[] = rtrim(strval(getenv('HOME') ?: ''), '/') . '/public_html/404.html';
    $seen = [];
    foreach ($candidatePaths as $candidatePath) {
        $normalizedPath = @realpath($candidatePath) ?: $candidatePath;
        if (isset($seen[$normalizedPath])) {
            continue;
        }
        $seen[$normalizedPath] = true;
        if (!is_file($candidatePath)) {
            continue;
        }
        $content = @file_get_contents($candidatePath);
        if ($content !== false && $content !== '') {
            return $content;
        }
    }

    $statusText = 'Error';
    if ($statusCode === 403) {
        $statusText = 'Forbidden';
    } elseif ($statusCode === 404) {
        $statusText = 'Not Found';
    } elseif ($statusCode === 405) {
        $statusText = 'Method Not Allowed';
    } elseif ($statusCode === 502) {
        $statusText = 'Bad Gateway';
    }

    return sprintf(
        '<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%1$d - %2$s</title><style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:Vazirmatn,Tahoma,sans-serif;background:linear-gradient(135deg,#0F2027,#203A43,#2C5364);color:#dfe6e9}.c{text-align:center;padding:60px 40px;max-width:500px}h1{font-size:72px;margin:0 0 10px;opacity:.15}p{font-size:18px;opacity:.7;line-height:1.8}a{color:#00b894;text-decoration:none}</style></head><body><div class="c"><h1>%1$d</h1><p>صفحه‌ای که به دنبال آن هستید در دسترس نیست یا دسترسی شما محدود شده است.</p><p><a href="/">بازگشت به صفحه اصلی</a></p></div></body></html>',
        $statusCode,
        $statusText
    );
}

function bridge_runtime_send_camouflage_response($statusCode)
{
    http_response_code((int) $statusCode);
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    echo bridge_runtime_camouflage_html((int) $statusCode);
}

function bridge_runtime_incoming_headers()
{
    if (function_exists('getallheaders')) {
        $headers = getallheaders();
        if (is_array($headers)) {
            return $headers;
        }
    }

    $headers = [];
    foreach ($_SERVER as $key => $value) {
        if (!is_string($key) || !is_string($value)) {
            continue;
        }
        if (strpos($key, 'HTTP_') === 0) {
            $name = str_replace(' ', '-', ucwords(strtolower(str_replace('_', ' ', substr($key, 5)))));
            $headers[$name] = $value;
        }
    }
    if (isset($_SERVER['CONTENT_TYPE'])) {
        $headers['Content-Type'] = strval($_SERVER['CONTENT_TYPE']);
    }
    if (isset($_SERVER['CONTENT_LENGTH'])) {
        $headers['Content-Length'] = strval($_SERVER['CONTENT_LENGTH']);
    }
    return $headers;
}

function bridge_runtime_request_target()
{
    $requestUri = strval($_SERVER['REQUEST_URI'] ?? '/');
    $path = parse_url($requestUri, PHP_URL_PATH);
    $path = is_string($path) && $path !== '' ? $path : '/';
    $query = parse_url($requestUri, PHP_URL_QUERY);
    $query = is_string($query) ? $query : '';

    $publicBasePath = relay_public_base_path();
    if ($publicBasePath !== '') {
        if ($path === $publicBasePath) {
            $path = '/';
        } elseif (strpos($path, $publicBasePath . '/') === 0) {
            $path = substr($path, strlen($publicBasePath));
        }
    }

    $path = '/' . ltrim($path, '/');
    $config = relay_config();
    $bridgeBasePath = trim(strval($config['bridge_public_base_path'] ?? ''));
    if ($bridgeBasePath !== '' && $bridgeBasePath !== '/') {
        $path = '/' . trim($bridgeBasePath, '/') . $path;
    }

    if ($query !== '') {
        $path .= '?' . $query;
    }
    return $path;
}

function bridge_runtime_open_proxy_stream($timeoutSeconds = 10.0)
{
    if (bridge_runtime_use_unix_socket()) {
        return @stream_socket_client('unix://' . bridge_runtime_socket_path(), $errno, $errstr, $timeoutSeconds);
    }
    return @stream_socket_client(
        'tcp://' . bridge_runtime_local_host() . ':' . bridge_runtime_local_port(),
        $errno,
        $errstr,
        $timeoutSeconds
    );
}

function bridge_runtime_proxy_connect()
{
    $socket = bridge_runtime_open_proxy_stream(2.0);
    if (is_resource($socket)) {
        return $socket;
    }
    bridge_runtime_ensure();
    $socket = bridge_runtime_open_proxy_stream(5.0);
    if (is_resource($socket)) {
        return $socket;
    }
    relay_fail(502, 'Unable to connect to bridge runtime');
}

function bridge_runtime_filtered_request_headers($body)
{
    $filtered = [];
    $hopByHop = [
        'connection' => true,
        'keep-alive' => true,
        'proxy-authenticate' => true,
        'proxy-authorization' => true,
        'proxy-connection' => true,
        'te' => true,
        'trailer' => true,
        'transfer-encoding' => true,
        'upgrade' => true,
        'host' => true,
        'content-length' => true,
    ];
    foreach (bridge_runtime_incoming_headers() as $name => $value) {
        $headerName = trim(strval($name));
        if ($headerName === '') {
            continue;
        }
        if (isset($hopByHop[strtolower($headerName)])) {
            continue;
        }
        $filtered[$headerName] = strval($value);
    }
    if ($body !== '') {
        $filtered['Content-Length'] = strval(strlen($body));
    } else {
        $filtered['Content-Length'] = '0';
    }
    $filtered['Host'] = bridge_runtime_local_host();
    $filtered['Connection'] = 'close';
    return $filtered;
}

function bridge_runtime_read_header_block($socket)
{
    $statusLine = fgets($socket);
    if ($statusLine === false) {
        relay_fail(502, 'Bridge runtime returned no response');
    }

    $headers = [];
    $headerMap = [];
    while (($line = fgets($socket)) !== false) {
        $trimmed = rtrim($line, "\r\n");
        if ($trimmed === '') {
            break;
        }
        $parts = explode(':', $trimmed, 2);
        if (count($parts) !== 2) {
            continue;
        }
        $name = trim($parts[0]);
        $value = trim($parts[1]);
        $headers[] = [$name, $value];
        $headerMap[strtolower($name)] = $value;
    }

    if (!preg_match('/^HTTP\/[0-9.]+\s+([0-9]{3})(?:\s+(.*))?$/i', trim($statusLine), $matches)) {
        relay_fail(502, 'Bridge runtime returned an invalid status line');
    }

    return [
        'status_code' => intval($matches[1]),
        'status_text' => isset($matches[2]) ? trim($matches[2]) : '',
        'headers' => $headers,
        'header_map' => $headerMap,
    ];
}

function bridge_runtime_prepare_stream_output()
{
    if (function_exists('apache_setenv')) {
        @apache_setenv('no-gzip', '1');
    }
    @ini_set('zlib.output_compression', '0');
    @ini_set('output_buffering', 'off');
    while (ob_get_level() > 0) {
        @ob_end_flush();
    }
    ob_implicit_flush(true);
}

function bridge_runtime_emit_response_headers($statusCode, $headers, $headerMap)
{
    http_response_code((int) $statusCode);
    $skipHeaders = [
        'connection' => true,
        'keep-alive' => true,
        'proxy-authenticate' => true,
        'proxy-authorization' => true,
        'proxy-connection' => true,
        'te' => true,
        'trailer' => true,
        'transfer-encoding' => true,
        'upgrade' => true,
    ];
    foreach ($headers as $pair) {
        $name = strval($pair[0] ?? '');
        $value = strval($pair[1] ?? '');
        if ($name === '' || isset($skipHeaders[strtolower($name)])) {
            continue;
        }
        header($name . ': ' . $value, false);
    }
    if (!isset($headerMap['cache-control'])) {
        header('Cache-Control: no-store');
    }
    header('X-Accel-Buffering: no');
}

function bridge_runtime_stream_chunked_body($socket)
{
    while (true) {
        $line = fgets($socket);
        if ($line === false) {
            break;
        }
        $sizeText = trim(explode(';', $line, 2)[0]);
        if ($sizeText === '') {
            continue;
        }
        $size = hexdec($sizeText);
        if ($size <= 0) {
            while (($trailer = fgets($socket)) !== false) {
                if (rtrim($trailer, "\r\n") === '') {
                    break;
                }
            }
            break;
        }
        $remaining = $size;
        while ($remaining > 0) {
            $chunk = fread($socket, min(8192, $remaining));
            if ($chunk === false || $chunk === '') {
                return;
            }
            echo $chunk;
            flush();
            $remaining -= strlen($chunk);
        }
        fread($socket, 2);
    }
}

function bridge_runtime_stream_sized_body($socket, $length)
{
    $remaining = max(0, intval($length));
    while ($remaining > 0) {
        $chunk = fread($socket, min(8192, $remaining));
        if ($chunk === false || $chunk === '') {
            break;
        }
        echo $chunk;
        flush();
        $remaining -= strlen($chunk);
    }
}

function bridge_runtime_stream_until_close($socket)
{
    while (!feof($socket)) {
        $chunk = fread($socket, 8192);
        if ($chunk === false || $chunk === '') {
            break;
        }
        echo $chunk;
        flush();
    }
}

function bridge_runtime_proxy_current_request()
{
    $method = strtoupper(strval($_SERVER['REQUEST_METHOD'] ?? 'GET'));
    $requestTarget = bridge_runtime_request_target();
    $body = file_get_contents('php://input');
    if ($body === false) {
        relay_fail(400, 'Unable to read request body');
    }
    $socket = bridge_runtime_proxy_connect();
    if (!is_resource($socket)) {
        bridge_runtime_send_camouflage_response(502);
        return;
    }

    stream_set_timeout($socket, 35);
    $requestLines = [$method . ' ' . $requestTarget . ' HTTP/1.1'];
    foreach (bridge_runtime_filtered_request_headers($body) as $name => $value) {
        $requestLines[] = $name . ': ' . $value;
    }
    $requestLines[] = '';
    $requestLines[] = '';
    fwrite($socket, implode("\r\n", $requestLines));
    if ($body !== '') {
        fwrite($socket, $body);
    }

    $response = bridge_runtime_read_header_block($socket);
    bridge_runtime_emit_response_headers($response['status_code'], $response['headers'], $response['header_map']);
    bridge_runtime_prepare_stream_output();
    $transferEncoding = strtolower(strval($response['header_map']['transfer-encoding'] ?? ''));
    if ($transferEncoding === 'chunked') {
        bridge_runtime_stream_chunked_body($socket);
    } elseif (isset($response['header_map']['content-length'])) {
        bridge_runtime_stream_sized_body($socket, $response['header_map']['content-length']);
    } else {
        bridge_runtime_stream_until_close($socket);
    }
    fclose($socket);
}

function bridge_runtime_ping()
{
    if (bridge_runtime_use_unix_socket()) {
        if (!bridge_runtime_socket_is_open(bridge_runtime_socket_path())) {
            return false;
        }
    } elseif (!bridge_runtime_tcp_is_open(bridge_runtime_local_host(), bridge_runtime_local_port())) {
        return false;
    }

    $body = bridge_runtime_http_get('/health', 2.0);
    if ($body === false || $body === '') {
        return false;
    }

    $decoded = json_decode($body, true);
    return is_array($decoded) && !empty($decoded['ok']);
}

function bridge_runtime_start()
{
    $script = bridge_runtime_daemon_script();
    if (!file_exists($script)) {
        relay_fail(500, 'Bridge runtime files are missing');
    }

    bridge_runtime_kill_matching_processes();
    bridge_runtime_write_config();
    $logPath = bridge_runtime_logs_dir() . '/bridge.log';
    $command = 'cd ' . escapeshellarg(bridge_runtime_dir())
        . ' && nohup /bin/python3 ' . escapeshellarg($script);
    if (bridge_runtime_use_unix_socket()) {
        @unlink(bridge_runtime_socket_path());
        $command .= ' --unix-socket ' . escapeshellarg(bridge_runtime_socket_path());
    } else {
        $command .= ' --listen-host ' . escapeshellarg(bridge_runtime_local_host())
            . ' --listen-port ' . (int) bridge_runtime_local_port();
    }
    $command .= ' --config ' . escapeshellarg(bridge_runtime_daemon_config_path())
        . ' >> ' . escapeshellarg($logPath) . ' 2>&1 < /dev/null & echo $!';
    $pid = bridge_runtime_spawn_command($command);
    if ($pid !== '' && preg_match('/^[0-9]+$/', $pid)) {
        bridge_runtime_write_pid(bridge_runtime_daemon_pid_path(), (int) $pid);
    }
}

function bridge_runtime_wait_until($callback, $timeoutMs)
{
    $deadline = microtime(true) + ($timeoutMs / 1000.0);
    while (microtime(true) < $deadline) {
        if ($callback()) {
            return true;
        }
        usleep(100000);
    }
    return false;
}

function bridge_runtime_ensure()
{
    bridge_runtime_bootstrap_files();
    bridge_runtime_write_config();
    if (bridge_runtime_ping()) {
        return;
    }

    $pid = bridge_runtime_read_pid(bridge_runtime_daemon_pid_path());
    if (bridge_runtime_is_process_alive($pid)) {
        bridge_runtime_stop_pid($pid);
    }
    bridge_runtime_kill_matching_processes();
    bridge_runtime_start();
    if (!bridge_runtime_wait_until(function () {
        return bridge_runtime_ping();
    }, 8000)) {
        relay_fail(502, 'Host bridge daemon did not start');
    }
}

function bridge_runtime_bridge_base_url()
{
    $config = relay_config();
    $bridgeBasePath = isset($config['bridge_public_base_path']) ? trim((string) $config['bridge_public_base_path']) : '';
    $publicBasePath = rtrim(relay_public_base_path(), '/');
    if ($bridgeBasePath === '') {
        return relay_current_origin() . ($publicBasePath !== '' ? $publicBasePath : '');
    }
    return relay_current_origin() . $publicBasePath . '/' . ltrim($bridgeBasePath, '/');
}
