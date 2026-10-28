#!/usr/bin/env python3
"""Informational Vega OS Linux CPU and I/O PSI local checks.

Reads /proc/pressure/cpu and /proc/pressure/io via the configured VDA shell.
Does not change any existing Checkmk checks or their thresholds.

CPU PSI 'full' at the system level is undefined and therefore excluded; only
'some' 10/60/300s averages are exported for CPU. For I/O, both 'some' and
'full' averages are exported. Any unavailable/malformed resource produces its
own UNKNOWN check instead of a misleading OK zero. No automatic alert levels.

Offline test: python3 firetv_resource_pressure.py --cpu-file cpu.txt --io-file io.txt
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import re

PATTERN = re.compile(
    r"^(some|full)\s+avg10=([0-9]+(?:\.[0-9]+)?)\s+"
    r"avg60=([0-9]+(?:\.[0-9]+)?)\s+"
    r"avg300=([0-9]+(?:\.[0-9]+)?)\s+total=([0-9]+)\s*$"
)
WINDOWS = ("10", "60", "300")


def parse_psi(raw: str, resource: str) -> dict[str, dict[str, float]]:
    """Parse documented CPU or I/O PSI data with strict required fields."""
    if resource not in {"cpu", "io"}:
        raise ValueError("Unrecognized PSI resource")
    result: dict[str, dict[str, float]] = {}
    for line in raw.splitlines():
        match = PATTERN.fullmatch(line.strip())
        if match is None:
            continue
        kind, avg10, avg60, avg300, _total = match.groups()
        if kind in result:
            raise ValueError("Duplicate PSI entry")
        values = tuple(float(s) for s in (avg10, avg60, avg300))
        if not all(math.isfinite(v) and 0 <= v <= 100 for v in values):
            raise ValueError("PSI average out of range")
        result[kind] = dict(zip(WINDOWS, values))
    required = {"some", "full"} if resource == "io" else {"some"}
    if not required.issubset(result):
        raise ValueError("Missing required PSI entries")
    return result


def render_psi(data: dict[str, dict[str, float]], resource: str) -> str:
    """Render a single valid Checkmk local-check line with percent metrics."""
    if resource == "cpu":
        kinds = ("some",)
        service = "Vega CPU pressure"
        summary = f'CPU wait 10s: some={data["some"]["10"]:.2f}%'
    elif resource == "io":
        kinds = ("some", "full")
        service = "Vega I/O pressure"
        summary = (f'I/O stalls 10s: some={data["some"]["10"]:.2f}%, '
                   f'full={data["full"]["10"]:.2f}%')
    else:
        raise ValueError("Unrecognized PSI resource")
    metrics = [
        f'vega_{resource}_psi_{kind}_avg{window}_pct={data[kind][window]:.2f};;;0;100'
        for kind in kinds for window in WINDOWS
    ]
    return (f'0 "{service}" {"|".join(metrics)} {summary}; '
            '10/60/300s averages graphed; informational, no alert thresholds')


def failure(resource: str) -> str:
    service = "Vega CPU pressure" if resource == "cpu" else "Vega I/O pressure"
    return (f'3 "{service}" - {resource.upper()} PSI unavailable or invalid; '
            f'check /proc/pressure/{resource} access or disable in site config')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-file", type=Path, help="Offline test CPU PSI file")
    parser.add_argument("--io-file", type=Path, help="Offline test I/O PSI file")
    opts = parser.parse_args()

    if opts.cpu_file is not None or opts.io_file is not None:
        # Offline test mode reads files only, never contacts the Fire TV.
        config = {"cpu_pressure_enabled": opts.cpu_file is not None,
                  "io_pressure_enabled": opts.io_file is not None}
        client = None
    else:
        from firetv_config import load_config, vda_client
        config = load_config()
        client = None  # Lazy: don't connect if both checks are disabled.

    for resource, key, input_path in (
        ("cpu", "cpu_pressure_enabled", opts.cpu_file),
        ("io", "io_pressure_enabled", opts.io_file),
    ):
        if not config[key]:
            continue
        try:
            if input_path is not None:
                raw = input_path.read_text(encoding="utf-8")
            else:
                if client is None:
                    client = vda_client()
                raw = client.shell("cat", f"/proc/pressure/{resource}", timeout=15)
            print(render_psi(parse_psi(raw, resource), resource), flush=True)
        except (OSError, RuntimeError, ValueError):
            # Never return raw stderr: may include private addresses or internals.
            print(failure(resource), flush=True)
    return 0  # Checkmk local-check exit status is carried in each check line.


if __name__ == "__main__":
    raise SystemExit(main())
