"""OpenClaw Connection Test for Voice Bridge Installation.

Tests that OpenClaw is reachable and responding before the bridge starts.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from installer.hardware_test import HardwareTestResult, TestStatus


@dataclass
class OpenClawTestResult:
    """Result of an OpenClaw connection test."""
    host: str
    port: int
    tcp_reachable: bool
    http_ok: bool
    latency_ms: Optional[float]
    error: Optional[str]
    http_status: Optional[int]
    response_preview: Optional[str]

    @property
    def passed(self) -> bool:
        return self.tcp_reachable and self.http_ok

    def as_hardware_result(self) -> HardwareTestResult:
        endpoint = f"{self.host}:{self.port}"

        if not self.tcp_reachable:
            return HardwareTestResult(
                test_name="OpenClaw Connection",
                status=TestStatus.FAILED,
                message=f"Cannot reach OpenClaw at {endpoint}",
                details=(
                    f"TCP connection refused or timed out.\n"
                    f"  Make sure OpenClaw is running on {endpoint}.\n"
                    f"  Error: {self.error}"
                ),
            )

        if not self.http_ok:
            return HardwareTestResult(
                test_name="OpenClaw Connection",
                status=TestStatus.FAILED,
                message=f"OpenClaw at {endpoint} is not responding correctly",
                details=(
                    f"TCP connected but HTTP request failed.\n"
                    f"  HTTP status: {self.http_status}\n"
                    f"  Error: {self.error}"
                ),
            )

        return HardwareTestResult(
            test_name="OpenClaw Connection",
            status=TestStatus.PASSED,
            message=f"OpenClaw is reachable at {endpoint}",
            details=f"Latency: {self.latency_ms:.0f}ms  |  Response: {self.response_preview}",
        )


def test_openclaw_connection(host: str, port: int, timeout: float = 5.0) -> OpenClawTestResult:
    """
    Test the connection to OpenClaw.

    Steps:
    1. TCP connect — confirms the service is listening.
    2. HTTP POST to /v1/chat/completions — confirms the API responds.

    Args:
        host: OpenClaw hostname or IP
        port: OpenClaw port
        timeout: Connection timeout in seconds

    Returns:
        OpenClawTestResult with full details
    """
    # ------------------------------------------------------------------ #
    # Step 1: TCP reachability                                             #
    # ------------------------------------------------------------------ #
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        tcp_ok = True
        tcp_error = None
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        return OpenClawTestResult(
            host=host,
            port=port,
            tcp_reachable=False,
            http_ok=False,
            latency_ms=None,
            error=str(exc),
            http_status=None,
            response_preview=None,
        )

    # ------------------------------------------------------------------ #
    # Step 2: HTTP request to /v1/chat/completions                         #
    # ------------------------------------------------------------------ #
    url = f"http://{host}:{port}/v1/chat/completions"
    payload = json.dumps({
        "model": "openclaw:main",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = (time.time() - start) * 1000
            http_status = resp.status
            body = resp.read(512).decode("utf-8", errors="replace")

        # Try to extract the response text for a preview
        try:
            data = json.loads(body)
            choices = data.get("choices", [])
            preview = choices[0]["message"]["content"].strip() if choices else body[:80]
        except Exception:
            preview = body[:80]

        return OpenClawTestResult(
            host=host,
            port=port,
            tcp_reachable=True,
            http_ok=(http_status == 200),
            latency_ms=latency_ms,
            error=None if http_status == 200 else f"HTTP {http_status}",
            http_status=http_status,
            response_preview=preview[:80],
        )

    except urllib.error.HTTPError as exc:
        latency_ms = (time.time() - start) * 1000
        return OpenClawTestResult(
            host=host,
            port=port,
            tcp_reachable=True,
            http_ok=False,
            latency_ms=latency_ms,
            error=f"HTTP {exc.code}: {exc.reason}",
            http_status=exc.code,
            response_preview=None,
        )

    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        latency_ms = (time.time() - start) * 1000
        return OpenClawTestResult(
            host=host,
            port=port,
            tcp_reachable=True,
            http_ok=False,
            latency_ms=latency_ms,
            error=str(exc),
            http_status=None,
            response_preview=None,
        )


def run_openclaw_test() -> HardwareTestResult:
    """
    Read config and run the OpenClaw connection test.

    Returns:
        HardwareTestResult suitable for display in the installer.
    """
    try:
        from bridge.config import get_config
        cfg = get_config().openclaw
        host = cfg.host
        port = cfg.port
        timeout = min(cfg.timeout, 10.0)
    except Exception as exc:
        return HardwareTestResult(
            test_name="OpenClaw Connection",
            status=TestStatus.ERROR,
            message="Could not read OpenClaw config",
            details=str(exc),
        )

    result = test_openclaw_connection(host, port, timeout=timeout)
    return result.as_hardware_result()
