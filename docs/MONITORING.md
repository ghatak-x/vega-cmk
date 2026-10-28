# Monitoring guide: memory, PSI, filesystems, alerting and runtime

## Standard Memory service (unchanged by this MKP)

This MKP **does not override, disable or change** Checkmk's standard Linux
`Memory` service, its committed-memory limits, thresholds, or state. An observed
CRIT state can be informative even if it does not mean resident RAM is exhausted.

On one Vega OS 2.0 development device, for example, the kernel reported:

| `/proc/meminfo` / PSI counter | Observation |
| --- | ---: |
| `MemTotal` | 998540 kB (~975 MiB) |
| `MemAvailable` | 292688 kB (~286 MiB) |
| `SwapTotal` | 1048572 kB (~1024 MiB) |
| `SwapFree` | 802628 kB (~784 MiB) |
| `Committed_AS` | 6102252 kB (~5.82 GiB) |
| `CommitLimit` | 1547840 kB (~1.48 GiB) |
| `vm.overcommit_memory` | 0 (heuristic overcommit) |
| `vm.overcommit_ratio` | 50 |
| PSI some/full 10s, 60s, 300s | All 0.00% at that observation |

`Committed_AS` represents virtual memory commitment, **not** resident memory.
Linux overcommit policy `0` permits commitments that exceed the displayed
`CommitLimit`; this may trigger committed-memory thresholds in Checkmk while
`MemAvailable` and recent memory-stall measurements remain reasonable. Swap
allocated/used is not the same as active swapping or sustained memory pressure.
A zero PSI average is a *recent* observation, not a guarantee that pressure
will never occur, or that all applications are healthy.

To inspect the Fire TV's agent memory counters as the Checkmk site user:

```sh
cmk -d <CHECKMK_HOST> | awk '
  /^<<<mem>>>/ { inside=1; next }
  /^<<</ { if (inside) exit }
  inside && /^(MemTotal|MemAvailable|SwapTotal|SwapFree|Committed_AS|CommitLimit):/ { print }
'
```

To inspect overcommit policy and PSI with the configured VDA client:

```sh
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'local/bin'))
from firetv_config import vda_client
client = vda_client()
for path in ('/proc/sys/vm/overcommit_memory', '/proc/pressure/memory'):
    try:
        print(path, '\n' + client.shell('cat', path, timeout=15).strip())
    except RuntimeError:
        print(path, 'unavailable')
PY
```

**Optional tuning (not enabled by the MKP):** If an administrator determines
that the committed-memory alarm is unsuitable for their particular Vega
workload, use Checkmk's *Parameters for this service* / corresponding Linux
memory ruleset and restrict any changes to the Fire TV host. Assess physical
memory availability, swap, OOM-related events and PSI together before adjusting
committed-memory levels. It is equally valid to leave the Checkmk Memory CRIT
untouched, as on the original test device. Do not alter the kernel's overcommit
policy to suppress a monitoring alert.

## Vega memory pressure

`Vega memory pressure` runs a **read-only** VDA shell command:

```
cat /proc/pressure/memory
```

This is *independent* of the Checkmk `Memory` service. Linux PSI reports the
percentage of time tasks are stalled waiting for memory:

* `some`: at least one task is stalled on memory;
* `full`: all non-idle tasks are stalled simultaneously.

For each category the service emits 10-, 60- and 300-second average percentages
(six **graphable performance metrics**). Its local-check status is always **OK**
when valid values are collected; **no WARN or CRIT thresholds are added**.
An invalid or inaccessible PSI interface produces **UNKNOWN**, never a made-up
`0.00` reading. Some devices or shell sandboxes may not expose PSI at all.

To disable just this service on unsupported devices, add to the site-local
`~/etc/firetv_vega.json` (JSON boolean, not a quoted string):

```json
"memory_pressure_enabled": false
```

Re-enable with `true`; rescan for service changes. This controls only the new
PSI check, **not** the standard Memory service. The default is `true`.
A normal result resembles:

```
0 "Vega memory pressure" vega_mem_psi_some_avg10_pct=0.00;;;0;100|... Memory stalls over 10s: some=0.00%, full=0.00%; ...
```

The six graph metrics all carry a `_pct` suffix and a range 0–100. They are
reported as informational metrics, not a configurable Checkmk ruleset.
Future revisions can add thresholds based on observed workload baselines, but
none are imposed in this version.

Linux kernel PSI documentation: https://www.kernel.org/doc/html/latest/accounting/psi.html
Checkmk local check format: https://docs.checkmk.com/latest/en/localchecks.html

## CPU and I/O pressure

The `firetv_resource_pressure.py` collector reads `/proc/pressure/cpu` and
`/proc/pressure/io` via two read-only VDA shell calls. CPU and I/O are
**separate Checkmk services**; if one is missing or invalid only that
service becomes UNKNOWN. A successful check is OK/informational regardless
of the measured percentages; **no warning or critical levels are introduced**.

* **Vega CPU pressure:** `some` PSI 10/60/300-second averages (**3 metrics**).
  This shows the share of time at least one task was waiting for CPU service;
  it is **not CPU utilization**. The Linux kernel's system-wide `cpu full`
  value is undefined (reported as 0 for compatibility), so it is intentionally
  excluded rather than graphed as meaningful zero pressure.
* **Vega I/O pressure:** `some` and `full` 10/60/300-second PSI averages
  (**6 metrics**). `some` indicates one or more tasks waiting on I/O;
  `full` indicates that all non-idle tasks are stalled on that resource.
* Both sets are independent of the native CPU/Load/Disk I/O and Memory
  services. They do not change the kernel or any native agent check.

Test the two checks without service discovery:

```sh
python3 ~/local/bin/firetv_resource_pressure.py
cmk -d <CHECKMK_HOST> | grep -E '"Vega (CPU|I/O|memory) pressure"'
```

If your Vega device doesn't expose one of these PSI resources, set the
appropriate site-local boolean in `~/etc/firetv_vega.json` and rescan:

```json
"cpu_pressure_enabled": false,
"io_pressure_enabled": false
```

Both default to `true`. These are individual switches; an unsupported I/O
PSI file does **not** require disabling CPU or memory PSI. JSON needs a comma
between adjacent properties. See [Setup](SETUP.md) for installation and upgrade instructions.

A real example measured about 7–8% CPU `some` PSI with load average near 8,
but CPU utilization of about 29.4%, minimal recent I/O pressure and low memory
PSI. This is **not** proof of a Prime Video fault: the load and CPU PSI also
persisted after switching to the Home screen. Investigate over time before
setting alert thresholds. Process visibility through the Vega developer shell
is limited (see [Known Issues](KNOWN_ISSUES.md)).

Kernel documentation: https://www.kernel.org/doc/html/latest/accounting/psi.html

## Filesystem duplicates from Vega mount namespaces

`<<<df_v2>>>` from the Vega developer shell can report different mount points
that all use the *same* `/dev/block/platform/userdata` block filesystem.
On a test device, these included `/data`, `/cache`, `/dm-cache`, `/var/log`,
`/var/lib/developer`, `/pkgs/com.amazon.dev.shell` and several internal paths.
The repeated **72% used** figures therefore do **not** describe ten independent
storage devices. Also note that `tmpfs` mounts are separate memory-backed
filesystems and should not be conflated with userdata.

Verify the mount sources before changing any monitoring:

```sh
cmk -d <CHECKMK_HOST> | awk '
  /^<<<df_v2>>>/ { inside=1; next }
  /^<<</ { if (inside) exit }
  inside {print}
' | head -50
```

**Optional cleanup (not performed by MKP):** In Checkmk go to
**Setup → Services → Discovery rules → Disabled services**, create a rule
restricted to your Vega host and **only** the redundant filesystem service
descriptions you have verified. Keep a representative `/data` filesystem
and any independent mounts you care about. Rescan, accept the resulting service
changes and activate. Never use a global Disabled services rule for this.
The package does **not** automatically suppress mounts or remove checks.

## Screenshot viewer and active playback

The optional **Capture now** viewer can fail while a protected Prime Video
movie is playing, even when a capture works after manually pausing the movie.
This has been observed on one physical Vega OS 2.0 Fire TV Stick; the exact
native cause is **not** verified. The screenshot tool is not a supported media
session API and no workaround for protected playback is guaranteed. The viewer
keeps the last successful screenshot on failure and never pauses/resumes media
on its own. See [Known Issues](KNOWN_ISSUES.md) for troubleshooting.

## Other informational checks

* Zero-unit systemd service/socket summaries may reflect a restricted
  developer shell rather than an unhealthy system. Consider host-scoped
  disable rules if they add no value; the MKP does not change these services.
* A `VISIBLE` Vega component indicates the foreground app only, **not**
  playback. The duration check measures continuous observations with a
  configurable gap reset, not exact viewing time.
* App process-death messages should be correlated with log times, repeated
  occurrences and observed application behavior. Lifecycle termination alone
  is not proof of a user-facing crash.
* The wrapper pushes the native Linux agent to device `/tmp` during polls.
  Measure Checkmk agent execution duration and avoid very frequent polling.
* Collecting application names and foreground app in Checkmk history can
  reveal viewing patterns. Restrict access and notifications accordingly.

---

## Customizing application IDs without editing the MKP

Vega app IDs can vary across devices, regions, releases and variants. **Do not
hard-code an ID into the package source or assume one will never change.**
Configure monitored applications for each Checkmk site in the private JSON
file `~/etc/firetv_vega.json`, not in files under `~/local/bin/`.

## Identify packages from the device

As the Checkmk site user, after validating VDA connectivity:

```sh
python3 ~/local/bin/firetv_vega_extras.py --list-packages
python3 ~/local/bin/firetv_vega_extras.py --list-components
```

The first command prints installed *package IDs*. The second shows component
IDs with states such as `VISIBLE`. A component typically has a suffix like
`.main` and must not be confused with the package ID used for version checks.

To confirm a candidate package's **official on-device title and version**:

```sh
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'local/bin'))
from firetv_config import vda_client
raw = vda_client().shell('vpm', 'info', 'com.amazon.frenchpress', timeout=15)
for line in raw.splitlines():
    if line.startswith(('id:', 'version:', 'build number:', 'enabled:')):
        print(line)
PY
```

The full `vpm info` record may include UUIDs, tracking identifiers, installer
metadata and signing certificates. **Avoid publishing unredacted output**.

### Example: Prime Video on a confirmed Vega OS 2.0 device

```
Package ID:    com.amazon.frenchpress
Component ID:  com.amazon.frenchpress.main
App title:     Prime Video
Version:       25.2.58189  (example; it will change)
```

The on-device `vpm info` title established that this component is **Prime
Video**, not a generic Fire TV Home app. `VISIBLE` still does not confirm a
particular media title or playback state.

## Update a site mapping

Edit `~/etc/firetv_vega.json` (must remain mode 0600) and change/add the
`apps` mapping. Example:

```json
"apps": {
  "Netflix": "com.netflix.kepler",
  "YouTube": "com.amazon.firetv.youtube",
  "Prime Video": "com.amazon.frenchpress"
}
```

The example is a subset: keep your other configured apps, such as ZDF, ZEE5
and Amazon Music. Keys are human-readable labels, values are **package IDs**.
The package accepts up to 20 label-to-package pairs. A changed identifier is
picked up at the next Checkmk data-source execution; no reinstallation or
Python editing is required. The **Vega screen activity** friendly labels
prioritize this same mapping and fall back to built-in labels for known
system components. New or unknown component IDs are shown literally until
mapped.

Validate and inspect monitored app metadata:

```sh
python3 -m json.tool ~/etc/firetv_vega.json >/dev/null
chmod 600 ~/etc/firetv_vega.json
python3 ~/local/bin/firetv_vega_deep.py --show-apps
```

When the package ID changes, check the newly reported version against the
actual app and decide whether the current installed version is an acceptable
new baseline. **Only then** accept a new baseline:

```sh
python3 ~/local/bin/firetv_vega_deep.py --accept-baseline
```

This replaces the saved app-version and OS-build baseline. Do not repeatedly
accept baselines just to suppress unexpected change alerts. Adding or changing
`apps` does not rewrite the installed-package inventory baseline. After adding
new app labels, use Checkmk service discovery to accept newly discovered
`Vega app ...` services; removing labels can remove those checks after rescan.

## OS and installed-package baselines

```sh
python3 ~/local/bin/firetv_vega_extras.py --save-baseline
python3 ~/local/bin/firetv_vega_deep.py --accept-baseline
```

The first baseline records **all installed package IDs**, not just the selected
apps. The second records the OS build and versions of selected apps.
The baselines are saved under the site `~/var/` and must **never** be bundled
into the MKP, shared in public archives, or committed to a public repository.

To find the cause of a newly installed package count before updating the first
baseline, compare the current package list with the private saved baseline.
For example, our optional development-only Media Probe adds its own package
ID; that is not necessarily an unexpected system update.

