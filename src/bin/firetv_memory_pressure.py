#!/usr/bin/env python3
"""Read Linux memory PSI from Vega OS over VDA for a Checkmk local check.

This is independent of Checkmk's standard `Memory` service and never changes
its rules or thresholds. All six PSI averages are percentages (0..100):
`some` = time with at least one task stalled on memory,
`full` = time with all non-idle tasks stalled on memory.

The Checkmk data-source wrapper writes the <<<local>>> header. This collector
writes only its own local-check line; a missing or inaccessible PSI interface
is UNKNOWN rather than a false zero-pressure measurement. Set
`memory_pressure_enabled: false` in ~/etc/firetv_vega.json to omit the service
on devices that do not expose /proc/pressure/memory.

Offline verification: python3 firetv_memory_pressure.py --psi-file example.txt
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import re

SERVICE = '"Vega memory pressure"'
LINE = re.compile(
    r"^(some|full)\s+avg10=([0-9]+(?:\.[0-9]+)?)\s+"
    r"avg60=([0-9]+(?:\.[0-9]+)?)\s+"
    r"avg300=([0-9]+(?:\.[0-9]+)?)\s+total=([0-9]+)\s*$"
)


def parse_psi(raw: str) -> dict[str, dict[str, float]]:
    """Validate a complete memory PSI observation; never silently use 0."""
    observations: dict[str, dict[str, float]] = {}
    for line in raw.splitlines():
        match = LINE.fullmatch(line.strip())
        if not match:
            continue
        resource, avg10, avg60, avg300, _total = match.groups()
        if resource in observations:
            raise ValueError("Duplicate PSI resource")
        values = [float(avg10), float(avg60), float(avg300)]
        if not all(math.isfinite(value) and 0 <= value <= 100 for value in values):
            raise ValueError("PSI averages outside 0..100 range")
        observations[resource] = dict(zip(("10", "60", "300"), values))
    if set(observations) != {"some", "full"}:
        raise ValueError("Memory PSI some/full readings are missing or invalid")
    return observations


def render_psi(observations: dict[str, dict[str, float]]) -> str:
    metrics = []
    for kind in ("some", "full"):
        for window in ("10", "60", "300"):
            number = observations[kind][window]
            # Ad-hoc Checkmk metrics: no levels; finite percentage range 0..100.
            metrics.append(f"vega_mem_psi_{kind}_avg{window}_pct={number:.2f};;;0;100")
    some10 = observations["some"]["10"]
    full10 = observations["full"]["10"]
    return (f'0 {SERVICE} {"|".join(metrics)} '
            f'Memory stalls over 10s: some={some10:.2f}%, full={full10:.2f}%; '
            '10/60/300s averages graphed; informational, no alert thresholds')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psi-file", type=Path,
                        help="Offline test: use a saved PSI file, do not call VDA")
    opts = parser.parse_args()
    try:
        if opts.psi_file is not None:
            raw = opts.psi_file.read_text(encoding="utf-8")
        else:
            from firetv_config import load_config, vda_client
            cfg = load_config()
            if not cfg["memory_pressure_enabled"]:
                return 0  # Explicitly disabled; omit this local check.
            raw = vda_client().shell("cat", "/proc/pressure/memory", timeout=15)
        print(render_psi(parse_psi(raw)), flush=True)
    except (OSError, RuntimeError, ValueError):
        # Error is intentionally generic; never forward raw VDA output or IP.
        print(f'3 {SERVICE} - Memory PSI unavailable or invalid; '
              'check /proc/pressure/memory access or disable in site config', flush=True)
    return 0  # A local check always emits its status within the section.


if __name__ == "__main__":
    raise SystemExit(main())
