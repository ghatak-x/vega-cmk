#!/usr/bin/env python3
"""Safe VDA transport for one Vega OS device per Checkmk site.

VDA 0.24 can list a TCP device yet reject a literal `-s <ip>:<port>`
selection. A guarded `-e` fallback is permitted only when exactly one
TCP device is listed and it matches the configured device. Never disconnects
or kills/restarts a shared VDA server; never prints raw VDA stderr in checks.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceStatus:
    matching: bool
    tcp_count: int
    states: tuple[str, ...]


class VDAError(RuntimeError):
    """Generic safe error: raw output may include host/device private data."""


class VDAClient:
    def __init__(self, executable: str, device: str, server_port: int | None = None):
        self.executable = executable
        self.device = device
        self.env = os.environ.copy()
        # Site-local config (when present) is authoritative; an accidental
        # `export` in an interactive shell must not silently change polling.
        self.env.pop("VEGA_VDA_SERVER_PORT", None)
        if server_port is not None:
            self.env["VEGA_VDA_SERVER_PORT"] = str(server_port)
        # A server socket override takes precedence over the host/port flags.
        self.env.pop("VDA_SERVER_SOCKET", None)
        self.env.pop("VEGA_VDA_SERVER_ADDRESS", None)
        # Explicit -s or -e below always takes precedence over VEGA_SERIAL.
        self.env.pop("VEGA_SERIAL", None)
        self.env.pop("VDA_SERIAL", None)

    def _call(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.executable, *args], capture_output=True, text=True,
                timeout=timeout, check=False, env=self.env,
            )
        except subprocess.TimeoutExpired as exc:
            raise VDAError(f"VDA timed out after {timeout}s") from exc
        except OSError as exc:
            raise VDAError("VDA executable unavailable") from exc

    def devices(self, timeout: int = 7) -> DeviceStatus:
        result = self._call(["devices"], timeout)
        if result.returncode:
            raise VDAError(f"VDA device listing failed (exit {result.returncode})")
        found: dict[str, str] = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1] in {"device", "offline", "unauthorized", "no", "recovery"}:
                found[fields[0]] = fields[1]
        matching = found.get(self.device) == "device"
        return DeviceStatus(
            matching=matching,
            tcp_count=sum(1 for identifier in found if re.fullmatch(r"[^\s:]+:\d+", identifier)),
            states=tuple(sorted(set(found.values()))),
        )

    def _connect_if_absent(self) -> DeviceStatus:
        status = self.devices()
        if status.matching:
            return status  # No unconditional `connect` per Checkmk poll.
        # A missing target might be disconnected, but do not disturb existing
        # VDA sessions: add only our configured target with a short timeout.
        result = self._call(["connect", self.device], 8)
        if result.returncode:
            raise VDAError(f"VDA target not connected; connect failed (exit {result.returncode})")
        status = self.devices()
        if not status.matching:
            raise VDAError("VDA target not reported as connected after connect")
        return status

    def ensure_connected(self) -> None:
        self._connect_if_absent()

    def run(self, *args: str, timeout: int = 25) -> subprocess.CompletedProcess:
        """Run a remote command. Never select a different TCP/IP device."""
        first = self._call(["-s", self.device, *args], timeout)
        if first.returncode == 0:
            return first
        diagnostic = (first.stderr or first.stdout).lower()
        if "device" not in diagnostic or "not found" not in diagnostic:
            raise VDAError(f"VDA command failed (exit {first.returncode})")
        # Device not found -> check the current transport instead of assuming
        # a stale connection and calling `connect` on every collection.
        status = self._connect_if_absent()
        second = self._call(["-s", self.device, *args], timeout)
        if second.returncode == 0:
            return second
        # `-e` refers to the only connected TCP/IP device in VDA 0.24.
        # Only attempt it when the configured target is that one device.
        if status.matching and status.tcp_count == 1:
            alternate = self._call(["-e", *args], timeout)
            if alternate.returncode == 0:
                return alternate
            raise VDAError(
                f"VDA target is listed, but both -s and guarded -e failed "
                f"(exit {second.returncode}/{alternate.returncode})"
            )
        raise VDAError(
            f"VDA target selection failed (exit {second.returncode}); "
            f"TCP/IP devices listed: {status.tcp_count}"
        )

    def shell(self, *args: str, timeout: int = 25) -> str:
        return self.run("shell", *args, timeout=timeout).stdout

    def diagnosis(self) -> list[str]:
        """Safe, non-mutating connection diagnostics (no connect/restart)."""
        checks = [("devices", ["devices"]),
                  ("selected shell", ["-s", self.device, "shell", "echo", "CHECKMK_VEGA_OK"])]
        try:
            status = self.devices()
        except VDAError as exc:
            return [f"VDA listing: {exc}"]
        lines = [f"Target present and online: {status.matching}",
                 f"Number of TCP/IP devices listed: {status.tcp_count}",
                 f"Reported states: {','.join(status.states) or '(none)'}"]
        for name, args in checks[1:]:
            try:
                result = self._call(args, 10)
                lines.append(f"{name}: exit={result.returncode}, sentinel={('CHECKMK_VEGA_OK' in result.stdout)}")
            except VDAError as exc:
                lines.append(f"{name}: {exc}")
        if status.matching and status.tcp_count == 1:
            try:
                result = self._call(["-e", "shell", "echo", "CHECKMK_VEGA_OK"], 10)
                lines.append(f"single TCP fallback (-e): exit={result.returncode}, sentinel={('CHECKMK_VEGA_OK' in result.stdout)}")
            except VDAError as exc:
                lines.append(f"single TCP fallback (-e): {exc}")
        return lines
