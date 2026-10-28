# Installation, prerequisites and upgrade

This guide assumes a **trusted development/QA network**, a Linux Checkmk site host **with Vega SDK/VDA installed on that same host** and one Fire TV running **Vega OS**, not an Android-based Fire OS device. Test on a separate Checkmk site first; this package does not install or configure the Amazon SDK.

## Supported deployment models for v0.0.1

**The current MKP runs VDA on the Checkmk site machine.** The site executes `~/local/bin/firetv_agent` as an *individual program call*, and that script invokes a VDA executable on **the same operating-system host**, using the site-local `~/etc/firetv_vega.json`. The `firetv_snapshot_v2.py` helper and Checkmk screenshot viewer also run there. Nothing in this release remotely invokes a collector installed on another Linux server.

```text
TESTED:                 Linux host A                        Fire TV (Vega OS)
                        Checkmk site + SDK/VDA    -- TCP 5555 --> developer endpoint
                        | local Python, site JSON and private screenshot storage |

POSSIBLE, NOT TESTED:   Linux host A -- trusted VPN / routed private network --> Fire TV
                        Checkmk site + SDK/VDA                  TCP 5555

NOT PROVIDED:           Checkmk site A -- TCP 6556 --> Linux agent B + VDA -- TCP 5555 --> Fire TV
                        (requires a new remote agent plug-in and piggyback collector)

NOT PROVIDED:           Checkmk site A -- SSH --> Linux VDA host B -- TCP 5555 --> Fire TV
                        (requires a new SSH transport and secure screenshot retrieval)
```

| Scenario | What to do in this release |
| --- | --- |
| Local Checkmk site and VDA | Install this MKP and the Vega SDK **on the same Linux host**. Configure the host-specific individual program call as below. **Only this layout was tested end-to-end.** |
| Fire TV on another subnet/location | Keep VDA on the Checkmk site host. Establish private routing or a VPN to the Fire TV's VDA developer port; verify `vda devices` and a harmless read-only VDA shell command. The cross-network topology itself has **not been validated**. |
| Distributed Checkmk with a complete *remote monitoring site* near the Fire TV | A remote Checkmk **site** (not just a Linux agent) may run the current MKP locally alongside VDA and report monitoring data through Checkmk distributed monitoring. This arrangement has **not** been integration-tested; GUI screenshot routing from a central site is not validated. |
| Ordinary Linux **Checkmk agent** on a remote VDA host, reporting to a central site on TCP 6556 | **Unsupported.** Port 6556 transports agent data. Merely installing our MKP on the central site does **not** copy its Python scripts to the agent host or execute VDA there. A remote agent plug-in/piggyback implementation would be needed. |
| Remote VDA host accessed with SSH | **Unsupported out of the box.** The current individual program command doesn't invoke SSH. A customer-created SSH wrapper is outside this package's tested contract, especially for screenshot capture, credentials and timeouts. |

**Ports:** VDA commonly uses **TCP 5555** from the machine running VDA to the Fire TV's developer endpoint. The VDA CLI talks to its **local** daemon (commonly port **5037**); do not open that daemon to remote networks. **TCP 6556** is the usual Checkmk agent transport from a monitored Linux machine, **not** a VDA proxy. SSH (**TCP 22**) is not used by this package. Verify any port and firewall settings against the actual SDK and local environment. Limit access to trusted devices/networks.

**Screenshots and baselines:** Capture and the authenticated viewer currently require VDA access **on the site host**; screenshot PNGs and baseline JSON remain private on that site. No remote screenshot API or cross-host copy is provided. Do **not** expose static PNG files or automatically pause video. The package intentionally supports **one Fire TV per Checkmk site**, not multiple devices behind one collector. For architecture rationale see [Security](SECURITY.md) and [Known Issues](KNOWN_ISSUES.md).

**Future possibilities, not promises for v0.0.1:** (1) remote Linux agent plug-in with piggyback sections for a distinct Fire TV host, (2) an optional authenticated SSH data-source transport, (3) secure remote on-demand screenshot execution and transfer, and (4) per-device configurations. No such mode has been shipped or tested yet.

## Prerequisites checklist

| Requirement | How to verify / prepare |
| --- | --- |
| Checkmk **2.5.0+** Linux site | `omd version`, then enter the site with `omd su <SITE>` (as an administrator) |
| Python 3 standard library | `python3 --version` and `/usr/bin/python3 --version`; data-source rule uses `/usr/bin/python3` |
| Native Checkmk Linux agent file | As the site user: `test -r ~/share/check_mk/agents/check_mk_agent.linux` |
| Amazon Developer account and Vega CLI/SDK **on the Checkmk site host** | Install via Amazon's official SDK instructions; `vega --version` and `vega update` as needed |
| Vega OS Fire TV with Developer Mode enabled | Follow Amazon's account-authenticated Developer Mode procedure; device needs to be reachable |
| VDA over TCP/IP | Configure device connection mode / port (normally 5555), connect from the host and verify `vda devices` |
| Site user can execute the installed VDA binary | `test -x /absolute/path/to/vda` and ensure the site user can traverse parent directories |
| Private site config | `~/etc/firetv_vega.json`, mode `0600`, with accurate device and SDK path |
| Checkmk UI configuration | Host-specific **Individual program call instead of agent access** rule; service discovery |

Official Amazon documentation:
- [Enable Developer Mode](https://developer.amazon.com/docs/vega/0.24/developer-mode) — requires an Amazon Developer account and the Vega CLI (reboots the device).
- [Configure VDA over TCP/IP](https://developer.amazon.com/docs/vega/0.24/vda-over-tcpip) — standard TCP port 5555, and host-side `vega exec vda connect <DEVICE_IP>:5555`.
- [VDA commands and diagnostics](https://www.developer.amazon.com/docs/vega/0.24/vda-tools).

### 1. Prepare Vega SDK / VDA

On the **Linux host**, using the account where the Vega SDK is installed:

```bash
vega --version
vega exec vda devices
# Connect only if your device is not already listed:
vega exec vda connect <DEVICE_IP>:5555
vega exec vda devices
```

Alternatively use the `vda` executable directly with its absolute path, e.g. `/home/SDK_OWNER/.../bin/tools/vda`. Use the **exact identifier** returned by `vda devices` for the JSON `device` value; even one wrong IP octet produces `device not found`. If connection or authentication is not working, follow Amazon's official documentation before installing the MKP. Do not restart a VDA server shared with other Checkmk sites without coordination. The default local VDA server port is typically 5037; `vda_server_port` is optional and should normally be `null`.

If the SDK lives in a different Linux user's home directory, the Checkmk site account must be able to **traverse** parent directories. Prefer narrow ACLs instead of globally opening the entire home directory. Example (replace placeholders):

```bash
# Executed by an administrator, for a real SDK-owner home path:
sudo setfacl -m u:<SITE>:--x /home/<SDK_OWNER>
# Also grant traversal for private SDK directories and execute/read on the VDA binary as needed.
# Repeat on any intermediate directory where the site user lacks access.
```

Test as the site account: `test -x /absolute/path/to/vda && echo VDA-executable`. `test -x` by itself does not prove that the SDK binary can use the device; validate the real connection below. Do **not** use `chmod -R 755 /home` or disable OS security settings.

### 2. Install the MKP on a test Checkmk site

Download `dist/firetv_vega_vda-0.0.1.mkp` from this repository or its GitHub release. **As the Checkmk site user**:

```bash
mkp inspect /path/to/firetv_vega_vda-0.0.1.mkp
mkp add /path/to/firetv_vega_vda-0.0.1.mkp
mkp enable firetv_vega_vda 0.0.1
mkp list
```

Do not assume that a `.mkp` automatically creates a host, a special-agent ruleset, credentials, a GUI custom icon rule or a device connection.

### 3. Create the private configuration

The source tree includes `examples/firetv_vega.json.example`. Copy and edit it **inside the Checkmk site**:

```bash
cp /path/to/vega-cmk/examples/firetv_vega.json.example ~/etc/firetv_vega.json
chmod 600 ~/etc/firetv_vega.json
${EDITOR:-vi} ~/etc/firetv_vega.json
python3 -m json.tool ~/etc/firetv_vega.json >/dev/null
python3 ~/local/bin/firetv_vega_extras.py --diagnose
python3 ~/local/bin/firetv_vega_extras.py --list-components
```

Set `device` to the target from `vda devices` (address and port); set `vda_path` to the **absolute path** of your installed `vda` executable. Keep `vda_server_port: null` unless you intentionally use a separate server. The sample `192.0.2.10:5555` is a reserved documentation address and **not** a working device. The config must be an owner-only regular JSON file, not a symlink. It never belongs in GitHub, a source ZIP or an MKP.

The `--diagnose` command is read-only; it reports whether target selection and harmless shell commands work. No raw device identifier or stderr is printed. The wrapper avoids redundant reconnects and has a guarded fallback only when exactly one matching TCP device is connected. Check the configured IP *first* if you see `device not found`.

### 4. Add a Checkmk host and individual program rule

In the Checkmk UI, create a host, for example `vega-firetv`. Configure **No IP** if the device's IP is supplied exclusively through the private JSON and you don't need a separate ping check. Disable SNMP for this host. The real device need not listen on Checkmk's normal Linux agent port.

Navigate to **Setup → Agents → Other integrations → Individual program call instead of agent access** (the UI path may vary slightly by Checkmk patch). Add a rule **restricted to this host only**. Command:

```text
/usr/bin/python3 /omd/sites/<SITE>/local/bin/firetv_agent
```

Replace `<SITE>` with the actual Checkmk site name. This is a **data-source program**, not an automatically registered Checkmk special agent. Only one program should supply the agent output; don't append the collectors separately or invoke an old wrapper as well. Save and activate the rule.

Validate as the site user (replace `<HOST>` with the actual Checkmk host name):

```bash
cmk -D <HOST>           # Verify that the Program line is the wrapper
cmk -d <HOST> | grep -E '^(OSName:|<<<local>>>|[0-3] "Vega)' | head -40
```

Expect `OSName: Vega OS`, one `<<<local>>>` section, and Vega local check lines. In Checkmk: **Setup → Hosts →** `<HOST>` **→ Service discovery → Full scan**, accept relevant services and activate changes. The exact number depends on device packages, mounts, configured apps and check availability; don't hard-code 44 services.

### 5. Establish baselines deliberately

After you verify the current installed packages and app/OS versions are correct:

```bash
python3 ~/local/bin/firetv_vega_extras.py --save-baseline
python3 ~/local/bin/firetv_vega_deep.py --show-apps
python3 ~/local/bin/firetv_vega_deep.py --accept-baseline
```

The first saves all installed package IDs; the second accepts OS build + configured app versions. **Do not automatically accept unexpected changes** just to make monitoring green. Baselines live under site `~/var/`, outside the MKP.

### 6. Optional screenshot viewer

As site user, restart Apache if needed: `omd restart apache`. The protected viewer is at `/<SITE>/check_mk/firetv_screen.py` and the authenticated image endpoint at `/<SITE>/check_mk/firetv_screen_image.py`. Viewing and capture require Checkmk `wato.edit`. Capture happens only on an explicit POST with a CSRF token; there is a cooldown; PNG lives under private `~/var/firetv_private/now.png` (not web htdocs).

The MKP includes `src/web/htdocs/images/icons/firetv_camera_64.png`, but **does not install a Checkmk Custom icons and actions UI rule**. If desired, configure a host/service-scoped custom icon action pointing to `firetv_screen.py`, and upload the icon through the UI if required. The icon is optional: you can use the viewer URL directly. On active Prime Video playback screenshot capture may fail; manually pausing may help. The helper never pauses video on its own.

## Migrating from an existing private prototype

If you previously installed an experimental build of this extension, its version might be **numerically higher** than this first public release. Checkmk may therefore not treat the public package as a normal version upgrade. **Only use these steps if you are replacing an existing installation.** Back up your site-specific customizations, private `~/etc/firetv_vega.json`, and baseline/screenshot state under `~/var/firetv*` first. Do not change a separate, working Checkmk site.

```bash
mkp list
mkp inspect /path/to/firetv_vega_vda-0.0.1.mkp
# Replace the string below with the exact installed version shown by mkp list.
PREVIOUS_VERSION="REPLACE_WITH_INSTALLED_VERSION"
mkp disable firetv_vega_vda "$PREVIOUS_VERSION"
mkp add /path/to/firetv_vega_vda-0.0.1.mkp
mkp enable firetv_vega_vda 0.0.1
mkp list
```

If Checkmk refuses to enable a numerically lower release, consult `mkp --help` and follow the supported explicit remove/install workflow **only after backups**. Preserve the site-local configuration, baselines and existing host/rule settings. The program rule path, MKP package name `firetv_vega_vda`, and local service names remain unchanged.

## Troubleshooting

- `device not found`: compare *every* octet and port in JSON against `vda devices`; a test-site incident was a JSON typo, not an established upstream VDA defect.
- `VDA failed`: check developer access, SDK path, site-user permissions and the guarded `--diagnose` output; avoid stopping shared VDA daemons.
- `Memory` CRIT with little PSI: see [Monitoring](MONITORING.md); standard thresholds are intentionally untouched.
- Several identical `Filesystem` percentages: same underlying userdata partition may be mounted under many paths; see [Monitoring](MONITORING.md).
- `<<<ps_lnx>>>` empty: process enumeration inside the restricted developer shell is incomplete; see [Known Issues](KNOWN_ISSUES.md).
- Screenshot capture fails during video playback: pause manually if appropriate; last good PNG is preserved. See [Known Issues](KNOWN_ISSUES.md).
