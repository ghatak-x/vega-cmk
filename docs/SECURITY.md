# Security and data handling

## Data flows

The **Checkmk site and VDA executable run on the same Linux host** in v0.0.1. The site invokes the Vega SDK VDA executable to communicate with the Fire TV on the configured private TCP endpoint. The wrapper reads device OS and package metadata, process-life-cycle error counts, and foreground component IDs. The native Checkmk Linux agent is pushed to `/tmp` and run inside the restricted device shell. None of our code includes analytics endpoints, tracking SDKs, cloud upload calls or external HTTP requests.

That **does not assert** the Checkmk product, Vega SDK, or surrounding systems never make independent network requests. Device developer mode and Checkmk's notifications, backups, remote sites and user accounts all affect the privacy threat model.

## Sensitive data

* `~/var/firetv_private/now.png`: actual TV screen, potentially containing account identifiers, viewing recommendations or private material. File mode 0600; directory mode 0700. **Never package** it.
* `~/var/firetv_private/foreground_state.json`: only most recent app and timestamps; no append-only viewing history. Mode 0600.
* `~/var/firetv_deep/`: app metadata cache and firmware/app version baseline; mode 0700, baseline mode 0600.
* `~/var/firetv_vega_packages_baseline.json`: installed package inventory baseline. This first prototype stored it using the current site default permissions; check it and harden with `chmod 600`.
* `~/etc/firetv_vega.json`: device address, installed SDK path, optional app list; owner-only mode 0600. Do not upload/share it.
* Checkmk performance histories and incident notifications record app and device status beyond the last foreground state, even if the extension's own JSON state does not.

## Viewer permissions

The screenshot HTML and PNG endpoints require `wato.edit` and respond with no-store, no-sniff and same-origin headers. Capture is a POST with Checkmk session CSRF token. The command is a fixed Python helper with no shell or user-controlled arguments, a timeout, concurrency lock and cooldown. Screenshots are not stored below public web htdocs. Important: administrators with `wato.edit`, the Checkmk site OS user and server administrators can access images.

## Network topology and unsupported remote modes

This release assumes a **site-local VDA executable**, not an SSH or TCP-6556 proxy. Opening a normal Linux Checkmk agent connection (typically TCP 6556) to a remote VDA host does not forward arbitrary VDA shell commands or capture requests. The Fire TV's developer TCP endpoint (typically 5555) must be privately reachable **from the Checkmk site/VDA host**. The VDA daemon's host-local port (typically 5037) must not be exposed over the network. Use tightly controlled firewall rules, trusted private networks or a VPN where required.

Remote collectors using agent plug-ins/piggyback, remote execution over SSH, and remote screenshot transfer are **not security-reviewed or included** in this package. Do not treat the screenshot endpoint as a network API to expose outside Checkmk. A distributed Checkmk deployment with a complete site beside VDA is possible in principle, but site-specific authentication, screenshot URLs and access control would require independent validation.

## Risks and residual issues

* **VDA TCP developer port:** direct system-debug capability; do not expose to internet or untrusted LAN; prefer firewalls and turn off developer access when finished.
* **GUI compatibility:** `web/plugins/pages` is a legacy Checkmk extension path. Re-evaluate after Checkmk updates.
* **Local code execution:** installing an MKP executes Python as the Checkmk site account; review source and restrict who can enable packages.
* **Host labels/agent data:** Linux agent may expose process, mount and network details; apply Checkmk access controls.
* **Screenshot contents:** content protected by streaming DRM may be blank. We do not OCR or inspect content.
* **No supply-chain guarantee:** package was assembled locally from our source and tested offline; no independent external security audit or signatures.

## Hardening checklist

Set `chmod 600 ~/etc/firetv_vega.json` and `chmod 600 ~/var/firetv_vega_packages_baseline.json` (if present), limit Checkmk administration to trusted users, use HTTPS for any non-local Checkmk access, avoid raw logs in bug reports, exclude screenshot/state directories from public archives, and check file permissions after MKP activation.

## Memory-pressure metrics

`Vega memory pressure` reads only aggregate kernel memory PSI averages via
`/proc/pressure/memory`. It does not read application content, process names,
screen frames or per-user memory usage. Its six percentage metrics may be
stored in Checkmk historical performance data or notifications. The default
behavior is informational; absence of PSI returns UNKNOWN, not a fake zero.
Checkmk's standard Memory and filesystem checks are **not changed**.

Documentation embedded in the MKP at `~/local/bin/firetv_docs/` contains only
public templates and generalized examples, never live `~/etc` configuration,
actual images or per-device baselines. Inspect installed package files before
sharing them, and never add private site state to public archives.

## CPU and I/O pressure metrics

The additional `firetv_resource_pressure.py` collector reads only the
kernel's `/proc/pressure/cpu` and `/proc/pressure/io` counters. It emits
numerical aggregate percentages as Checkmk metrics, without process names,
usernames, PIDs, or device IP addresses. PSI is observational and does not
alter a device's memory/CPU scheduling or storage behavior. If VDA fails,
raw command output and stderr are not included in service messages.

The developer-shell `ps` and `ace_memusage` inventory is incomplete on the
retail device tested; it is **not** automatically pushed into monitoring.
Do not collect or publish unredacted process command lines or snapshots to
diagnose a restricted namespace. The standard Checkmk Linux Memory and
filesystem monitoring remains unchanged.

---

## Why use VDA instead of ordinary network checks?

## Intended audience

This is a **development and QA integration**, not a recommended permanent setting for every home Fire TV. Developer Mode and TCP VDA expose privileged diagnostic interfaces; the benefit must justify leaving them enabled. The current experimental release supports **one target per Checkmk site**, not fleet orchestration.

## Decision guide

| Goal | Need VDA? | Practical alternative |
| --- | --- | --- |
| Is the stick online? | No | Checkmk ping / active network checks |
| Is the streaming app reachable? | Maybe | Test externally observable network service, if one exists |
| OS build and package versions | Yes, for this collector | Device UI / manual SDK commands |
| Inspect Vega process-death events | Yes | App's own exported health telemetry, when available |
| Foreground component and observed duration | Yes | Not available from ordinary ICMP monitoring |
| One authenticated screenshot for QA triage | Yes | Manual developer screenshot tooling |
| Actual movie title / play/pause status | Not supported in this release | Not available through the tested Media Controls API on this device |

## Checkmk value proposition

- **Continuity**: turn occasional VDA CLI diagnostics into timestamped service health, historical metrics, state changes, notifications and dashboards.
- **QA regression signals**: correlate OS/app version changes with an increase in process deaths or resource pressure after an update. A process death alone is not proof of a crash; use counts/context.
- **Faster triage**: the Checkmk service and protected screenshot viewer provide device context without repeatedly invoking CLI tools. No automatic screenshots.
- **Reusable collaboration**: a documented Checkmk extension lets a team install the same checks with private per-site configuration, test changes, and remove them consistently.

## Costs and boundaries

- Developer Mode must remain enabled and VDA TCP/IP access reachable. Restrict it to a trusted development LAN or controlled host.
- On every Checkmk agent collection, the wrapper confirms VDA connectivity when needed, pushes the Linux agent into device `/tmp`, runs it, and queries Vega APIs. This is **not passive monitoring**. Tune check interval so diagnostics don't disturb TV use; start at a multi-minute interval and profile overhead.
- Some system counters represent the **restricted Vega developer shell** view, not the full platform. Be careful interpreting memory, CPU and mounts.
- `VISIBLE` means foreground, **not verified playback**. The Media Controls probe returned status 126 while discovering endpoints; do not claim title or playback-state monitoring.
- Screen contents, installed apps, logs and timestamps can be sensitive. Store the screenshot privately; do not activate automatic capture or forward image data into notifications.
- For a normal home device that doesn't already need debugging, Checkmk's basic reachability check is usually the more sensible, lower-risk option.

## Product roadmap before broad deployment

Move from a single site-wide device configuration to per-host rules (special agent or dedicated datasource), implement low-overhead caching/rate limiting, add hardening and a documented supported-device matrix, and verify Checkmk GUI and MKP compatibility across patch releases. Only consider a consumer-facing approach after finding a supported narrow-permission interface that does **not** require broad developer mode.

