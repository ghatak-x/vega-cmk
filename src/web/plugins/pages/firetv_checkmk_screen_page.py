#!/usr/bin/env python3
"""Checkmk 2.5 authenticated Fire TV screenshot viewer with on-demand capture.

Install as (REPLACE the existing file; do not install alongside it):
  ~/local/share/check_mk/web/plugins/pages/firetv_checkmk_screen_page.py

Pages:
  firetv_screen.py       GET -> view saved PNG; POST + Checkmk CSRF -> capture then view
  firetv_screen_image.py GET -> return latest private PNG

Security: Checkmk's wato.edit permission, session-bound CSRF, POST-only action,
non-blocking file lock, a per-site cooldown, a subprocess timeout, no shell,
and no static/public screenshots. Opening the camera icon is read-only.
"""
from __future__ import annotations

import fcntl
import html as html_std
import json
import os
from datetime import datetime
from pathlib import Path
import subprocess
import time

from flask import session

from cmk.gui.http import response
from cmk.gui.log import logger
from cmk.gui.logged_in import user
from cmk.gui.pages import PageEndpoint, page_registry
from cmk.gui.utils.csrf_token import check_csrf_token

SITE = Path(os.environ.get("OMD_ROOT") or Path.home()).resolve()
PRIVATE = SITE / "var" / "firetv_private"
SCREENSHOT = PRIVATE / "now.png"
FOREGROUND = PRIVATE / "foreground_state.json"
CAPTURE_SCRIPT = SITE / "local" / "bin" / "firetv_snapshot_v2.py"
LOCKFILE = PRIVATE / ".web_capture.lock"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# Site-only config is not packaged, and must never be downloaded through web.
def _cooldown() -> int:
    try:
        config = SITE / "etc/firetv_vega.json"
        if config.is_symlink():
            return 20
        obj = json.loads(config.read_text(encoding="utf-8"))
        value = obj.get("screenshot_cooldown_seconds", 20)
        return value if type(value) is int and 1 <= value <= 3600 else 20
    except (OSError, ValueError, TypeError, AttributeError):
        return 20

COOLDOWN_SECONDS = _cooldown()
CAPTURE_TIMEOUT_SECONDS = 85  # Below Checkmk 2.5 request timeout (110s).


def _require_access() -> None:
    # Screenshot contents may be sensitive; use the same permission as v1.
    user.need_permission("wato.edit")


def _security_headers() -> None:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    # No scripts needed: keep the capture form plain HTML.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'self'"
    )


def _screenshot_metadata() -> tuple[bool, str, int, int]:
    try:
        st = SCREENSHOT.stat()
        if not SCREENSHOT.is_file():
            raise OSError("Not a regular file")
        dt = datetime.fromtimestamp(st.st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        return True, dt, max(0, int(time.time() - st.st_mtime)), st.st_mtime_ns
    except OSError:
        return False, "No screenshot saved yet", 0, 0


def _foreground_info() -> str:
    try:
        obj = json.loads(FOREGROUND.read_text(encoding="utf-8"))
        name = str(obj.get("label", "Unknown"))
        checked = float(obj.get("checked_at", 0))
        changed = float(obj.get("since", 0))
        # Do not represent an old reading as current playback.
        if checked <= 0 or time.time() - checked > 600 or checked > time.time() + 60:
            return f"Last reported foreground app: {name} (observation stale)"
        elapsed = max(0, int(checked - changed)) if changed else 0
        return f"Last observed foreground app: {name} (observed for {elapsed // 60} min at last check)"
    except (OSError, ValueError, TypeError, OverflowError):
        return "Foreground app: see the 'Vega screen activity' Checkmk service"


def _capture_fresh() -> tuple[bool, str]:
    """Execute exactly one existing snapshot script, with site-wide concurrency protection.

    Called only *after* admin permission and Checkmk CSRF checks, on POST.
    The CLI takes no user-controlled arguments. The script handles atomic replace,
    so unsuccessful captures preserve the previous screenshot.
    """
    if not CAPTURE_SCRIPT.is_file():
        return False, "Screenshot helper not installed; the previous image is unchanged."

    try:
        # This directory already exists from the working manual screenshot workflow.
        # Never turn a GUI action into an implicit change of its permissions.
        if PRIVATE.is_symlink() or not PRIVATE.is_dir():
            return False, "Screenshot storage is missing; capture manually first."
        fd = os.open(LOCKFILE, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+", encoding="ascii") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False, "A screenshot capture is already running. Please try again shortly."

            handle.seek(0)
            try:
                last_attempt = float(handle.read().strip())
            except ValueError:
                last_attempt = 0.0
            now = time.time()
            if 0 <= now - last_attempt < COOLDOWN_SECONDS:
                remaining = max(1, int(COOLDOWN_SECONDS - (now - last_attempt)) + 1)
                return False, f"Recently captured. Please retry in about {remaining} seconds."

            # Reserve the cooldown before launching VDA. Keep lock until done.
            handle.seek(0)
            handle.truncate()
            handle.write(f"{now:.6f}\n")
            handle.flush()

            try:
                result = subprocess.run(
                    ["/usr/bin/python3", str(CAPTURE_SCRIPT)],
                    capture_output=True,
                    text=True,
                    timeout=CAPTURE_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                logger.warning("Fire TV screenshot timed out after %s seconds", CAPTURE_TIMEOUT_SECONDS)
                return False, "Screenshot capture timed out; the earlier image has been kept."
            except OSError:
                logger.exception("Could not start Fire TV screenshot helper")
                return False, "Could not start screenshot capture; see Checkmk web.log."

            if result.returncode != 0:
                # Do not put VDA stderr (possibly sensitive) into the web page.
                logger.warning(
                    "Fire TV capture helper failed (exit %s); details redacted",
                    result.returncode,
                )
                return False, ("Screenshot capture failed; previous image preserved. "
                               "Active video playback, especially protected content, may block capture. "
                               "Try pausing the video and capturing again. "
                               "If it still fails, investigate the helper from the site terminal.")

            try:
                with SCREENSHOT.open("rb") as img:
                    if img.read(8) != PNG_MAGIC:
                        raise ValueError("Not a PNG")
            except (OSError, ValueError):
                logger.warning("Screenshot helper returned success without a valid PNG")
                return False, "The capture did not produce a valid PNG; see Checkmk web.log."

            return True, "Fresh screenshot captured successfully."
    except OSError:
        logger.exception("Fire TV screenshot lock/storage error")
        return False, "Capture storage is unavailable; see Checkmk web.log."


def page_firetv_screen_image(ctx) -> None:
    _require_access()
    # No mutating actions are exposed by this endpoint.
    if ctx.request.method != "GET":
        response.status_code = 405
        response.headers["Allow"] = "GET"
        response.set_content_type("text/plain")
        response.set_data("Method not allowed")
        return
    _security_headers()
    try:
        st = SCREENSHOT.stat()
        if st.st_size > MAX_IMAGE_BYTES or st.st_size < len(PNG_MAGIC):
            raise ValueError("Snapshot size invalid")
        data = SCREENSHOT.read_bytes()
        if len(data) != st.st_size or not data.startswith(PNG_MAGIC):
            raise ValueError("Snapshot is not a valid PNG")
    except (OSError, ValueError):
        response.status_code = 404
        response.set_content_type("text/plain")
        response.set_data("No valid private Fire TV screenshot is available.")
        return
    response.set_content_type("image/png")
    response.headers["Content-Disposition"] = 'inline; filename="firetv-now.png"'
    response.set_data(data)


def page_firetv_screen(ctx) -> None:
    _require_access()
    _security_headers()
    notice = ""
    notice_success = False
    if ctx.request.method == "POST":
        # This validates _csrf_token against the current Checkmk session. No
        # GET request, including a camera-icon click, can trigger capture.
        check_csrf_token()
        if ctx.request.form.get("action") != "capture":
            response.status_code = 400
            notice = "Unknown request; no screenshot was taken."
        else:
            notice_success, notice = _capture_fresh()
    elif ctx.request.method != "GET":
        response.status_code = 405
        response.headers["Allow"] = "GET, POST"
        response.set_content_type("text/plain")
        response.set_data("Method not allowed")
        return

    # Checkmk 2.5's native CSRF implementation uses this session value.
    token = html_std.escape(session.session_info.csrf_token, quote=True)
    exists, captured, age, version = _screenshot_metadata()
    if exists:
        image_url = f"firetv_screen_image.py?v={version}"
        image = (
            f'<a href="{image_url}" target="_blank" rel="noopener noreferrer" '
            'title="Open full-size screenshot">'
            f'<img src="{image_url}" alt="Last captured Fire TV screen" '
            'style="display:block;width:100%;height:auto;border-radius:10px;border:1px solid #485363">'
            '</a>'
        )
        details = f"Captured: {html_std.escape(captured)} (about {age // 60} min ago)"
    else:
        image = '<p role="status">No screenshot has been captured yet.</p>'
        details = "No saved capture"
    foreground = html_std.escape(_foreground_info())
    notice_html = (
        f'<p role="status" style="padding:10px 14px;border:1px solid '
        f'{"#518d6c" if notice_success else "#d99c62"};border-radius:8px">'
        f'{html_std.escape(notice)}</p>'
        if notice else ""
    )
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fire TV — private screenshot</title></head>
<body style="font-family:system-ui,sans-serif;max-width:1040px;margin:30px auto;padding:0 18px;background:#171c22;color:#edf0f3">
<h1>Fire TV — screenshot</h1>
<p style="color:#c2ccd5">{foreground}</p>
<p>{html_std.escape(details)}</p>
<form action="firetv_screen.py" method="post" style="display:inline-flex;gap:10px;align-items:center;margin:6px 0 20px">
  <input type="hidden" name="_csrf_token" value="{token}">
  <input type="hidden" name="action" value="capture">
  <button type="submit" style="border:0;border-radius:8px;padding:12px 20px;background:#2176ae;color:white;font-size:1rem;cursor:pointer;font-weight:650">📷 Capture now</button>
</form>
<a href="firetv_screen.py" style="margin-left:16px;color:#9bd4fa">Refresh viewer</a>
{notice_html}
{image}
<p style="color:#b6c2cb;line-height:1.5">Click the image to open full size. The camera icon and “Refresh viewer” only display the last saved screenshot; <b>Capture now</b> takes one new screenshot. This is not a live stream. Foreground visibility does not verify playback.</p>
</body></html>'''
    response.set_content_type("text/html")
    response.set_data(document)


# Preserve these endpoint IDs so the existing camera icon/rules remain valid.
page_registry.register(PageEndpoint("firetv_screen", page_firetv_screen))
page_registry.register(PageEndpoint("firetv_screen_image", page_firetv_screen_image))
