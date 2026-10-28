# Known issues and upstream observations

## Media Controls client discovery: native status 126

On a physical Fire TV Stick 4K Select running Vega OS 2.0 (build 2602000052920), a read-only Vega SDK 0.24.12112 React Native Media Controls probe successfully loaded `MediaControlComponentAsync`, constructed a locator and found `getMediaControlEndpoints()` was callable. The underlying native KCP active-clusters query failed with `status: 126`, cluster 1286, attribute `0x1217000c` and the JavaScript bundle threw `TypeError: undefined is not a function`. A similar failure was independently posted for a Vega virtual device, and an Amazon representative acknowledged investigating it.

Public discussion: https://community.amazondeveloper.com/t/vega-0-24-vvd-media-controls-client-discovery-fails-with-active-clusters-status-126/29205

A native return of 126 suggests an access-control problem in the KCP read, but does not prove precisely which missing permission or runtime restriction caused this retail-device failure. **No playback session/title API is used in this release.** We never claim a VISIBLE app is actively playing.

## Screenshot capture may fail during active Prime Video playback

On a physical Fire TV Stick 4K Select running Vega OS 2.0, the Checkmk
**Capture now** action sometimes returned `Fire TV capture helper failed (exit 1)`
while a Prime Video movie was playing. On the same device, **pausing the
movie allowed a fresh screenshot to succeed**. This is a reproducible
*observation*, not a confirmed diagnosis of the underlying Vega component.

Secure video surfaces / DRM restrictions and playback-related compositor
behavior are plausible causes, but the generic `exit 1` does not by itself
prove either mechanism. Screenshot availability can depend on the app,
content and playback state. Do not promise capture of protected video frames.

**User guidance:** leave the screenshot viewer open, pause the video manually
if appropriate, then click **Capture now** again after the configured cooldown.
The last successful screenshot is preserved on failure. If the capture still
fails while paused, run `python3 ~/local/bin/firetv_snapshot_v2.py` as the
Checkmk site user and investigate VDA and the screenshot helper separately.

**Privacy and non-interference:** the viewer must not automatically pause,
resume or control streaming playback. Do not attempt to bypass DRM or expose
screenshots through a public static URL. Log errors without raw video frames,
account names or unredacted VDA diagnostics.

## Checkmk GUI and portability

Legacy `local/share/check_mk/web/plugins/pages` endpoint registration currently works on Checkmk 2.5.0p17 but may be deprecated in future versions. MKP installation needs testing on a real site; some Checkmk release variants might reject our custom part structure or block the web plugin.

## Remote collector and distributed deployment scope

**Not implemented in v0.0.1:** installation on a central Checkmk site while VDA runs on a different host monitored only by a normal Linux agent on TCP 6556; remote VDA calls over SSH; remote screenshot capture/transfer; or per-device multi-target collection. The Checkmk site and VDA executable must share the same Linux host for the shipped data-source program and web viewer. A Fire TV at a remote location is only feasible when its developer endpoint is reachable privately from that host (cross-network routing has not been validated here).

A **complete remote Checkmk monitoring site** located beside the Vega SDK is a possible distributed-monitoring architecture, but has not been validated as part of this release. In particular, central-site routing of the authenticated screenshot viewer has not been tested. No SSH or TCP 6556 remote-collection implementation is included. See [Setup — supported deployment models](SETUP.md#supported-deployment-models-for-v001).

## Operational limitations

One Fire TV target per site; the VDA SDK must be installed separately and accessible to the site user. Direct VDA shell access is not guaranteed on all retail devices unless developer mode is enabled. Native Linux agent sections can reflect restricted/containerized Vega shell views, not the whole device. IP, SDK path and thresholds are configurable locally.

## VDA device-selection diagnostics: verify configured target first

On a second Checkmk test site, `vda devices` listed a connected TCP device,
but explicit `vda -s <target> shell ...` returned `device not found`.
**The incident was resolved by correcting a typo in the site-local JSON
`device` address.** It was not evidence of a confirmed upstream VDA defect.

If this occurs, compare `~/etc/firetv_vega.json` against the device identifier
reported by `vda devices`, including each IPv4 octet and TCP port, then run
`python3 ~/local/bin/firetv_vega_extras.py --diagnose`. Never copy a private
address into a public bug report or MKP. The shared VDA transport deliberately
avoids repeated connections when the configured target is already connected;
its guarded sole-TCP-device `-e` fallback remains defensive but does not fix
incorrect configuration. Avoid restarting a shared VDA daemon.

## Process visibility: `ps_lnx`, `ps` and `ace_memusage`

On a physical Fire TV Stick 4K Select with Vega OS 2.0, the Checkmk Linux
agent's `<<<ps_lnx>>>` section contained `[time]` / `[processes]` but an
empty `[header]`, so **no usable Linux process inventory** was available.
The developer-shell `ps -ef` and `top` showed only a handful of
`app_user` processes (developer-shell app and its children) while
`/proc/loadavg`, `/proc/stat` and `/proc/pressure/*` were available as
kernel-wide counters. The shell's `ps aux` returned `ps: bad aux` because
that syntax isn't supported by its implementation.

`ace_memusage` also returned only **three process rows** on this retail device:
`com.amazon.dev.shell:dev_shel`, `/bin/sh`, and `ace_memusage` itself,
followed by a separate `Total System Memory` summary. One early parser
incorrectly counted the numeric `Total` field as a fourth process; this
was a **diagnostic parser mistake**, not evidence of an extra process.
Invoking `ace_memusage -h` returned the same normal report, not usage/help
text, on the tested device. See Amazon's VDA 0.24 reference, which describes
a broader system-process output; the reason for the discrepancy is unknown:
https://www.developer.amazon.com/docs/vega/0.24/vda-tools

Do **not** treat the developer-shell process list as a complete system
inventory, and do not add system-wide process count, per-process CPU, or
process-RSS alert checks based on these incomplete views. `vlcm list` still
provides useful Vega component lifecycle/visible states and may expose PIDs,
but it does not substitute for the complete Linux process inventory.
An explicit upstream question about observed retail-device sandbox scope
would be reasonable; do not claim a confirmed Amazon defect.

## CPU load, CPU PSI and I/O PSI

In one playback-versus-Home observation, the Fire TV's 1-minute load remained
around 8 and CPU PSI `some` around 7–8% even after ten minutes on Home.
The independently sampled CPU busy fraction was about 29.4%, and recent I/O
and memory stall percentages were comparatively low. Load average, CPU usage,
and CPU PSI measure **different** aspects of scheduling. Restricted `ps`
prevents attributing this load to a particular process. This observation
**does not establish an OS performance bug** or prove Prime Video was the
cause. The additional informational PSI checks are informational only. Note that the
kernel defines system-wide CPU PSI `full` as unavailable (reported as 0):
https://www.kernel.org/doc/html/latest/accounting/psi.html

## Memory CRIT / duplicate filesystem displays

On some Vega shell views Checkmk's standard Linux `Memory` service evaluates
very high `Committed_AS` even when the recent PSI averages are zero. This is
not proof the alert is harmless; inspect available RAM, swap, PSI and error
logs. The MKP does **not** suppress memory thresholds.

The native Linux agent may report several filesystem mount paths backed by
`/dev/block/platform/userdata`. Checkmk legitimately discovers each path;
these do not necessarily represent independent storage pools. Optional
host-scoped discovery-rule cleanup is documented in [Monitoring](MONITORING.md).

## Prime Video ID verified on device

An on-device `vpm info com.amazon.frenchpress` reported the title **Prime
Video**, with component `com.amazon.frenchpress.main`. Prior shorthand
describing `frenchpress` as the Fire TV Home UI was incorrect. The display
name is configurable; see [Monitoring](MONITORING.md) for application identifiers and configuration.
