#!/usr/bin/env python3
"""Site-local settings for the Vega OS Checkmk integration.

Nothing under ~/etc or ~/var is part of the MKP. No network or telemetry.
"""
from __future__ import annotations
import ipaddress
import json
import os
import re
from pathlib import Path

DEFAULT_APPS = {"Netflix": "com.netflix.kepler", "YouTube": "com.amazon.firetv.youtube"}
PKG = re.compile(r"^[A-Za-z0-9_]+(?:[.][A-Za-z0-9_-]+)+$")


def site_root() -> Path:
    return Path(os.environ.get("OMD_ROOT") or Path.home()).resolve()


def load_config() -> dict:
    path = site_root() / "etc" / "firetv_vega.json"
    if not path.is_file():
        raise RuntimeError("Fire TV configuration missing: create ~/etc/firetv_vega.json (see README)")
    # Config is site-local (not packaged) and may expose a private device address.
    if path.is_symlink():
        raise RuntimeError("Refusing symlinked Fire TV configuration")
    if path.stat().st_mode & 0o077:
        raise RuntimeError("Fire TV configuration must be owner-only: chmod 600 ~/etc/firetv_vega.json")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise RuntimeError("Could not read Fire TV configuration JSON") from exc
    if not isinstance(cfg, dict):
        raise RuntimeError("Fire TV configuration must be a JSON object")
    vda = cfg.get("vda_path")
    target = cfg.get("device")
    if not isinstance(vda, str) or not Path(vda).is_absolute() or not Path(vda).is_file():
        raise RuntimeError("vda_path must be an absolute path to an existing VDA binary")
    if not isinstance(target, str) or ":" not in target:
        raise RuntimeError("device must be an IPv4-address:port, e.g. 192.0.2.10:5555")
    host, port = target.rsplit(":", 1)
    try:
        addr = ipaddress.ip_address(host)
        if addr.version != 4 or not 1 <= int(port) <= 65535:
            raise ValueError()
    except ValueError as exc:
        raise RuntimeError("device must be an IPv4-address:port") from exc
    apps = cfg.get("apps", DEFAULT_APPS)
    if not isinstance(apps, dict) or len(apps) > 20 or not apps or not all(
        isinstance(label, str) and 0 < len(label) <= 30 and re.fullmatch(r"[A-Za-z0-9 _-]+", label)
        and isinstance(pkg, str) and PKG.fullmatch(pkg) for label, pkg in apps.items()
    ):
        raise RuntimeError("apps must map safe display names to valid package IDs (max 20)")
    def positive(name: str, default: int, upper: int = 86400) -> int:
        value = cfg.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise RuntimeError(f"{name} must be an integer in range 1..{upper}")
        return value
    window = positive("process_death_window_minutes", 30, 120)
    warn = positive("process_deaths_warn", 2, 1000)
    crit = positive("process_deaths_crit", 5, 1000)
    if warn >= crit:
        raise RuntimeError("process_deaths_warn must be less than process_deaths_crit")
    port_override = cfg.get("vda_server_port")
    if port_override is not None and (isinstance(port_override, bool) or not isinstance(port_override, int)
                                      or not 1 <= port_override <= 65534):
        raise RuntimeError("vda_server_port must be null or an integer in 1..65534")
    cfg["apps"] = apps
    cfg["process_death_window_minutes"] = window
    cfg["process_deaths_warn"] = warn
    cfg["process_deaths_crit"] = crit
    cfg["foreground_gap_seconds"] = positive("foreground_gap_seconds", 600, 3600)
    cfg["screenshot_cooldown_seconds"] = positive("screenshot_cooldown_seconds", 20, 3600)
    psi_enabled = cfg.get("memory_pressure_enabled", True)
    if type(psi_enabled) is not bool:
        raise RuntimeError("memory_pressure_enabled must be true or false")
    cfg["memory_pressure_enabled"] = psi_enabled
    for option in ("cpu_pressure_enabled", "io_pressure_enabled"):
        enabled = cfg.get(option, True)
        if type(enabled) is not bool:
            raise RuntimeError(f"{option} must be true or false")
        cfg[option] = enabled
    return cfg


def vda_settings() -> tuple[str, str, dict]:
    cfg = load_config()
    return cfg["vda_path"], cfg["device"], cfg


def private_dir() -> Path:
    return site_root() / "var" / "firetv_private"


def vda_client():
    """A shared, site-configured VDA transport with safe error handling."""
    from firetv_vda import VDAClient
    vda, device, cfg = vda_settings()
    return VDAClient(vda, device, cfg.get("vda_server_port"))
