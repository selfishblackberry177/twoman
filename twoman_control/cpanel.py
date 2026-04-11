from __future__ import annotations

import json
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from twoman_control.models import (
    BACKEND_BRIDGE,
    BACKEND_NODE,
    BACKEND_PASSENGER,
    BackendCapability,
)


def _public_host_from_origin(public_origin: str) -> str:
    parsed = urlparse(public_origin)
    return parsed.netloc or parsed.path


@dataclass(slots=True)
class CpanelClient:
    base_url: str
    username: str
    password: str
    cpanel_home: str
    verify: bool = True
    proxy_url: str = ""

    def _request_with_retry(self, method: str, endpoint: str, *, timeout: float, retries: int = 3, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return httpx.request(
                    method,
                    f"{self.base_url.rstrip('/')}/execute/{endpoint}",
                    auth=(self.username, self.password),
                    timeout=timeout,
                    verify=self.verify,
                    proxy=self.proxy_url or None,
                    follow_redirects=True,
                    **kwargs,
                )
            except httpx.RequestError as error:
                last_error = error
                if attempt >= retries:
                    break
                time.sleep(0.5 * attempt)
        assert last_error is not None
        raise last_error

    def _public_get(self, url: str, *, timeout: float, verify: bool) -> httpx.Response:
        response = httpx.request(
            "GET",
            url,
            timeout=timeout,
            verify=verify,
            proxy=self.proxy_url or None,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response

    def _api_get(self, endpoint: str, **params: Any) -> httpx.Response:
        response = self._request_with_retry(
            "GET",
            endpoint,
            timeout=20.0,
            params=params,
        )
        response.raise_for_status()
        return response

    def _api_post(self, endpoint: str, *, data: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> httpx.Response:
        response = self._request_with_retry(
            "POST",
            endpoint,
            timeout=30.0,
            data=data,
            files=files,
        )
        response.raise_for_status()
        return response

    def save_file_content(self, remote_dir: str, filename: str, content: str) -> None:
        response = self._api_post(
            "Fileman/save_file_content",
            data={
                "dir": remote_dir,
                "file": filename,
                "content": content,
                "from_charset": "UTF-8",
                "to_charset": "UTF-8",
                "fallback": "1",
            },
        )
        payload = response.json()
        if payload.get("status") is False or payload.get("errors"):
            raise RuntimeError(f"save_file_content failed: {payload}")

    def delete_file(self, remote_dir: str, filename: str) -> None:
        self._api_post(
            "Fileman/file_op",
            data={
                "op": "trash",
                "sourcefiles": filename,
                "metadata": remote_dir,
            },
        )

    def passenger_supported(self) -> BackendCapability:
        try:
            payload = self._api_get("PassengerApps/list_applications").json()
        except Exception as error:
            return BackendCapability(
                key=BACKEND_PASSENGER,
                label="Passenger Python",
                available=False,
                reason=f"PassengerApps API unavailable: {error}",
            )
        errors = payload.get("errors") or payload.get("result", {}).get("errors")
        if errors:
            return BackendCapability(
                key=BACKEND_PASSENGER,
                label="Passenger Python",
                available=False,
                reason=f"PassengerApps API returned errors: {errors}",
            )
        return BackendCapability(
            key=BACKEND_PASSENGER,
            label="Passenger Python",
            available=True,
            reason="Application Manager / Passenger API responded.",
            details={"raw": payload},
        )

    def bridge_supported(self, public_origin: str, public_verify_tls: bool) -> BackendCapability:
        probe_name = f"twoman_probe_{secrets.token_hex(4)}.txt"
        public_html = f"{self.cpanel_home.rstrip('/')}/public_html"
        probe_body = f"twoman-bridge-probe:{probe_name}"
        try:
            self.save_file_content(public_html, probe_name, probe_body)
            response = self._public_get(
                f"{public_origin.rstrip('/')}/{probe_name}",
                timeout=20.0,
                verify=public_verify_tls,
            )
            if response.text.strip() != probe_body:
                raise RuntimeError("public probe content mismatch")
        except Exception as error:
            return BackendCapability(
                key=BACKEND_BRIDGE,
                label="cPanel Runtime Bridge",
                available=False,
                reason=f"Public HTML probe failed: {error}",
            )
        finally:
            try:
                self.delete_file(public_html, probe_name)
            except Exception:
                pass
        return BackendCapability(
            key=BACKEND_BRIDGE,
            label="cPanel Runtime Bridge",
            available=True,
            reason="File manager upload and public origin probe both succeeded.",
        )

    def node_selector_supported(self, public_origin: str, public_verify_tls: bool) -> BackendCapability:
        local_node = shutil.which("node")
        local_npx = shutil.which("npx")
        if not local_node or not local_npx:
            return BackendCapability(
                key=BACKEND_NODE,
                label="CloudLinux Node Selector",
                available=False,
                reason="Local prerequisites missing: both node and npx are required to bundle the Node broker.",
                details={"local_node": bool(local_node), "local_npx": bool(local_npx)},
            )
        probe_name = f"twoman_probe_{secrets.token_hex(4)}.php"
        public_html = f"{self.cpanel_home.rstrip('/')}/public_html"
        php_probe = """<?php
header('Content-Type: application/json');
$selector = '/usr/sbin/cloudlinux-selector';
$result = [
  'selector_executable' => is_executable($selector),
  'proc_open_available' => function_exists('proc_open'),
];
if ($result['selector_executable'] && $result['proc_open_available']) {
  $spec = [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
  $proc = proc_open($selector . ' list --json --interpreter nodejs 2>&1', $spec, $pipes);
  if (is_resource($proc)) {
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $code = proc_close($proc);
    $result['list_exit_code'] = $code;
    $result['stdout'] = $stdout;
    $result['stderr'] = $stderr;
  } else {
    $result['list_exit_code'] = 127;
    $result['stderr'] = 'proc_open failed';
  }
}
echo json_encode($result);
"""
        try:
            self.save_file_content(public_html, probe_name, php_probe)
            response = self._public_get(
                f"{public_origin.rstrip('/')}/{probe_name}",
                timeout=20.0,
                verify=public_verify_tls,
            )
            payload = response.json()
        except Exception as error:
            return BackendCapability(
                key=BACKEND_NODE,
                label="CloudLinux Node Selector",
                available=False,
                reason=f"Node selector probe failed: {error}",
            )
        finally:
            try:
                self.delete_file(public_html, probe_name)
            except Exception:
                pass
        if not payload.get("selector_executable"):
            return BackendCapability(
                key=BACKEND_NODE,
                label="CloudLinux Node Selector",
                available=False,
                reason="cloudlinux-selector is not executable for this account.",
                details=payload,
            )
        if not payload.get("proc_open_available"):
            return BackendCapability(
                key=BACKEND_NODE,
                label="CloudLinux Node Selector",
                available=False,
                reason="PHP proc_open is disabled; Node selector automation cannot run.",
                details=payload,
            )
        exit_code = int(payload.get("list_exit_code", 1))
        if exit_code != 0:
            return BackendCapability(
                key=BACKEND_NODE,
                label="CloudLinux Node Selector",
                available=False,
                reason="cloudlinux-selector command did not complete successfully.",
                details=payload,
            )
        return BackendCapability(
            key=BACKEND_NODE,
            label="CloudLinux Node Selector",
            available=True,
            reason="Host selector probe and local Node prerequisites both passed.",
            details=payload,
        )

    def detect_capabilities(self, public_origin: str, public_verify_tls: bool) -> list[BackendCapability]:
        capabilities = [
            self.node_selector_supported(public_origin, public_verify_tls),
            self.passenger_supported(),
            self.bridge_supported(public_origin, public_verify_tls),
        ]
        for capability in capabilities:
            capability.recommended = False
        for backend_key in (BACKEND_NODE, BACKEND_PASSENGER, BACKEND_BRIDGE):
            for capability in capabilities:
                if capability.key == backend_key and capability.available:
                    capability.recommended = True
                    return capabilities
        return capabilities

    def verify_public_tls(self, public_origin: str) -> bool:
        try:
            response = httpx.request(
                "GET",
                public_origin.rstrip("/"),
                timeout=10.0,
                verify=True,
                proxy=self.proxy_url or None,
                follow_redirects=True,
            )
            response.read()
            return True
        except httpx.HTTPError:
            return True
        except Exception:
            return False

    @staticmethod
    def default_cpanel_base_url(public_origin: str) -> str:
        host = _public_host_from_origin(public_origin)
        return f"https://{host}:2083"
