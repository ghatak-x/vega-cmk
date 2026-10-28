#!/usr/bin/env python3
"""Checkmk 2.5 local check: Vega foreground application and observed duration.

Designed for a Checkmk individual-program collector on the Ubuntu SITE host.
Read-only VDA shell -> `vlcm list`. Prints exactly one local-check record,
without the `<<<local>>>` header; firetv_agent already prints that header.

Privacy: only the last foreground-component set and timestamps are saved,
not a history of viewing, screenshots, titles or playback events.

Service: Vega screen activity
Existing: Vega visible components (from firetv_vega_extras.py) remains unchanged.

Important distinction: `VISIBLE` means foreground component, not video playback.
The duration is time since the first successful observation of this same
foreground component, NOT the exact instant the user opened the app.
After a monitoring gap >10min, continuity is not assumed and duration restarts.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from firetv_config import vda_settings, site_root, vda_client
import subprocess
import sys
import tempfile
import time

VDA, DEVICE, CFG = vda_settings()
CLIENT = vda_client()
SITE = site_root()
DEFAULT_STATE = SITE / "var/firetv_private/foreground_state.json"
GAP_LIMIT_SECONDS = CFG["foreground_gap_seconds"]  # 10 minutes, much longer than normal Checkmk polling
VDA_TIMEOUT_SECONDS = 15
SERVICE = '"Vega screen activity"'

# Prefixes are package/component IDs observed on Vega OS, not Android package IDs.
FRIENDLY_PREFIXES = {
    "com.netflix.kepler": "Netflix",
    "com.amazon.firetv.youtube": "YouTube",
    "com.zdf.android.mediathek": "ZDF",
    "com.zee5.amazon": "ZEE5",
    "com.amazon.music.kepler": "Amazon Music",
    "com.amazon.screensaver": "Screensaver",
    "com.amazon.frenchpress": "Prime Video",
    "com.amazon.smplighthouse.launcher": "Fire TV Home",
    "com.example.firetvmediaprobe": "Media Probe (diagnostic)",
}


def visible_components(vlcm_output: str) -> tuple[list[str], int]:
    """Parse the real columns: application type pid id state lifespan ...

    Ignore banner / header / VDA shell prompt; require a genuine component row.
    Return (visible component IDs, number of parsed component rows).
    """
    visible: set[str] = set()
    parsed = 0
    for line in vlcm_output.splitlines():
        fields = line.split()
        if (
            len(fields) >= 6
            and fields[0].startswith("com.")
            and fields[1] in {"I", "S", "T"}
            and fields[2].isdigit()
            and fields[3].isdigit()
        ):
            parsed += 1
            if fields[4].upper() == "VISIBLE":
                visible.add(fields[0])
    return sorted(visible), parsed


def friendly(component_id: str) -> str:
    # Site-specific monitored apps are authoritative; avoids hard-coding future
    # package ID changes, while known UI/system components keep fallback labels.
    for description, prefix in CFG["apps"].items():
        if component_id == prefix or component_id.startswith(prefix + "."):
            return description
    for prefix, description in FRIENDLY_PREFIXES.items():
        if component_id == prefix or component_id.startswith(prefix + "."):
            return description
    return component_id


def fetch_vlcm() -> str:
    return CLIENT.shell("vlcm", "list", timeout=VDA_TIMEOUT_SECONDS)


def load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, obj: dict) -> None:
    # Private directory shared with the working manually-captured screenshot.
    os.umask(0o077)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix=".foreground-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def make_observation(components: list[str], prior: dict, now: float) -> tuple[dict, int, bool]:
    """Maintain last-observed state only, with bounded continuity."""
    component = ", ".join(components) if components else "(none visible)"
    descriptions = ", ".join(dict.fromkeys(friendly(x) for x in components)) if components else "No visible component"
    try:
        previous_check = float(prior.get("checked_at", 0))
        previous_since = float(prior.get("since", now))
    except (ValueError, TypeError, OverflowError):
        previous_check, previous_since = 0, now

    # If we could not observe the device for ten minutes, we cannot assert it
    # stayed on the same app. Also guard against clock moves and malformed data.
    continuous = (
        prior.get("component") == component
        and previous_check > 0
        and 0 <= now - previous_check <= GAP_LIMIT_SECONDS
        and 0 <= previous_since <= previous_check
    )
    since = previous_since if continuous else now
    observed_seconds = max(0, int(now - since)) if components else 0
    observation = {
        "component": component,  # backward-compatible with screenshot viewer
        "label": descriptions,
        "since": since,
        "checked_at": now,
    }
    was_reset = prior.get("component") == component and previous_check > 0 and not continuous
    return observation, observed_seconds, was_reset


def one_line(value: object, max_length: int = 180) -> str:
    return str(value).replace("\n", " ").replace("\r", " ").replace('"', "'")[:max_length]


def render(components: list[str], observation: dict, duration: int, reset: bool) -> str:
    label = one_line(observation["label"])
    num = len(components)
    if not num:
        msg = "No visible component reported; playback state not available"
    else:
        since = datetime.fromtimestamp(observation["since"]).astimezone().strftime("%Y-%m-%d %H:%M")
        summary = f"Visible: {label}; first observed at {since}; observed for {duration // 60}m {duration % 60}s"
        if reset:
            summary += "; observation restarted after monitoring gap"
        msg = summary + "; playback not verified"
    return f'0 {SERVICE} visible_seconds={duration}|visible_components={num} {one_line(msg, 400)}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlcm-file", type=Path, help="Offline test: read vlcm output from file, not VDA")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE, help="State JSON location (for offline tests)")
    parser.add_argument("--now", type=float, help="Override epoch time (offline tests only)")
    opts = parser.parse_args()
    try:
        if opts.now is not None and opts.vlcm_file is None:
            raise RuntimeError("--now is permitted only with --vlcm-file")
        raw = opts.vlcm_file.read_text(encoding="utf-8") if opts.vlcm_file else fetch_vlcm()
        components, parsed = visible_components(raw)
        if not parsed:
            raise RuntimeError("vlcm list returned no valid component rows")
        now = time.time() if opts.now is None else opts.now
        if not (0 < now < 1e12):
            raise RuntimeError("Invalid observation time")
        old = load_state(opts.state_file)
        observed, seconds, reset = make_observation(components, old, now)
        save_state(opts.state_file, observed)
        print(render(components, observed, seconds, reset), flush=True)
    except (OSError, ValueError, OverflowError, RuntimeError) as exc:
        # Do not overwrite a prior successful observation on VDA failure.
        print(f'3 {SERVICE} - Unable to determine foreground app: {one_line(exc)}', flush=True)
    return 0  # Always emit a Checkmk local-check line rather than killing the agent.


if __name__ == "__main__":
    sys.exit(main())
