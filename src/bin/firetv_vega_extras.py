#!/usr/bin/env python3
"""Read-only Vega OS app inventory and lifecycle checks for Checkmk 2.5.

Runs on the Checkmk SITE HOST, not on the Fire TV. Emits local-check records,
without the <<<local>>> header (the Checkmk data source wrapper adds it).

Usage as the Checkmk site user:
  python3 ~/local/bin/firetv_vega_extras.py
  python3 ~/local/bin/firetv_vega_extras.py --save-baseline
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from firetv_config import vda_settings, site_root, vda_client

VDA, DEVICE, CFG = vda_settings()
CLIENT = vda_client()
SITE = site_root()
BASELINE = SITE / "var" / "firetv_vega_packages_baseline.json"
PKG_RE = re.compile(r"\s*(?:[-*]\s*)?([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+)\s*")
CORE_COMPONENTS = (
    "com.amazon.minerva",
    "com.amazon.kcp.component.service",
)


def local(state, name, metrics, message):
    message = str(message).replace("\n", " ").replace("\r", " ")[:350]
    print(f'{state} "{name}" {metrics} {message}', flush=True)


def run_shell(*args):
    return CLIENT.shell(*args, timeout=20)


def packages_from(text):
    return sorted({match.group(1) for line in text.splitlines()
                   if (match := PKG_RE.fullmatch(line))})


def components_from(text):
    components = {}
    for line in text.splitlines():
        fields = line.split()
        # application, type, pid, id, state, lifespan, timeout, container...
        if len(fields) >= 6 and fields[0].startswith("com.") and fields[2].isdigit():
            components[fields[0]] = {"state": fields[4], "lifespan": fields[5]}
    return components


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-baseline", action="store_true",
                        help="Accept the current installed package list as expected")
    parser.add_argument("--list-packages", action="store_true",
                        help="Print the installed package IDs instead of check results")
    parser.add_argument("--diagnose", action="store_true",
                        help="Print safe VDA transport diagnostics; no connection changes")
    parser.add_argument("--list-components", action="store_true",
                        help="Print the running component states instead of check results")
    args = parser.parse_args()

    if args.diagnose:
        print("\n".join(CLIENT.diagnosis()))
        return

    if args.list_components:
        try:
            components = components_from(run_shell("vlcm", "list"))
            if not components:
                raise RuntimeError("vlcm returned no parseable components")
            for name, data in sorted(components.items()):
                print(f"{name:60} {data['state']:15} {data['lifespan']}")
        except RuntimeError as exc:
            parser.exit(1, f"Cannot list components: {exc}\n")
        return

    try:
        packages = packages_from(run_shell("vpm", "list", "packages"))
        if not packages:
            raise RuntimeError("vpm returned no parseable package IDs")
        if args.list_packages:
            print("\n".join(packages))
            return
    except RuntimeError as exc:
        if args.save_baseline:
            parser.exit(1, f"Baseline not saved: {exc}\n")
        local(3, "Vega package inventory", "-", exc)
        local(3, "Vega package changes", "-", "Cannot compare package inventory")
    else:
        if args.save_baseline:
            BASELINE.parent.mkdir(parents=True, exist_ok=True)
            os.umask(0o077)
            BASELINE.write_text(json.dumps({"packages": packages}, indent=2) + "\n")
            BASELINE.chmod(0o600)
            print(f"Saved {len(packages)} package IDs in {BASELINE}")
            return

        local(0, "Vega package inventory", f"packages={len(packages)}",
              f"{len(packages)} packages installed (includes system packages)")
        if BASELINE.exists():
            try:
                expected = set(json.loads(BASELINE.read_text())["packages"])
                added = sorted(set(packages) - expected)
                removed = sorted(expected - set(packages))
                if added or removed:
                    details = "; ".join(x for x in (
                        "added: " + ", ".join(added[:3]) if added else "",
                        "removed: " + ", ".join(removed[:3]) if removed else "",
                    ) if x)
                    local(1, "Vega package changes",
                          f"added={len(added)}|removed={len(removed)}",
                          f"Package inventory changed; {details}; run --save-baseline to accept")
                else:
                    local(0, "Vega package changes", "added=0|removed=0",
                          "Installed packages match saved baseline")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                local(3, "Vega package changes", "-", f"Invalid baseline: {exc}")
        else:
            local(0, "Vega package changes", "-",
                  "No baseline saved; run --save-baseline to enable changes alert")

    try:
        components = components_from(run_shell("vlcm", "list"))
        if not components:
            raise RuntimeError("vlcm returned no parseable running components")
    except RuntimeError as exc:
        local(3, "Vega running components", "-", exc)
        local(3, "Vega visible components", "-", "Cannot determine visible components")
        local(3, "Vega core components", "-", "Cannot check core component presence")
    else:
        visible = [name for name, val in components.items()
                   if val["state"].upper() == "VISIBLE"]
        permanent = [name for name, val in components.items()
                     if val["lifespan"].upper() == "PERMANENT"]
        local(0, "Vega running components",
              f"components={len(components)}|permanent={len(permanent)}",
              f"{len(components)} running components, {len(permanent)} permanent")
        local(0, "Vega visible components", f"visible={len(visible)}",
              "Visible: " + (", ".join(visible[:4]) if visible else "none reported") +
              "; visible does not guarantee video playback")
        missing = [name for name in CORE_COMPONENTS if name not in components]
        states = ", ".join(f"{name}={components[name]['state']}"
                           for name in CORE_COMPONENTS if name in components)
        local(1 if missing else 0, "Vega core components", "-",
              ("Missing: " + ", ".join(missing) + "; " if missing else "") + states)


if __name__ == "__main__":
    main()
