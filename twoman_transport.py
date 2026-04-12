#!/usr/bin/env python3

import asyncio
import collections
import contextlib
import ipaddress
import os
import random
import ssl
import sys
import urllib.parse

import httpx
try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - compatibility for older distro packages
    from websockets import connect as ws_connect

from twoman_http import (
    RouteProvider,
    build_connection_headers,
    expected_binary_media_type,
    httpx_proxy_kwargs,
    is_json_media_type,
    jittered_backoff_seconds,
    jittered_interval_seconds,
    validate_binary_media_type,
    validate_json_media_type,
)
from twoman_protocol import (
    FLAG_DATA_BULK,
    FRAME_DATA,
    FRAME_PING,
    Frame,
    FrameDecoder,
    LANES,
    LANE_BULK,
    LANE_CTL,
    LANE_DATA,
    LANE_PRI,
    encode_frame,
)
from twoman_crypto import TransportCipher

TRACE_ENABLED = os.environ.get("TWOMAN_TRACE", "").strip().lower() in ("1", "true", "yes", "on", "debug", "verbose")


def trace(message):
    if not TRACE_ENABLED:
        return
    sys.stderr.write("[transport] %s\n" % message)
    sys.stderr.flush()


PROFILE_SHARED_HOST_SAFE = "shared_host_safe"
PROFILE_MANAGED_HOST_HTTP = "managed_host_http"
PROFILE_MANAGED_HOST_WS = "managed_host_ws"
DEFAULT_TRANSPORT_PROFILE = "auto"
CAPABILITY_VERSION = 1


def _normalize_upstream_proxy_url(value):
    proxy_url = str(value or "").strip()
    if not proxy_url:
        return None
    parsed = urllib.parse.urlsplit(proxy_url)
    if parsed.scheme != "socks5":
        return proxy_url
    hostname = (parsed.hostname or "").strip()
    is_loopback = hostname == "localhost"
    if not is_loopback and hostname:
        with contextlib.suppress(ValueError):
            is_loopback = ipaddress.ip_address(hostname).is_loopback
    if not is_loopback:
        return proxy_url
    return urllib.parse.urlunsplit(("socks5h", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _deep_merge_dict(base, override):
    if not isinstance(base, dict):
        return dict(override or {}) if isinstance(override, dict) else override
    merged = dict(base)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _transport_profile_library():
    return {
        PROFILE_SHARED_HOST_SAFE: {
            "transport": "http",
            "helper": {
                "http2_enabled": {"ctl": False, "data": False},
                "down_lanes": ["data"],
                "up_request_timeout_seconds": {"ctl": 5.0, "data": 20.0},
                "upload_profiles": {
                    "data": {"max_batch_bytes": 65536, "flush_delay_seconds": 0.004},
                },
                "idle_repoll_delay_seconds": {"ctl": 0.05, "data": 0.10},
                "streaming_up_lanes": [],
            },
            "agent": {
                "http2_enabled": {"ctl": False, "data": False},
                "down_lanes": ["data"],
                "down_parallelism": {"data": 2},
                "proxy_keepalive_connections": 2,
                "proxy_keepalive_expiry_seconds": 15.0,
                "up_request_timeout_seconds": {"ctl": 5.0, "data": 20.0},
                "upload_profiles": {
                    "data": {"max_batch_bytes": 131072, "flush_delay_seconds": 0.006},
                },
                "down_read_timeout_seconds": 20.0,
                "idle_repoll_delay_seconds": {"ctl": 0.05, "data": 0.10},
                "streaming_up_lanes": [],
            },
        },
        PROFILE_MANAGED_HOST_HTTP: {
            "transport": "http",
            "helper": {
                "http2_enabled": {"ctl": True, "data": False},
                "down_lanes": ["data"],
                "down_parallelism": {"data": 2},
                "upload_profiles": {
                    "data": {"max_batch_bytes": 65536, "flush_delay_seconds": 0.004},
                },
                "idle_repoll_delay_seconds": {"ctl": 0.05, "data": 0.10},
                "streaming_up_lanes": [],
            },
            "agent": {
                "http2_enabled": {"ctl": False, "data": False},
                "down_lanes": ["data"],
                "proxy_keepalive_connections": 2,
                "proxy_keepalive_expiry_seconds": 15.0,
                "upload_profiles": {
                    "data": {"max_batch_bytes": 131072, "flush_delay_seconds": 0.006},
                },
                "idle_repoll_delay_seconds": {"ctl": 0.05, "data": 0.10},
                "streaming_up_lanes": [],
                "stream_control_lane": "pri",
            },
        },
        PROFILE_MANAGED_HOST_WS: {
            "transport": "ws",
            "helper": {
                "streaming_up_lanes": [],
            },
            "agent": {
                "streaming_up_lanes": [],
            },
        },
    }


def _profile_from_explicit_transport(config):
    transport_kind = str(config.get("transport", "http")).strip().lower()
    if transport_kind == "ws":
        return PROFILE_MANAGED_HOST_WS
    return ""


def _extract_health_stats(payload):
    if not isinstance(payload, dict):
        return {}
    stats = payload.get("stats")
    if isinstance(stats, dict):
        return stats
    return payload


def _extract_transport_capabilities(payload):
    stats = _extract_health_stats(payload)
    capabilities = stats.get("capabilities", {})
    if isinstance(capabilities, dict):
        return capabilities
    return {}


def _default_capabilities_for_backend(backend_family):
    backend = str(backend_family or "").strip().lower()
    if backend == "node_selector":
        return {
            "version": CAPABILITY_VERSION,
            "backend_family": "node_selector",
            "recommended_profile": PROFILE_MANAGED_HOST_HTTP,
            "supported_profiles": [PROFILE_MANAGED_HOST_HTTP, PROFILE_MANAGED_HOST_WS],
            "profiles": {},
        }
    if backend in ("passenger_python", "bridge_runtime"):
        return {
            "version": CAPABILITY_VERSION,
            "backend_family": backend,
            "recommended_profile": PROFILE_SHARED_HOST_SAFE,
            "supported_profiles": [PROFILE_SHARED_HOST_SAFE],
            "profiles": {},
        }
    return {
        "version": CAPABILITY_VERSION,
        "backend_family": backend or "unknown",
        "recommended_profile": PROFILE_SHARED_HOST_SAFE,
        "supported_profiles": [PROFILE_SHARED_HOST_SAFE],
        "profiles": {},
    }


def _role_profile_overrides(profile_name, role, capabilities):
    profile_library = _transport_profile_library()
    resolved = dict(profile_library.get(profile_name, {}).get(role, {}))
    capability_profiles = capabilities.get("profiles", {}) if isinstance(capabilities, dict) else {}
    capability_profile = capability_profiles.get(profile_name, {}) if isinstance(capability_profiles, dict) else {}
    if isinstance(capability_profile, dict):
        resolved = _deep_merge_dict(resolved, capability_profile.get(role, {}))
    return resolved


def _transport_for_profile(profile_name, capabilities):
    profile_library = _transport_profile_library()
    transport = profile_library.get(profile_name, {}).get("transport", "http")
    capability_profiles = capabilities.get("profiles", {}) if isinstance(capabilities, dict) else {}
    capability_profile = capability_profiles.get(profile_name, {}) if isinstance(capability_profiles, dict) else {}
    if isinstance(capability_profile, dict):
        transport = str(capability_profile.get("transport", transport)).strip().lower() or transport
    return transport


def _apply_transport_profile(config, role, capabilities, profile_name):
    resolved = dict(config)
    resolved["selected_transport_profile"] = profile_name
    resolved["transport"] = _transport_for_profile(profile_name, capabilities)
    for key, value in _role_profile_overrides(profile_name, role, capabilities).items():
        if isinstance(value, dict) and isinstance(resolved.get(key), dict):
            resolved[key] = _deep_merge_dict(value, resolved.get("%s_overrides" % key, {}))
        else:
            resolved[key] = value
    return resolved


def _profile_candidates(config, capabilities):
    requested_profile = str(config.get("transport_profile", "")).strip().lower()
    if requested_profile and requested_profile != DEFAULT_TRANSPORT_PROFILE:
        return [requested_profile]
    explicit_profile = _profile_from_explicit_transport(config)
    if explicit_profile:
        return [explicit_profile]
    defaults = _default_capabilities_for_backend(
        capabilities.get("backend_family", config.get("backend_family", "")) if isinstance(capabilities, dict) else ""
    )
    supported_profiles = list(capabilities.get("supported_profiles", defaults["supported_profiles"])) if isinstance(capabilities, dict) else list(defaults["supported_profiles"])
    recommended_profile = (
        str(capabilities.get("recommended_profile", defaults["recommended_profile"])).strip().lower()
        if isinstance(capabilities, dict)
        else defaults["recommended_profile"]
    )
    candidates = []
    if (
        PROFILE_MANAGED_HOST_WS in supported_profiles
        and bool(config.get("allow_websocket_transport", True))
    ):
        candidates.append(PROFILE_MANAGED_HOST_WS)
    if recommended_profile:
        candidates.append(recommended_profile)
    candidates.extend(supported_profiles)
    deduped = []
    for profile_name in candidates:
        if profile_name and profile_name not in deduped:
            deduped.append(profile_name)
    return deduped or [PROFILE_SHARED_HOST_SAFE]


def _protocol_config_from_config(config):
    return {
        "auth_mode": config.get("auth_mode", "bearer"),
        "legacy_custom_headers_enabled": config.get("legacy_custom_headers_enabled", True),
        "binary_media_type": config.get("binary_media_type", "image/webp"),
        "binary_media_types": config.get("binary_media_types", []),
        "route_template": config.get("route_template", "/{lane}/{direction}"),
        "ws_route_template": config.get("ws_route_template", "/{lane}"),
        "health_template": config.get("health_template", "/health"),
        "route_context": config.get("route_context", {}),
        "version": config.get("version", config.get("api_version", "")),
        "tenant": config.get("tenant", config.get("tenant_id", "")),
        "endpoint": config.get("endpoint", config.get("endpoint_id", "")),
        "identity_cookie_names": config.get("identity_cookie_names", {}),
        "backoff_initial_delay_seconds": config.get("backoff_initial_delay_seconds", 0.1),
        "backoff_max_delay_seconds": config.get("backoff_max_delay_seconds", 5.0),
        "backoff_multiplier": config.get("backoff_multiplier", 2.0),
        "backoff_free_failures": config.get("backoff_free_failures", 1),
        "heartbeat_interval_seconds": config.get("heartbeat_interval_seconds", 15.0),
        "down_read_timeout_seconds": config.get("down_read_timeout_seconds", 10.0),
        "down_stream_max_session_seconds": config.get("down_stream_max_session_seconds", 60.0),
        "interval_jitter_ratio": config.get("interval_jitter_ratio", 0.2),
        "proxy_keepalive_connections": config.get("proxy_keepalive_connections", None),
        "proxy_keepalive_expiry_seconds": config.get("proxy_keepalive_expiry_seconds", None),
        "ws_ping_interval_seconds": config.get("ws_ping_interval_seconds", 20.0),
        "ws_ping_timeout_seconds": config.get("ws_ping_timeout_seconds", 20.0),
    }


def _transport_common_args(config, role, peer_id, on_frame):
    return {
        "base_url": config["broker_base_url"],
        "token": config["client_token"] if role == "helper" else config["agent_token"],
        "role": role,
        "peer_id": peer_id,
        "on_frame": on_frame,
        "http_timeout_seconds": config.get("http_timeout_seconds", 60),
        "max_batch_bytes": config.get("max_batch_bytes", 65536),
        "flush_delay_seconds": config.get("flush_delay_seconds", 0.01),
        "verify_tls": config.get("verify_tls", True),
        "http2_enabled": config.get("http2_enabled", True),
        "collapse_data_lanes": True,
        "upload_profiles": config.get("upload_profiles", {}),
        "streaming_up_lanes": config.get("streaming_up_lanes", []),
        "down_lanes": config.get("down_lanes", []),
        "idle_repoll_delay_seconds": config.get("idle_repoll_delay_seconds", {}),
        "down_parallelism": config.get("down_parallelism", {}),
        "up_request_timeout_seconds": config.get("up_request_timeout_seconds", {}),
        "stream_control_lane": config.get("stream_control_lane", LANE_CTL),
        "upstream_proxy_url": str(config.get("upstream_proxy_url", "")).strip() or None,
        "protocol_config": _protocol_config_from_config(config),
    }


def _instantiate_transport(config, role, peer_id, on_frame):
    transport_kind = str(config.get("transport", "http")).strip().lower()
    common_args = _transport_common_args(config, role, peer_id, on_frame)
    if transport_kind == "ws":
        return WebSocketLaneTransport(**common_args)
    return LaneTransport(**common_args)


def _normalize_stream_control_lane_value(stream_control_lane):
    lane = str(stream_control_lane or LANE_CTL).strip().lower()
    if lane in (LANE_CTL, LANE_PRI, LANE_BULK):
        return lane
    return LANE_CTL


class LaneTransport(object):
    def __init__(
        self,
        base_url,
        token,
        role,
        peer_id,
        on_frame,
        http_timeout_seconds=60,
        flush_delay_seconds=0.01,
        max_batch_bytes=65536,
        verify_tls=True,
        http2_enabled=True,
        collapse_data_lanes=False,
        upload_profiles=None,
        streaming_up_lanes=None,
        down_lanes=None,
        idle_repoll_delay_seconds=None,
        down_parallelism=None,
        up_request_timeout_seconds=None,
        stream_control_lane=LANE_CTL,
        protocol_config=None,
        upstream_proxy_url=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.role = role
        self.peer_label = peer_id
        self.peer_session_id = os.urandom(8).hex()
        self.on_frame = on_frame
        self.http_timeout_seconds = float(http_timeout_seconds)
        self.flush_delay_seconds = float(flush_delay_seconds)
        self.max_batch_bytes = int(max_batch_bytes)
        self.verify_tls = verify_tls
        self.collapse_data_lanes = bool(collapse_data_lanes)
        self.http2_enabled_default = False if isinstance(http2_enabled, dict) else bool(http2_enabled)
        self.http2_enabled_lanes = self._normalize_http2_enabled(http2_enabled)
        default_upload_profiles = {
            "ctl": {"max_batch_bytes": 4096, "flush_delay_seconds": 0.0},
            "pri": {"max_batch_bytes": 4096, "flush_delay_seconds": min(self.flush_delay_seconds, 0.001)},
            "bulk": {"max_batch_bytes": min(self.max_batch_bytes, 32768), "flush_delay_seconds": max(self.flush_delay_seconds, 0.008)},
            "data": self._default_data_upload_profile(),
        }
        self.upload_profiles = self._merge_upload_profiles(default_upload_profiles, upload_profiles or {})
        self.streaming_up_lanes = self._normalize_streaming_up_lanes(streaming_up_lanes)
        self.protocol_config = dict(protocol_config or {})
        self.down_lanes = self._normalize_down_lanes(down_lanes)
        self.idle_repoll_delay_seconds = self._normalize_idle_repoll_delay_seconds(idle_repoll_delay_seconds)
        self.upstream_proxy_url = _normalize_upstream_proxy_url(upstream_proxy_url)
        self.down_parallelism = self._normalize_down_parallelism(down_parallelism)
        self.up_request_timeout_seconds = self._normalize_up_request_timeout_seconds(up_request_timeout_seconds)
        self.stream_control_lane = self._normalize_stream_control_lane(stream_control_lane)
        self.down_read_timeout_seconds = self._normalize_down_read_timeout_seconds()
        self.down_stream_max_session_seconds = self._normalize_down_stream_max_session_seconds()
        self.route_provider = RouteProvider.from_config(self.base_url, self.protocol_config)
        self.random = random.Random()
        self.queues = dict((lane, asyncio.Queue()) for lane in LANES)
        self.data_queue = asyncio.Queue() if self.collapse_data_lanes else None
        self.replay_queues = dict((lane, collections.deque()) for lane in self._external_lanes())
        self.stop_event = asyncio.Event()
        self.clients = {}
        self.tasks = []
        self.failure_counts = {}
        self.event_handler = None

    async def start(self):
        if self.clients:
            return
        for lane in self._external_lanes():
            self.clients[(lane, "up")] = self._build_client(lane, "up")
        for lane in self.down_lanes:
            for worker_index in range(self.down_parallelism.get(lane, 1)):
                self.clients[self._down_client_key(lane, worker_index)] = self._build_client(lane, "down")
        for lane in self._external_lanes():
            if lane in self.streaming_up_lanes:
                self.tasks.append(asyncio.create_task(self._streaming_up_loop(lane)))
            else:
                self.tasks.append(asyncio.create_task(self._up_loop(lane)))
        for lane in self.down_lanes:
            for worker_index in range(self.down_parallelism.get(lane, 1)):
                self.tasks.append(asyncio.create_task(self._down_loop(lane, worker_index)))
        self.tasks.append(asyncio.create_task(self._ping_loop()))
        self._report_event(
            "transport_start",
            base_url=self.base_url,
            collapse_data_lanes=self.collapse_data_lanes,
            http2_enabled=self.http2_enabled_lanes,
            streaming_up_lanes=self.streaming_up_lanes,
            down_lanes=sorted(self.down_lanes),
            down_parallelism=self.down_parallelism,
        )

    async def stop(self):
        self.stop_event.set()
        self._report_event("transport_stop")
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks = []
        for client in self.clients.values():
            with contextlib.suppress(Exception):
                await client.aclose()
        self.clients = {}

    async def send_frame(self, lane, frame):
        if self.collapse_data_lanes and lane in ("pri", "bulk"):
            if frame.type_id == FRAME_DATA:
                flags = int(frame.flags)
                if lane == "bulk":
                    flags |= FLAG_DATA_BULK
                else:
                    flags &= ~FLAG_DATA_BULK
                frame = Frame(
                    frame.type_id,
                    stream_id=frame.stream_id,
                    offset=frame.offset,
                    payload=frame.payload,
                    flags=flags,
                )
            await self.data_queue.put(frame)
            return
        if lane not in self.queues:
            raise ValueError("unknown lane")
        await self.queues[lane].put(frame)

    async def _up_loop(self, lane):
        while not self.stop_event.is_set():
            batch_frames = []
            try:
                first = await self._next_outbound_frame(lane)
                logical_lane = lane
                profile = self.upload_profiles.get(
                    logical_lane,
                    {"max_batch_bytes": self.max_batch_bytes, "flush_delay_seconds": self.flush_delay_seconds},
                )
                batch_frames = [first]
                batch = [encode_frame(first)]
                total = len(batch[0])
                max_batch_bytes = int(profile["max_batch_bytes"])
                flush_delay_seconds = float(profile["flush_delay_seconds"])
                deadline = asyncio.get_running_loop().time() + flush_delay_seconds
                while total < max_batch_bytes:
                    if flush_delay_seconds <= 0:
                        try:
                            frame = self._next_outbound_frame_nowait(lane)
                        except asyncio.QueueEmpty:
                            break
                        encoded = encode_frame(frame)
                        if total + len(encoded) > max_batch_bytes:
                            await self._requeue_frame(lane, frame)
                            break
                        batch_frames.append(frame)
                        batch.append(encoded)
                        total += len(encoded)
                        continue
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        frame = await asyncio.wait_for(self._next_outbound_frame(lane), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    encoded = encode_frame(frame)
                    if total + len(encoded) > max_batch_bytes:
                        await self._requeue_frame(lane, frame)
                        break
                    batch_frames.append(frame)
                    batch.append(encoded)
                    total += len(encoded)
                
                # Encrypt outer payload with hop-by-hop cipher
                batch_payload = b"".join(batch)
                iv = os.urandom(16)
                cipher = TransportCipher(self.token.encode('utf-8'), iv)
                encrypted_payload = iv + cipher.process(batch_payload)

                response = await self.clients[(lane, "up")].post(
                    self._lane_url(lane, "up"),
                    headers=self._headers(binary_request=True),
                    content=encrypted_payload,
                    timeout=self.up_request_timeout_seconds.get(logical_lane, self.http_timeout_seconds),
                )
                response.raise_for_status()
                self._validate_ack_response(response)
                self._mark_success("up", lane)
                self._report_event(
                    "transport_up_ok",
                    lane=lane,
                    batch_bytes=total,
                    batch_frames=len(batch_frames),
                    status_code=response.status_code,
                )
                trace("%s/%s@%s up ok lane=%s batch_bytes=%s status=%s" % (self.role, self.peer_label, self.peer_session_id, lane, total, response.status_code))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                err_str = repr(error)
                if hasattr(error, 'response') and error.response is not None:
                    err_str += " BODY: " + error.response.text
                await self._requeue_frames_front(lane, batch_frames)
                await self._reset_client(lane, "up")
                delay = self._backoff_after_error("up", lane)
                self._report_event(
                    "transport_up_error",
                    lane=lane,
                    delay_seconds=delay,
                    error=err_str,
                    queued_frames=len(batch_frames),
                )
                trace("%s/%s@%s up error lane=%s delay=%0.3f error=%s" % (self.role, self.peer_label, self.peer_session_id, lane, delay, err_str))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _down_loop(self, lane, worker_index=0):
        while not self.stop_event.is_set():
            decoder = FrameDecoder()
            saw_non_ping = False
            rotated = False
            frame_count = 0
            non_ping_frames = 0
            payload_bytes = 0
            status_code = 0
            try:
                async with self.clients[self._down_client_key(lane, worker_index)].stream(
                    "GET",
                    self._lane_url(lane, "down"),
                    headers=self._headers(),
                ) as response:
                    response.raise_for_status()
                    status_code = int(response.status_code)
                    self._mark_success("down", lane)
                    if not self._validate_binary_response(response):
                        self._report_event(
                            "transport_down_response",
                            lane=lane,
                            worker_index=worker_index,
                            status_code=status_code,
                            frame_count=0,
                            non_ping_frames=0,
                            payload_bytes=0,
                            rotated=False,
                        )
                        delay = self.idle_repoll_delay_seconds.get(lane, 0.0)
                        if delay > 0:
                            await asyncio.sleep(self._jittered_interval(delay))
                        continue
                    trace("%s/%s@%s down open lane=%s status=%s" % (self.role, self.peer_label, self.peer_session_id, lane, response.status_code))
                    opened_at = asyncio.get_running_loop().time()
                    cipher = None
                    iv_buffer = b""
                    iterator = response.aiter_bytes().__aiter__()
                    while True:
                        if self.down_stream_max_session_seconds > 0:
                            age = asyncio.get_running_loop().time() - opened_at
                            if age >= self.down_stream_max_session_seconds:
                                rotated = True
                                self._report_event(
                                    "transport_down_rotate",
                                    lane=lane,
                                    age_seconds=age,
                                )
                                trace(
                                    "%s/%s@%s down rotate lane=%s age=%0.3f"
                                    % (self.role, self.peer_label, self.peer_session_id, lane, age)
                                )
                                break
                        try:
                            chunk = await self._next_response_chunk(iterator, lane)
                        except StopAsyncIteration:
                            break
                        if not chunk:
                            continue

                        # Extract the 16-byte IV from the front of the stream
                        if cipher is None:
                            needed_iv = 16 - len(iv_buffer)
                            iv_buffer += chunk[:needed_iv]
                            chunk = chunk[needed_iv:]
                            if len(iv_buffer) == 16:
                                cipher = TransportCipher(self.token.encode('utf-8'), iv_buffer)
                            if not chunk:
                                continue

                        trace("%s/%s@%s down chunk lane=%s bytes=%s" % (self.role, self.peer_label, self.peer_session_id, lane, len(chunk)))

                        plaintext_chunk = cipher.process(chunk)

                        for frame in decoder.feed(plaintext_chunk):
                            frame_count += 1
                            payload_bytes += len(frame.payload)
                            if frame.type_id != FRAME_PING:
                                saw_non_ping = True
                                non_ping_frames += 1
                                trace("%s/%s@%s down frame lane=%s type=%s stream=%s payload=%s" % (self.role, self.peer_label, self.peer_session_id, lane, frame.type_id, frame.stream_id, len(frame.payload)))
                            await self.on_frame(frame, lane)
                self._report_event(
                    "transport_down_response",
                    lane=lane,
                    worker_index=worker_index,
                    status_code=status_code,
                    frame_count=frame_count,
                    non_ping_frames=non_ping_frames,
                    payload_bytes=payload_bytes,
                    rotated=rotated,
                )
                if rotated:
                    continue
                if not saw_non_ping:
                    delay = self.idle_repoll_delay_seconds.get(lane, 0.0)
                    if delay > 0:
                        await asyncio.sleep(self._jittered_interval(delay))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._reset_client(lane, "down", worker_index)
                delay = self._backoff_after_error("down", lane)
                self._report_event(
                    "transport_down_error",
                    lane=lane,
                    worker_index=worker_index,
                    delay_seconds=delay,
                    error=repr(error),
                )
                trace("%s/%s@%s down error lane=%s delay=%0.3f error=%r" % (self.role, self.peer_label, self.peer_session_id, lane, delay, error))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _streaming_up_loop(self, lane):
        while not self.stop_event.is_set():
            try:
                response = await self.clients[(lane, "up")].post(
                    self._lane_url(lane, "up"),
                    headers=self._headers(binary_request=True),
                    content=self._streaming_upload_chunks(lane),
                    timeout=self.up_request_timeout_seconds.get(lane, self.http_timeout_seconds),
                )
                response.raise_for_status()
                self._validate_ack_response(response)
                self._mark_success("up_stream", lane)
                trace("%s/%s@%s up stream closed lane=%s status=%s" % (self.role, self.peer_label, self.peer_session_id, lane, response.status_code))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                delay = self._backoff_after_error("up_stream", lane)
                self._report_event(
                    "transport_up_stream_error",
                    lane=lane,
                    delay_seconds=delay,
                    error=repr(error),
                )
                trace("%s/%s@%s up stream error lane=%s delay=%0.3f error=%r" % (self.role, self.peer_label, self.peer_session_id, lane, delay, error))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _ping_loop(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(self._jittered_interval(self.protocol_config.get("heartbeat_interval_seconds", 15.0)))
            await self.send_frame(LANE_CTL, Frame(FRAME_PING, offset=int(asyncio.get_running_loop().time() * 1000)))

    def _headers(self, binary_request=False):
        headers = build_connection_headers(
            self.token,
            self.role,
            self.peer_label,
            self.peer_session_id,
            self.protocol_config,
        )
        if binary_request:
            headers["Content-Type"] = expected_binary_media_type(self.protocol_config)
        return headers

    def _lane_url(self, lane, direction):
        return self.route_provider.lane_url(lane, direction)

    def _external_lanes(self):
        if self.collapse_data_lanes:
            return (LANE_CTL, LANE_DATA)
        return LANES

    def _normalize_http2_enabled(self, http2_enabled):
        external_lanes = self._external_lanes()
        if isinstance(http2_enabled, dict):
            values = dict((str(key), bool(value)) for key, value in http2_enabled.items())
            lanes = dict((lane, False) for lane in external_lanes)
            for lane in external_lanes:
                if lane in values:
                    lanes[lane] = values[lane]
            if self.collapse_data_lanes and LANE_DATA not in values:
                lanes[LANE_DATA] = bool(values.get("pri", False) or values.get("bulk", False))
            return lanes
        default_value = bool(http2_enabled)
        return dict((lane, default_value) for lane in external_lanes)

    def _normalize_streaming_up_lanes(self, streaming_up_lanes):
        if not streaming_up_lanes:
            return set()
        return set(str(value) for value in streaming_up_lanes if str(value) in self._external_lanes())

    def _normalize_down_lanes(self, down_lanes):
        external_lanes = self._external_lanes()
        if not down_lanes:
            return set(external_lanes)
        normalized = set()
        for value in down_lanes:
            lane = str(value).strip()
            if lane in external_lanes:
                normalized.add(lane)
        return normalized or set(external_lanes)

    def _normalize_down_parallelism(self, down_parallelism):
        external_lanes = self._external_lanes()
        values = dict((lane, 1) for lane in external_lanes)
        explicit_lanes = set()
        scalar_explicit = False
        if isinstance(down_parallelism, dict):
            for lane in external_lanes:
                if lane not in down_parallelism:
                    continue
                try:
                    values[lane] = max(1, int(down_parallelism[lane]))
                    explicit_lanes.add(lane)
                except (TypeError, ValueError):
                    continue
        elif down_parallelism is not None:
            try:
                parallelism = max(1, int(down_parallelism))
            except (TypeError, ValueError):
                parallelism = 1
            for lane in external_lanes:
                values[lane] = parallelism
            scalar_explicit = True
        if (
            self.role == "agent"
            and self.upstream_proxy_url
            and LANE_DATA in values
            and LANE_DATA not in explicit_lanes
            and not scalar_explicit
        ):
            values[LANE_DATA] = max(values[LANE_DATA], 2)
        return values

    def _normalize_idle_repoll_delay_seconds(self, idle_repoll_delay_seconds):
        external_lanes = self._external_lanes()
        if isinstance(idle_repoll_delay_seconds, dict):
            values = dict((str(key), float(value)) for key, value in idle_repoll_delay_seconds.items())
            delays = dict((lane, 0.0) for lane in external_lanes)
            for lane in external_lanes:
                if lane in values:
                    delays[lane] = max(0.0, values[lane])
            if self.collapse_data_lanes and LANE_DATA not in values:
                delays[LANE_DATA] = max(0.0, float(values.get("pri", 0.0) or values.get("bulk", 0.0)))
            return delays
        if idle_repoll_delay_seconds is None:
            configured = self.protocol_config.get("idle_repoll_delay_seconds")
            if isinstance(configured, dict):
                return self._normalize_idle_repoll_delay_seconds(configured)
            if configured is not None:
                try:
                    default_value = max(0.0, float(configured))
                except (TypeError, ValueError):
                    default_value = 0.05 if LANE_CTL in external_lanes else 0.1
                return dict((lane, default_value) for lane in external_lanes)
            defaults = {}
            for lane in external_lanes:
                defaults[lane] = 0.05 if lane == LANE_CTL else 0.1
            return defaults
        default_value = max(0.0, float(idle_repoll_delay_seconds))
        return dict((lane, default_value) for lane in external_lanes)

    def _normalize_up_request_timeout_seconds(self, up_request_timeout_seconds):
        external_lanes = self._external_lanes()
        defaults = dict((lane, float(self.http_timeout_seconds)) for lane in external_lanes)
        if isinstance(up_request_timeout_seconds, dict):
            values = dict((str(key), value) for key, value in up_request_timeout_seconds.items())
            for lane in external_lanes:
                if lane not in values:
                    continue
                try:
                    defaults[lane] = max(0.1, float(values[lane]))
                except (TypeError, ValueError):
                    continue
            if self.collapse_data_lanes and LANE_DATA not in values:
                for source_lane in ("pri", "bulk"):
                    if source_lane not in values:
                        continue
                    try:
                        defaults[LANE_DATA] = max(0.1, float(values[source_lane]))
                    except (TypeError, ValueError):
                        pass
                    break
            return defaults
        if up_request_timeout_seconds is None:
            return defaults
        try:
            timeout = max(0.1, float(up_request_timeout_seconds))
        except (TypeError, ValueError):
            timeout = float(self.http_timeout_seconds)
        return dict((lane, timeout) for lane in external_lanes)

    def _normalize_stream_control_lane(self, stream_control_lane):
        return _normalize_stream_control_lane_value(stream_control_lane)

    def _normalize_down_read_timeout_seconds(self):
        configured = self.protocol_config.get("down_read_timeout_seconds", 10.0)
        try:
            return max(0.01, float(configured))
        except (TypeError, ValueError):
            return 10.0

    def _normalize_down_stream_max_session_seconds(self):
        configured = self.protocol_config.get("down_stream_max_session_seconds", 60.0)
        try:
            return max(0.0, float(configured))
        except (TypeError, ValueError):
            return 60.0

    def _default_data_upload_profile(self):
        if self.role == "agent":
            return {
                "max_batch_bytes": min(max(self.max_batch_bytes, 65536), 131072),
                "flush_delay_seconds": max(self.flush_delay_seconds, 0.005),
            }
        return {
            "max_batch_bytes": min(max(self.max_batch_bytes // 2, 16384), 32768),
            "flush_delay_seconds": max(min(self.flush_delay_seconds, 0.002), 0.001),
        }

    def _merge_upload_profiles(self, defaults, overrides):
        merged = dict((lane, dict(profile)) for lane, profile in defaults.items())
        for lane, profile in (overrides or {}).items():
            lane_key = str(lane)
            if lane_key not in merged or not isinstance(profile, dict):
                continue
            if "max_batch_bytes" in profile:
                merged[lane_key]["max_batch_bytes"] = int(profile["max_batch_bytes"])
            if "flush_delay_seconds" in profile:
                merged[lane_key]["flush_delay_seconds"] = float(profile["flush_delay_seconds"])
        return merged

    def _mark_success(self, direction, lane):
        self.failure_counts[(direction, lane)] = 0

    def _backoff_after_error(self, direction, lane):
        key = (direction, lane)
        failures = self.failure_counts.get(key, 0) + 1
        self.failure_counts[key] = failures
        return jittered_backoff_seconds(
            failures,
            initial_delay=float(self.protocol_config.get("backoff_initial_delay_seconds", 0.1)),
            maximum_delay=float(self.protocol_config.get("backoff_max_delay_seconds", 5.0)),
            multiplier=float(self.protocol_config.get("backoff_multiplier", 2.0)),
            free_failures=int(self.protocol_config.get("backoff_free_failures", 1)),
            rng=self.random,
        )

    async def _next_outbound_frame(self, lane):
        replay = self.replay_queues.get(lane)
        if replay:
            return replay.popleft()
        if lane != LANE_DATA:
            return await self.queues[lane].get()
        return await self.data_queue.get()

    def _next_outbound_frame_nowait(self, lane):
        replay = self.replay_queues.get(lane)
        if replay:
            return replay.popleft()
        if lane != LANE_DATA:
            return self.queues[lane].get_nowait()
        return self.data_queue.get_nowait()

    async def _requeue_frame(self, lane, frame):
        # Preserve lane order when a batch overflows its byte budget.
        replay = self.replay_queues.setdefault(lane, collections.deque())
        replay.appendleft(frame)

    async def _requeue_frames_front(self, lane, frames):
        if not frames:
            return
        replay = self.replay_queues.setdefault(lane, collections.deque())
        for frame in reversed(frames):
            replay.appendleft(frame)

    def _down_client_key(self, lane, worker_index):
        if worker_index == 0:
            return (lane, "down")
        return (lane, "down", worker_index)

    async def _reset_client(self, lane, direction, worker_index=0):
        key = (lane, direction) if direction != "down" else self._down_client_key(lane, worker_index)
        client = self.clients.get(key)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()
        self.clients[key] = self._build_client(lane, direction)

    async def _next_response_chunk(self, iterator, lane):
        if self.down_read_timeout_seconds <= 0:
            return await iterator.__anext__()
        next_task = asyncio.create_task(iterator.__anext__())
        done, _pending = await asyncio.wait({next_task}, timeout=self.down_read_timeout_seconds)
        if done:
            return await next_task
        next_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
            await next_task
        raise TimeoutError(
            "down stream chunk timeout lane=%s timeout=%s"
            % (lane, self.down_read_timeout_seconds)
        )

    async def _streaming_upload_chunks(self, lane):
        logical_lane = lane
        profile = self.upload_profiles.get(
            logical_lane,
            {"max_batch_bytes": self.max_batch_bytes, "flush_delay_seconds": self.flush_delay_seconds},
        )
        max_batch_bytes = int(profile["max_batch_bytes"])
        flush_delay_seconds = float(profile["flush_delay_seconds"])
        while not self.stop_event.is_set():
            first = await self._next_outbound_frame(lane)
            batch = [encode_frame(first)]
            total = len(batch[0])
            deadline = asyncio.get_running_loop().time() + flush_delay_seconds
            while total < max_batch_bytes:
                if flush_delay_seconds <= 0:
                    try:
                        frame = self._next_outbound_frame_nowait(lane)
                    except asyncio.QueueEmpty:
                        break
                    encoded = encode_frame(frame)
                    if total + len(encoded) > max_batch_bytes:
                        await self._requeue_frame(lane, frame)
                        break
                    batch.append(encoded)
                    total += len(encoded)
                    continue
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    frame = await asyncio.wait_for(self._next_outbound_frame(lane), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                encoded = encode_frame(frame)
                if total + len(encoded) > max_batch_bytes:
                    await self._requeue_frame(lane, frame)
                    break
                batch.append(encoded)
                total += len(encoded)
                
            batch_payload = b"".join(batch)
            if not getattr(self, '_streaming_cipher', None) or getattr(self, '_streaming_lane', None) != lane:
                iv = os.urandom(16)
                self._streaming_cipher = TransportCipher(self.token.encode('utf-8'), iv)
                self._streaming_lane = lane
                yield iv + self._streaming_cipher.process(batch_payload)
            else:
                yield self._streaming_cipher.process(batch_payload)

    def _build_client(self, lane, direction):
        max_keepalive_connections = 20
        keepalive_expiry = 120
        if self.upstream_proxy_url:
            # Hidden routes behind WARP benefit from a short-lived keepalive
            # pool. Disabling reuse entirely makes every bounded poll pay a
            # fresh proxy + TLS setup cost, which causes long OPEN latency on
            # managed-host HTTP profiles.
            configured_keepalive_connections = self.protocol_config.get("proxy_keepalive_connections")
            configured_keepalive_expiry = self.protocol_config.get("proxy_keepalive_expiry_seconds")
            try:
                max_keepalive_connections = max(0, int(configured_keepalive_connections))
            except (TypeError, ValueError):
                max_keepalive_connections = 2
            try:
                keepalive_expiry = max(0.0, float(configured_keepalive_expiry))
            except (TypeError, ValueError):
                keepalive_expiry = 15.0
        limits = httpx.Limits(
            max_keepalive_connections=max_keepalive_connections,
            max_connections=50,
            keepalive_expiry=keepalive_expiry,
        )
        read_timeout = None
        if direction == "down" and self.down_read_timeout_seconds > 0:
            read_timeout = self.down_read_timeout_seconds
        timeout = httpx.Timeout(
            None,
            connect=self.http_timeout_seconds,
            read=read_timeout,
            write=self.http_timeout_seconds,
            pool=self.http_timeout_seconds,
        )
        kwargs = {
            "http2": self.http2_enabled_lanes.get(lane, self.http2_enabled_default),
            "timeout": timeout,
            "limits": limits,
            "verify": self.verify_tls,
        }
        kwargs.update(httpx_proxy_kwargs(self.upstream_proxy_url, async_client=True))
        return httpx.AsyncClient(**kwargs)

    def _validate_ack_response(self, response):
        validate_json_media_type(response.headers.get("content-type", ""))
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ValueError("unexpected transport ack payload")
        return payload

    def _validate_binary_response(self, response):
        if int(response.status_code) == 204:
            return False
        content_length = str(response.headers.get("content-length", "")).strip()
        if content_length == "0":
            return False
        content_type = response.headers.get("content-type", "")
        if is_json_media_type(content_type):
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else payload
            raise ValueError("binary response returned JSON: %s" % error)
        validate_binary_media_type(content_type, self.protocol_config)
        return True

    def _jittered_interval(self, base_delay):
        return jittered_interval_seconds(
            base_delay,
            jitter_ratio=float(self.protocol_config.get("interval_jitter_ratio", 0.2)),
            rng=self.random,
        )

    def _report_event(self, kind, **fields):
        if self.event_handler is None:
            return
        payload = {
            "kind": kind,
            "role": self.role,
            "peer_label": self.peer_label,
            "peer_session_id": self.peer_session_id,
        }
        payload.update(fields)
        try:
            self.event_handler(payload)
        except Exception:
            pass


class AsyncFrameQueue(object):
    def __init__(self):
        self._items = collections.deque()
        self._condition = asyncio.Condition()

    async def put(self, item):
        async with self._condition:
            self._items.append(item)
            self._condition.notify()

    async def putleft(self, item):
        async with self._condition:
            self._items.appendleft(item)
            self._condition.notify()

    async def get(self):
        async with self._condition:
            while not self._items:
                await self._condition.wait()
            return self._items.popleft()


class WebSocketLaneTransport(object):
    def __init__(
        self,
        base_url,
        token,
        role,
        peer_id,
        on_frame,
        http_timeout_seconds=60,
        flush_delay_seconds=0.01,
        max_batch_bytes=65536,
        verify_tls=True,
        http2_enabled=True,
        collapse_data_lanes=False,
        upload_profiles=None,
        streaming_up_lanes=None,
        down_lanes=None,
        idle_repoll_delay_seconds=None,
        down_parallelism=None,
        up_request_timeout_seconds=None,
        stream_control_lane=LANE_CTL,
        protocol_config=None,
        upstream_proxy_url=None,
    ):
        del http2_enabled
        del upload_profiles
        del streaming_up_lanes
        del down_lanes
        del idle_repoll_delay_seconds
        del down_parallelism
        del up_request_timeout_seconds
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.role = role
        self.peer_label = peer_id
        self.peer_session_id = os.urandom(8).hex()
        self.on_frame = on_frame
        self.http_timeout_seconds = float(http_timeout_seconds)
        self.flush_delay_seconds = float(flush_delay_seconds)
        self.max_batch_bytes = int(max_batch_bytes)
        self.verify_tls = bool(verify_tls)
        self.collapse_data_lanes = bool(collapse_data_lanes)
        self.stream_control_lane = _normalize_stream_control_lane_value(stream_control_lane)
        self.protocol_config = dict(protocol_config or {})
        self.upstream_proxy_url = _normalize_upstream_proxy_url(upstream_proxy_url)
        self.route_provider = RouteProvider.from_config(self.base_url, self.protocol_config)
        self.random = random.Random()
        self.queues = dict((lane, AsyncFrameQueue()) for lane in LANES)
        self.data_queue = AsyncFrameQueue() if self.collapse_data_lanes else None
        self.stop_event = asyncio.Event()
        self.tasks = []
        self.failure_counts = {}
        self.event_handler = None

    async def start(self):
        if self.tasks:
            return
        for lane in self._external_lanes():
            self.tasks.append(asyncio.create_task(self._lane_loop(lane)))
        self.tasks.append(asyncio.create_task(self._ping_loop()))
        self._report_event(
            "transport_start",
            base_url=self.base_url,
            collapse_data_lanes=self.collapse_data_lanes,
        )

    async def stop(self):
        self.stop_event.set()
        self._report_event("transport_stop")
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks = []

    async def send_frame(self, lane, frame):
        if self.collapse_data_lanes and lane in ("pri", "bulk"):
            if frame.type_id == FRAME_DATA:
                flags = int(frame.flags)
                if lane == "bulk":
                    flags |= FLAG_DATA_BULK
                else:
                    flags &= ~FLAG_DATA_BULK
                frame = Frame(
                    frame.type_id,
                    stream_id=frame.stream_id,
                    offset=frame.offset,
                    payload=frame.payload,
                    flags=flags,
                )
            await self.data_queue.put(frame)
            return
        if lane not in self.queues:
            raise ValueError("unknown lane")
        await self.queues[lane].put(frame)

    async def _lane_loop(self, lane):
        while not self.stop_event.is_set():
            try:
                ssl_context = self._ssl_context_for_lane()
                connect_kwargs = {
                    "additional_headers": self._headers(),
                    "proxy": self._websocket_proxy_arg(),
                    "open_timeout": self.http_timeout_seconds,
                    "ping_interval": float(self.protocol_config.get("ws_ping_interval_seconds", 20)),
                    "ping_timeout": float(self.protocol_config.get("ws_ping_timeout_seconds", 20)),
                    "close_timeout": 5,
                    "max_size": None,
                    "max_queue": 16,
                    "write_limit": 32768,
                }
                if ssl_context is not None:
                    connect_kwargs["ssl"] = ssl_context
                async with ws_connect(
                    self._lane_url(lane),
                    **connect_kwargs,
                ) as websocket:
                    self._mark_success("ws", lane)
                    trace("%s/%s@%s ws open lane=%s" % (self.role, self.peer_label, self.peer_session_id, lane))
                    
                    iv = os.urandom(16)
                    send_cipher = TransportCipher(self.token.encode('utf-8'), iv)
                    recv_cipher = None
                    
                    recv_task = asyncio.create_task(self._recv_loop(websocket, lane, recv_cipher=recv_cipher))
                    send_task = asyncio.create_task(self._send_loop(websocket, lane, send_cipher=send_cipher, send_iv=iv))
                    done, pending = await asyncio.wait(
                        [recv_task, send_task],
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()
                    for task in pending:
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                    for task in done:
                        task.result()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                delay = self._backoff_after_error("ws", lane)
                trace("%s/%s@%s ws error lane=%s delay=%0.3f error=%r" % (self.role, self.peer_label, self.peer_session_id, lane, delay, error))
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _send_loop(self, websocket, lane, send_cipher, send_iv):
        first_frame = True
        while not self.stop_event.is_set():
            frame = await self._next_outbound_frame(lane)
            payload = encode_frame(frame)
            
            try:
                encrypted_payload = send_cipher.process(payload)
                if first_frame:
                    encrypted_payload = send_iv + encrypted_payload
                    first_frame = False
                    
                await websocket.send(encrypted_payload)
                if frame.type_id != FRAME_PING:
                    trace("%s/%s@%s ws send lane=%s type=%s stream=%s payload=%s" % (self.role, self.peer_label, self.peer_session_id, lane, frame.type_id, frame.stream_id, len(frame.payload)))
            except Exception:
                await self._requeue_frame_front(lane, frame)
                raise

    async def _recv_loop(self, websocket, lane, recv_cipher):
        decoder = FrameDecoder()
        iv_buffer = b""
        async for message in websocket:
            if isinstance(message, str):
                message = message.encode("utf-8")
            if not message:
                continue
                
            # Extract the 16-byte IV from the front of the stream
            if recv_cipher is None:
                needed_iv = 16 - len(iv_buffer)
                iv_buffer += message[:needed_iv]
                message = message[needed_iv:]
                if len(iv_buffer) == 16:
                    recv_cipher = TransportCipher(self.token.encode('utf-8'), iv_buffer)
                if not message:
                    continue

            plaintext_message = recv_cipher.process(message)
            
            for frame in decoder.feed(plaintext_message):
                logical_lane = self._logical_lane(lane, frame)
                if frame.type_id != FRAME_PING:
                    trace("%s/%s@%s ws recv lane=%s type=%s stream=%s payload=%s" % (self.role, self.peer_label, self.peer_session_id, logical_lane, frame.type_id, frame.stream_id, len(frame.payload)))
                await self.on_frame(frame, logical_lane)

    async def _ping_loop(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(self._jittered_interval(self.protocol_config.get("heartbeat_interval_seconds", 15.0)))
            await self.send_frame(LANE_CTL, Frame(FRAME_PING, offset=int(asyncio.get_running_loop().time() * 1000)))

    def _headers(self):
        return build_connection_headers(
            self.token,
            self.role,
            self.peer_label,
            self.peer_session_id,
            self.protocol_config,
        )

    def _lane_url(self, lane):
        url = self.route_provider.ws_lane_url(lane)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https":
            scheme = "wss"
        elif parsed.scheme == "http":
            scheme = "ws"
        else:
            scheme = parsed.scheme
        return urllib.parse.urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))

    def _websocket_proxy_arg(self):
        # websockets 15+ auto-discovers ambient proxies unless proxy=None.
        # Twoman should stay deterministic and use only the explicitly configured
        # hidden-path upstream proxy when one is present.
        if self.upstream_proxy_url:
            return self.upstream_proxy_url
        return None

    def _external_lanes(self):
        if self.collapse_data_lanes:
            return (LANE_CTL, LANE_DATA)
        return LANES

    def _mark_success(self, direction, lane):
        self.failure_counts[(direction, lane)] = 0

    def _backoff_after_error(self, direction, lane):
        key = (direction, lane)
        failures = self.failure_counts.get(key, 0) + 1
        self.failure_counts[key] = failures
        return jittered_backoff_seconds(
            failures,
            initial_delay=float(self.protocol_config.get("backoff_initial_delay_seconds", 0.1)),
            maximum_delay=float(self.protocol_config.get("backoff_max_delay_seconds", 5.0)),
            multiplier=float(self.protocol_config.get("backoff_multiplier", 2.0)),
            free_failures=int(self.protocol_config.get("backoff_free_failures", 1)),
            rng=self.random,
        )

    async def _next_outbound_frame(self, lane):
        if lane != LANE_DATA:
            return await self.queues[lane].get()
        return await self.data_queue.get()

    async def _requeue_frame_front(self, lane, frame):
        if lane != LANE_DATA:
            await self.queues[lane].putleft(frame)
            return
        await self.data_queue.putleft(frame)

    def _ssl_context_for_lane(self):
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "https":
            return None
        if self.verify_tls:
            return None
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _jittered_interval(self, base_delay):
        return jittered_interval_seconds(
            base_delay,
            jitter_ratio=float(self.protocol_config.get("interval_jitter_ratio", 0.2)),
            rng=self.random,
        )

    def _logical_lane(self, lane, frame):
        if lane != LANE_DATA:
            return lane
        if frame.type_id != FRAME_DATA:
            return LANE_CTL
        return "bulk" if (frame.flags & FLAG_DATA_BULK) else LANE_PRI

    def _report_event(self, kind, **fields):
        if self.event_handler is None:
            return
        payload = {
            "kind": kind,
            "role": self.role,
            "peer_label": self.peer_label,
            "peer_session_id": self.peer_session_id,
        }
        payload.update(fields)
        try:
            self.event_handler(payload)
        except Exception:
            pass


class AdaptiveTransport(object):
    def __init__(self, config, role, peer_id, on_frame):
        self.config = dict(config)
        self.role = role
        self.peer_label = peer_id
        self.on_frame = on_frame
        self.event_handler = None
        self.transport = None
        self.selected_profile = ""
        self.capabilities = {}
        self._peer_session_id = ""

    @property
    def peer_session_id(self):
        if self.transport is None:
            return self._peer_session_id
        return self.transport.peer_session_id

    @property
    def stream_control_lane(self):
        if self.transport is None:
            return str(self.config.get("stream_control_lane", LANE_CTL)).strip().lower() or LANE_CTL
        return getattr(self.transport, "stream_control_lane", LANE_CTL)

    async def start(self):
        if self.transport is not None:
            return
        capabilities = {}
        try:
            capabilities = await self._fetch_capabilities()
            self.capabilities = capabilities
            self._report_event(
                "transport_capabilities",
                backend_family=capabilities.get("backend_family", ""),
                recommended_profile=capabilities.get("recommended_profile", ""),
                supported_profiles=capabilities.get("supported_profiles", []),
            )
        except Exception as error:
            self._report_event("transport_capabilities_error", error=repr(error))
            trace("%s/%s capabilities error=%r" % (self.role, self.peer_label, error))
        candidate_profiles = _profile_candidates(self.config, capabilities)
        errors = []
        for profile_name in candidate_profiles:
            resolved_config = _apply_transport_profile(self.config, self.role, capabilities, profile_name)
            if resolved_config.get("transport") == "ws":
                ok, reason = await self._probe_websocket_transport(resolved_config)
                if not ok:
                    errors.append("%s: %s" % (profile_name, reason))
                    self._report_event(
                        "transport_profile_probe_failed",
                        profile=profile_name,
                        transport=resolved_config.get("transport", ""),
                        reason=reason,
                    )
                    continue
            transport = _instantiate_transport(resolved_config, self.role, self.peer_label, self.on_frame)
            transport.event_handler = self.event_handler
            await transport.start()
            self.transport = transport
            self._peer_session_id = transport.peer_session_id
            self.selected_profile = profile_name
            self._report_event(
                "transport_profile_selected",
                profile=profile_name,
                transport=resolved_config.get("transport", ""),
                backend_family=capabilities.get("backend_family", ""),
            )
            return
        fallback = _instantiate_transport(self.config, self.role, self.peer_label, self.on_frame)
        fallback.event_handler = self.event_handler
        await fallback.start()
        self.transport = fallback
        self._peer_session_id = fallback.peer_session_id
        self.selected_profile = _profile_from_explicit_transport(self.config) or "legacy"
        self._report_event(
            "transport_profile_fallback",
            profile=self.selected_profile,
            errors=errors,
        )

    async def stop(self):
        if self.transport is None:
            return
        await self.transport.stop()
        self.transport = None

    async def send_frame(self, lane, frame):
        if self.transport is None:
            raise RuntimeError("transport is not started")
        await self.transport.send_frame(lane, frame)

    async def _fetch_capabilities(self):
        route_provider = RouteProvider.from_config(self.config["broker_base_url"], _protocol_config_from_config(self.config))
        timeout_seconds = float(self.config.get("http_timeout_seconds", 30))
        timeout = httpx.Timeout(
            None,
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )
        headers = build_connection_headers(
            self.config["client_token"] if self.role == "helper" else self.config["agent_token"],
            self.role,
            self.peer_label,
            os.urandom(8).hex(),
            _protocol_config_from_config(self.config),
        )
        client_kwargs = {
            "http2": False,
            "timeout": timeout,
            "verify": bool(self.config.get("verify_tls", True)),
        }
        client_kwargs.update(
            httpx_proxy_kwargs(
                _normalize_upstream_proxy_url(self.config.get("upstream_proxy_url", "")),
                async_client=True,
            )
        )
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.get(route_provider.health_url(), headers=headers)
            response.raise_for_status()
            validate_json_media_type(response.headers.get("content-type", ""))
            payload = response.json()
        return _extract_transport_capabilities(payload)

    async def _probe_websocket_transport(self, config):
        upstream_proxy_url = _normalize_upstream_proxy_url(config.get("upstream_proxy_url", ""))
        route_provider = RouteProvider.from_config(config["broker_base_url"], _protocol_config_from_config(config))
        url = route_provider.ws_lane_url(LANE_CTL)
        ssl_context = None
        parsed = urllib.parse.urlsplit(config["broker_base_url"])
        if parsed.scheme == "https" and not bool(config.get("verify_tls", True)):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        headers = build_connection_headers(
            config["client_token"] if self.role == "helper" else config["agent_token"],
            self.role,
            self.peer_label,
            os.urandom(8).hex(),
            _protocol_config_from_config(config),
        )
        try:
            connect_kwargs = {
                "additional_headers": headers,
                "proxy": upstream_proxy_url or None,
                "open_timeout": min(5.0, float(config.get("http_timeout_seconds", 30))),
                "ping_interval": None,
                "close_timeout": 1,
                "max_size": 1,
            }
            if ssl_context is not None:
                connect_kwargs["ssl"] = ssl_context
            async with ws_connect(
                url,
                **connect_kwargs,
            ):
                return True, ""
        except Exception as error:
            return False, repr(error)

    def _report_event(self, kind, **fields):
        if self.event_handler is None:
            return
        payload = {
            "kind": kind,
            "role": self.role,
            "peer_label": self.peer_label,
            "peer_session_id": self.peer_session_id,
        }
        payload.update(fields)
        try:
            self.event_handler(payload)
        except Exception:
            pass


def create_transport(config, role, peer_id, on_frame):
    transport_profile = str(config.get("transport_profile", "")).strip().lower()
    if transport_profile == DEFAULT_TRANSPORT_PROFILE:
        return AdaptiveTransport(config, role, peer_id, on_frame)
    if transport_profile:
        resolved_config = _apply_transport_profile(config, role, {}, transport_profile)
        return _instantiate_transport(resolved_config, role, peer_id, on_frame)
    return _instantiate_transport(config, role, peer_id, on_frame)
