#!/usr/bin/env python3
"""Additional read-only Vega OS local checks for Checkmk 2.5.

Runs on the Checkmk site host, NOT on the Fire TV. Requires access to VDA.
Output is Checkmk local-check records WITHOUT a <<<local>>> header: the existing
firetv_agent data-source wrapper should print that header exactly once.

Examples (run as OMD[firetv]):
  python3 ~/local/bin/firetv_vega_deep.py
  python3 ~/local/bin/firetv_vega_deep.py --accept-baseline
  python3 ~/local/bin/firetv_vega_deep.py --show-apps

Never takes screenshots, reports watched titles, installs applications, or
changes Fire TV settings. Build version and installed app version do not prove
that Amazon has released a newer update.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from firetv_config import vda_settings, site_root, vda_client

VDA, DEVICE, CFG = vda_settings()
CLIENT = vda_client()
SITE = site_root()
STATE_DIR = SITE / "var" / "firetv_deep"
CACHE_PATH = STATE_DIR / "app_metadata_cache.json"
BASELINE_PATH = STATE_DIR / "version_baseline.json"
CACHE_SECONDS = 3600
WINDOW_MINUTES = CFG["process_death_window_minutes"]
WARN_DEATHS = CFG["process_deaths_warn"]
CRIT_DEATHS = CFG["process_deaths_crit"]

# Installed package IDs from your actual Fire TV inventory (Oct 27, 2026).
# Edit this list to track more or fewer apps. Package not running != app unhealthy.
APPS = CFG["apps"]

LOG_TIME_RE = re.compile(r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<hms>\d{2}:\d{2}:\d{2})(?:\.\d+)?\s")
DEATH_RE = re.compile(r"Deregistering application.*?package id\s+(?P<pkg>[A-Za-z0-9_.-]+)\s+after process death", re.IGNORECASE)
PAIR_RE = re.compile(r'^([A-Z][A-Z0-9_]+)="?(.*?)"?$')
MON_NUM = {v: i for i, v in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def local(state: int, name: str, metrics: str, summary: str) -> None:
    # Fixed names, sanitized summary; no raw log lines or personal content.
    summary = str(summary).replace("\r", " ").replace("\n", " ")[:300]
    print(f'{state} "{name}" {metrics} {summary}', flush=True)


def vda_shell(*args: str, timeout: int = 15) -> str:
    return CLIENT.shell(*args, timeout=timeout)



def parse_os_release(raw: str) -> dict:
    out = {}
    for line in raw.splitlines():
        match = PAIR_RE.match(line.strip())
        if match:
            out[match.group(1)] = match.group(2)
    if not out.get("VERSION_NUMBER"):
        raise RuntimeError("Device /etc/os-release has no VERSION_NUMBER")
    return out


def parse_vpm_info(raw: str) -> dict:
    # Don't retain the lengthy certificate/fingerprint information from vpm info.
    fields = {}
    for line in raw.splitlines():
        m = re.match(r"^(id|version|build number|update time|install time|enabled):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    if not fields.get("id") or not fields.get("version"):
        raise RuntimeError("Unparseable vpm info (missing id or version)")
    return {
        "id": fields["id"],
        "version": fields["version"],
        "build": fields.get("build number", ""),
        "updated": fields.get("update time", ""),
        "enabled": fields.get("enabled", "unknown"),
    }


def ensure_private_dir() -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)


def atomic_json_write(path: Path, payload: dict) -> None:
    ensure_private_dir()
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", dir=STATE_DIR, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def json_read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as stream:
            obj = json.load(stream)
        if not isinstance(obj, dict):
            raise ValueError("Expected JSON object")
        return obj
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read {path.name}: {exc}") from exc


def collect_apps(force: bool = False) -> tuple[dict, bool]:
    """Return app metadata, cache_fresh boolean.

    If cache refresh fails, return cached data marked stale; caller should WARN.
    No cache + VDA error raises. Cache is only used for app metadata; OS/logs
    remain fresh on every check.
    """
    try:
        cached = json_read(CACHE_PATH)
    except RuntimeError:
        cached = None
    if not force and cached and time.time() - cached.get("collected_at", 0) < CACHE_SECONDS:
        return cached.get("apps", {}), True

    fresh = {}
    try:
        for label, pkg in APPS.items():
            try:
                meta = parse_vpm_info(vda_shell("vpm", "info", pkg, timeout=20))
                if meta["id"] != pkg:
                    raise RuntimeError(f"unexpected ID {meta['id']}")
                fresh[label] = meta
            except RuntimeError as exc:
                fresh[label] = {"id": pkg, "error": str(exc)}
        atomic_json_write(CACHE_PATH, {"collected_at": time.time(), "apps": fresh})
        return fresh, True
    except (OSError, ValueError) as exc:
        if cached and cached.get("apps"):
            return cached["apps"], False
        raise RuntimeError(f"Cannot cache app metadata: {exc}") from exc


def event_datetime(line: str, now: datetime) -> datetime | None:
    m = LOG_TIME_RE.match(line)
    if not m or m.group("mon") not in MON_NUM:
        return None
    candidates = []
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            dt = datetime.strptime(f'{year}-{MON_NUM[m.group("mon")]:02d}-{int(m.group("day")):02d} {m.group("hms")}', "%Y-%m-%d %H:%M:%S")
            candidates.append(dt.replace(tzinfo=now.tzinfo))
        except ValueError:
            continue
    return min(candidates, key=lambda x: abs((now - x).total_seconds())) if candidates else None


def parse_process_deaths(text: str, now: datetime, minutes: int = WINDOW_MINUTES) -> dict[str, int]:
    since = now - timedelta(minutes=minutes)
    counts = {pkg: 0 for pkg in APPS.values()}
    for line in text.splitlines():
        match = DEATH_RE.search(line)
        if not match:
            continue
        when = event_datetime(line, now)
        if when is None or not since <= when <= now + timedelta(minutes=1):
            continue
        pkg = match.group("pkg")
        if pkg in counts:
            counts[pkg] += 1
    return counts


def query_process_deaths() -> tuple[dict[str, int], str | None]:
    now = datetime.now().astimezone()
    since = now - timedelta(minutes=WINDOW_MINUTES)
    # Amazon supports both -S HH:MM and -S yesterday. Time filtering is
    # confirmed by parsing each log timestamp, even around midnight.
    since_arg = since.strftime("%H:%M") if since.date() == now.date() else "yesterday"
    try:
        logs = vda_shell("loggingctl", "log", "-p", "err", "-S", since_arg, timeout=20)
        return parse_process_deaths(logs, now), None
    except RuntimeError as exc:
        return {}, str(exc)


def build_baseline(osinfo: dict, apps: dict) -> dict:
    return {
        "os_build": osinfo["VERSION_NUMBER"],
        "apps": {label: data["version"] for label, data in apps.items() if data.get("version")},
    }


def report(osinfo: dict, apps: dict, apps_fresh: bool, baseline: dict | None,
           deaths: dict[str, int], log_error: str | None) -> None:
    osver = osinfo.get("OS_VERSION", "unknown")
    build = osinfo["VERSION_NUMBER"]
    desc = osinfo.get("BUILD_DESC", "")
    local(0, "Vega OS build", "-", f"Vega OS {osver}; build {build}; {desc}; availability of newer OTA versions NOT checked")
    if baseline is None:
        local(0, "Vega OS build changes", "-", "No baseline; run --accept-baseline to track future changes")
    elif baseline.get("os_build") != build:
        local(1, "Vega OS build changes", "-", f"Build changed: {baseline.get('os_build', 'unknown')} -> {build}; review and accept baseline")
    else:
        local(0, "Vega OS build changes", "-", f"Build {build} matches baseline")

    changed = []
    errors = []
    for label, pkg in APPS.items():
        data = apps.get(label, {})
        service_name = f"Vega app {label}"
        if not data.get("version"):
            local(3, service_name, "-", f"Cannot read package {pkg}: {data.get('error', 'missing information')}")
            errors.append(label)
            continue
        version = data["version"]
        enabled = data.get("enabled", "unknown").lower()
        freshness = "cached metadata is stale" if not apps_fresh else "metadata cached <=1h"
        status = 1 if enabled == "false" or not apps_fresh else 0
        local(status, service_name, "-", f"v{version}; enabled={enabled}; updated {data.get('updated') or 'unknown'}; {freshness}; NOT an appstore version comparison")
        if baseline is not None and baseline.get("apps", {}).get(label) != version:
            changed.append(f"{label}: {baseline.get('apps', {}).get(label, 'not tracked')} -> {version}")
    if baseline is None:
        local(0, "Vega app version changes", "-", "No baseline; run --accept-baseline to start version comparison")
    elif errors:
        local(3, "Vega app version changes", "-", f"Cannot compare all apps: {', '.join(errors)}")
    elif changed:
        local(1, "Vega app version changes", f"version_changes={len(changed)}", "Changes: " + "; ".join(changed[:3]) + "; review and accept baseline")
    else:
        local(0, "Vega app version changes", "version_changes=0", "Tracked app versions match baseline")

    if log_error:
        local(3, "Vega app process deaths", "-", f"Unable to read Vega logs: {log_error}")
    else:
        total = sum(deaths.values())
        state = 2 if total >= CRIT_DEATHS else 1 if total >= WARN_DEATHS else 0
        names = ", ".join(f"{label}={deaths.get(pkg, 0)}" for label, pkg in APPS.items() if deaths.get(pkg))
        local(state, "Vega app process deaths", f"process_deaths={total}",
              f"{total} lifecycle process-death event(s) in last {WINDOW_MINUTES} min" + (f"; {names}" if names else "") + "; not definitive proof of an app crash")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-baseline", action="store_true", help="Accept currently installed firmware and app versions as expected")
    parser.add_argument("--show-apps", action="store_true", help="Print tracked app versions, not Checkmk local lines")
    args = parser.parse_args()
    try:
        osinfo = parse_os_release(vda_shell("cat", "/etc/os-release"))
    except RuntimeError as exc:
        if args.accept_baseline or args.show_apps:
            parser.exit(1, f"Cannot retrieve OS: {exc}\n")
        local(3, "Vega OS build", "-", exc)
        local(3, "Vega OS build changes", "-", "No OS data")
        osinfo = None

    try:
        apps, fresh = collect_apps(force=args.accept_baseline or args.show_apps)
    except RuntimeError as exc:
        if args.accept_baseline or args.show_apps:
            parser.exit(1, f"Cannot retrieve apps: {exc}\n")
        apps, fresh = ({}, False)

    if args.show_apps:
        for label, pkg in APPS.items():
            data = apps.get(label, {})
            print(f"{label:16} {pkg:40} {data.get('version', 'UNAVAILABLE'):20} enabled={data.get('enabled', '?')}")
        return 0

    if args.accept_baseline:
        missing = [label for label in APPS if not apps.get(label, {}).get("version")]
        if missing:
            parser.exit(1, "Cannot accept incomplete version inventory: " + ", ".join(missing) + "\n")
        atomic_json_write(BASELINE_PATH, build_baseline(osinfo, apps))
        print(f"Baseline saved: OS {osinfo['VERSION_NUMBER']}, {len(APPS)} apps -> {BASELINE_PATH}")
        return 0

    try:
        baseline = json_read(BASELINE_PATH)
    except RuntimeError as exc:
        baseline = None
        print(f"Warning: {exc}", file=sys.stderr)
    deaths, log_error = query_process_deaths()
    if osinfo:
        report(osinfo, apps, fresh, baseline, deaths, log_error)
    else:
        # Still report independent app services, but don't pretend OS data is good.
        for label in APPS:
            data = apps.get(label, {})
            local(3, f"Vega app {label}", "-", f"OS unavailable; cached app version {data.get('version', '?')}")
        local(3, "Vega app version changes", "-", "OS unavailable")
        local(3, "Vega app process deaths", "-", log_error or "OS unavailable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
