"""OpenClaw Connection Test for Voice Bridge Installation.

Tests that OpenClaw is reachable and responding before the bridge starts.
Captures enough diagnostic detail to identify the root cause of failures.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from installer.hardware_test import HardwareTestResult, TestStatus


class TCPFailureReason(Enum):
    CONNECTION_REFUSED = "connection_refused"   # port not open — service not running
    TIMEOUT            = "timeout"              # port open but not responding
    DNS_FAILURE        = "dns_failure"          # hostname does not resolve
    PERMISSION_DENIED  = "permission_denied"    # OS blocked the connection
    OTHER              = "other"


@dataclass
class OpenClawTestResult:
    """Full diagnostic result of an OpenClaw connection test."""
    host: str
    port: int
    url: str                              # exact URL tested

    # TCP phase
    tcp_reachable: bool
    tcp_failure_reason: Optional[TCPFailureReason] = None
    tcp_error: Optional[str] = None

    # HTTP phase
    http_ok: bool = False
    http_status: Optional[int] = None
    http_error: Optional[str] = None
    http_response_body: Optional[str] = None  # raw body on non-200 or error
    latency_ms: Optional[float] = None
    response_preview: Optional[str] = None   # parsed AI reply on success

    # Config context (populated by run_openclaw_test)
    config_path: Optional[str] = None
    api_mode: Optional[str] = None
    auth_token_set: bool = False

    @property
    def passed(self) -> bool:
        return self.tcp_reachable and self.http_ok

    # ------------------------------------------------------------------ #
    # Human-readable diagnostics                                           #
    # ------------------------------------------------------------------ #

    def _tcp_hint(self) -> str:
        reason = self.tcp_failure_reason
        if reason == TCPFailureReason.CONNECTION_REFUSED:
            return (
                f"  Port {self.port} is not accepting connections — OpenClaw is not running.\n"
                f"  Start OpenClaw first, then re-run the installer."
            )
        if reason == TCPFailureReason.TIMEOUT:
            return (
                f"  Connection to {self.host}:{self.port} timed out.\n"
                f"  Possible causes: wrong port, firewall blocking, or slow startup.\n"
                f"  Verify OpenClaw is listening: ss -tlnp | grep {self.port}"
            )
        if reason == TCPFailureReason.DNS_FAILURE:
            return (
                f"  Hostname '{self.host}' could not be resolved.\n"
                f"  Check the 'host' setting in your config and make sure it is correct."
            )
        if reason == TCPFailureReason.PERMISSION_DENIED:
            return (
                f"  OS denied the connection to {self.host}:{self.port}.\n"
                f"  Check firewall rules or whether the port requires elevated privileges."
            )
        return f"  Raw error: {self.tcp_error}"

    def _http_hint(self) -> str:
        status = self.http_status
        if status == 401 or status == 403:
            token_note = "auth_token IS set in config" if self.auth_token_set else "auth_token is NOT set in config"
            return (
                f"  HTTP {status}: authentication required.\n"
                f"  {token_note}.\n"
                f"  Set 'auth_token' in config.yaml or the OPENCLAW_GATEWAY_TOKEN env var."
            )
        if status == 404:
            return (
                f"  HTTP 404: endpoint not found at {self.url}\n"
                f"  OpenClaw is running but the API path is wrong.\n"
                f"  Check 'ws_path' or the OpenClaw API version."
            )
        if status == 503 or status == 502:
            return (
                f"  HTTP {status}: OpenClaw is up but the backend is unavailable.\n"
                f"  OpenClaw may still be starting up — wait a moment and retry."
            )
        if status == 500:
            return (
                f"  HTTP 500: OpenClaw returned an internal error.\n"
                f"  Check the OpenClaw logs for details."
            )
        if self.http_error:
            return f"  Error: {self.http_error}"
        return ""

    def _config_context(self) -> list[str]:
        lines = []
        if self.config_path:
            lines.append(f"  Config file : {self.config_path}")
        lines.append(f"  URL tested  : {self.url}")
        lines.append(f"  api_mode    : {self.api_mode or 'unknown'}")
        lines.append(f"  auth_token  : {'set' if self.auth_token_set else 'not set'}")
        return lines

    def as_hardware_result(self) -> HardwareTestResult:
        endpoint = f"{self.host}:{self.port}"
        context = "\n".join(self._config_context())

        if not self.tcp_reachable:
            detail_lines = [context, "", "TCP connection failed:", self._tcp_hint()]
            return HardwareTestResult(
                test_name="OpenClaw Connection",
                status=TestStatus.FAILED,
                message=f"Cannot reach OpenClaw at {endpoint}",
                details="\n".join(detail_lines),
            )

        if not self.http_ok:
            body_snippet = ""
            if self.http_response_body:
                body_snippet = f"\n  Response body:\n    {self.http_response_body[:300]}"
            detail_lines = [
                context,
                "",
                f"TCP connected to {endpoint} but HTTP request failed:",
                self._http_hint(),
                body_snippet,
            ]
            return HardwareTestResult(
                test_name="OpenClaw Connection",
                status=TestStatus.FAILED,
                message=f"OpenClaw at {endpoint} rejected the request (HTTP {self.http_status})",
                details="\n".join(detail_lines),
            )

        detail_lines = [
            context,
            f"  Latency     : {self.latency_ms:.0f}ms",
            f"  Response    : {self.response_preview}",
        ]
        return HardwareTestResult(
            test_name="OpenClaw Connection",
            status=TestStatus.PASSED,
            message=f"OpenClaw is reachable at {endpoint} ({self.latency_ms:.0f}ms)",
            details="\n".join(detail_lines),
        )


def _classify_tcp_error(exc: Exception) -> TCPFailureReason:
    """Map a socket exception to a TCPFailureReason."""
    msg = str(exc).lower()
    if isinstance(exc, ConnectionRefusedError):
        return TCPFailureReason.CONNECTION_REFUSED
    if isinstance(exc, socket.timeout):
        return TCPFailureReason.TIMEOUT
    if isinstance(exc, PermissionError):
        return TCPFailureReason.PERMISSION_DENIED
    # socket.gaierror is a subclass of OSError
    if isinstance(exc, socket.gaierror) or "name or service not known" in msg or "nodename nor servname" in msg:
        return TCPFailureReason.DNS_FAILURE
    if "timed out" in msg:
        return TCPFailureReason.TIMEOUT
    if "refused" in msg:
        return TCPFailureReason.CONNECTION_REFUSED
    return TCPFailureReason.OTHER


def test_openclaw_connection(
    host: str,
    port: int,
    timeout: float = 5.0,
    auth_token: Optional[str] = None,
    config_path: Optional[str] = None,
    api_mode: Optional[str] = None,
) -> OpenClawTestResult:
    """
    Test the connection to OpenClaw.

    Steps:
    1. TCP connect      — confirms the service is listening on host:port.
    2. HTTP POST        — confirms /v1/chat/completions responds correctly.

    Returns:
        OpenClawTestResult with full diagnostic detail.
    """
    url = f"http://{host}:{port}/v1/chat/completions"
    base = OpenClawTestResult(
        host=host,
        port=port,
        url=url,
        tcp_reachable=False,
        config_path=config_path,
        api_mode=api_mode,
        auth_token_set=bool(auth_token),
    )

    # ------------------------------------------------------------------ #
    # Step 1: TCP                                                          #
    # ------------------------------------------------------------------ #
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        base.tcp_reachable = True
    except (ConnectionRefusedError, socket.timeout, socket.gaierror, OSError, PermissionError) as exc:
        base.tcp_failure_reason = _classify_tcp_error(exc)
        base.tcp_error = str(exc)
        return base

    # ------------------------------------------------------------------ #
    # Step 2: HTTP                                                         #
    # ------------------------------------------------------------------ #
    payload = json.dumps({
        "model": "openclaw:main",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            base.latency_ms = (time.time() - start) * 1000
            base.http_status = resp.status
            body = resp.read(512).decode("utf-8", errors="replace")

        base.http_ok = (base.http_status == 200)

        if base.http_ok:
            try:
                data = json.loads(body)
                choices = data.get("choices", [])
                preview = choices[0]["message"]["content"].strip() if choices else body[:80]
            except Exception:
                preview = body[:80]
            base.response_preview = preview[:80]
        else:
            base.http_response_body = body
            base.http_error = f"HTTP {base.http_status}"

    except urllib.error.HTTPError as exc:
        base.latency_ms = (time.time() - start) * 1000
        base.http_status = exc.code
        base.http_error = f"HTTP {exc.code}: {exc.reason}"
        try:
            base.http_response_body = exc.read(512).decode("utf-8", errors="replace")
        except Exception:
            pass

    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        base.latency_ms = (time.time() - start) * 1000
        base.http_error = str(exc)

    return base


def run_openclaw_test() -> HardwareTestResult:
    """
    Read config and run the OpenClaw connection test.

    Returns:
        HardwareTestResult suitable for display in the installer.
    """
    try:
        from bridge.config import get_config
        cfg_obj = get_config()
        cfg = cfg_obj.openclaw

        # Find config file path for display
        import os
        config_path = None
        candidates = [
            os.path.expanduser("~/.voice-bridge/config.yaml"),
            "config.yaml",
        ]
        for p in candidates:
            if os.path.exists(p):
                config_path = os.path.abspath(p)
                break

        result = test_openclaw_connection(
            host=cfg.host,
            port=cfg.port,
            timeout=min(cfg.timeout, 10.0),
            auth_token=cfg.get_auth_token() if hasattr(cfg, "get_auth_token") else getattr(cfg, "auth_token", None),
            config_path=config_path or "(default)",
            api_mode=getattr(cfg, "api_mode", "http"),
        )
        return result.as_hardware_result()

    except Exception as exc:
        return HardwareTestResult(
            test_name="OpenClaw Connection",
            status=TestStatus.ERROR,
            message="Could not read OpenClaw config",
            details=str(exc),
        )
