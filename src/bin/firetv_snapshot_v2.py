#!/usr/bin/env python3
"""Private, manual Fire TV Vega screenshot, with useful failure diagnostics.

Run as the Checkmk site user:
  python3 ~/local/bin/firetv_snapshot_v2.py
  python3 ~/local/bin/firetv_snapshot_v2.py --fixed-remote

Does not run in Checkmk polling or publish the screenshot anywhere.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from firetv_config import vda_settings, site_root, vda_client
import subprocess
import sys
import tempfile
import uuid

VDA, DEVICE, CFG = vda_settings()
CLIENT = vda_client()
SITE = site_root()
PRIVATE = SITE / "var" / "firetv_private"


def run(*args: str, cwd: Path | None = None, timeout: int = 35) -> subprocess.CompletedProcess:
    # `cwd` is retained for backward compatibility. The VDA helper receives
    # absolute destination paths, so there is no reliance on current directory.
    return CLIENT.run(*args, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Take one private Fire TV screenshot")
    parser.add_argument(
        "--fixed-remote", action="store_true",
        help="Use the known-working /tmp/firetv-screen.png path instead of a unique path",
    )
    args = parser.parse_args()

    os.umask(0o077)
    if PRIVATE.is_symlink():
        raise RuntimeError("Private screenshot directory may not be a symlink")
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(PRIVATE, 0o700)
    remote = "/tmp/firetv-screen.png" if args.fixed_remote else f"/tmp/firetv-snapshot-{uuid.uuid4().hex[:12]}.png"

    try:
        with tempfile.TemporaryDirectory(prefix="shot-", dir=PRIVATE) as temp_str:
            temp = Path(temp_str)
            if args.fixed_remote:
                # Prevent accidentally pulling a stale screenshot after a failed capture.
                run("shell", "rm", "-f", remote)
            try:
                run("shell", "gwsi-tool-screenshooter", remote)
            except RuntimeError as exc:
                try:
                    detail = run("shell", "ls", "-l", remote).stdout.strip()
                except RuntimeError:
                    detail = "No screenshot file was created on the Fire TV"
                raise RuntimeError(f"{exc}\n  remote file: {detail}") from exc

            # Use an explicit destination to avoid depending on shell working directory.
            local = temp / "captured.png"
            run("pull", remote, str(local))
            if not local.is_file() or local.stat().st_size < 8:
                raise RuntimeError("Screenshot file missing or unexpectedly small")
            with local.open("rb") as f:
                if f.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise RuntimeError("Screenshot is not a PNG; preserving earlier now.png")
            os.chmod(local, 0o600)
            os.replace(local, PRIVATE / "now.png")
            print(f"Saved current screenshot: {PRIVATE / 'now.png'}")
            print("Private file (0600); replaces the previous screenshot")
        return 0
    finally:
        # Deletion failure must not mask a successful or failed capture.
        try:
            run("shell", "rm", "-f", remote, timeout=10)
        except RuntimeError as exc:
            print(f"Note: could not remove remote temporary screenshot: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Screenshot failed:\n{exc}", file=sys.stderr)
        raise SystemExit(1)
