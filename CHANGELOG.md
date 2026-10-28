# Changelog

## 0.0.1 — first public release (2026-10-28)

- Experimental Checkmk 2.5.0+ monitoring of Vega OS Fire TV devices over Amazon VDA, for development and QA use.
- Linux agent transport, package/app/OS version change detection, component lifecycle, foreground activity and process-death diagnostics.
- Graphable informational CPU, I/O and memory PSI checks. Standard Checkmk Memory and filesystem services remain unchanged.
- Authenticated, on-demand screenshot viewer and private screenshot storage with playback/DRM limitations.
- Site-private JSON configuration and app-ID mapping (including Prime Video); change baselines under site `var/`.
- Three verified, user-provided Checkmk example screenshots (Vega services, CPU pressure and Linux services) included under `docs/images/` and linked from README.
- Consolidated documentation with an explicit **local VDA/Checkmk deployment prerequisite**, supported-vs-planned network topologies (remote Linux agent / SSH **not included**), security guidance, GPL-2.0-only LICENSE, reproducible MKP build, root `manifest.json`, and release `dist/` with SHA-256 checksums.
- Previously exercised as **private pre-public prototypes**. The initial public version number is 0.0.1 by design; this is not a claim of production readiness.
