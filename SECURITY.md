# Security policy

## Supported deployment posture

This application is designed primarily for a single-user Windows desktop. The mobile API defaults to loopback. LAN mode is an explicit opt-in and requires either enrolled per-device credentials or the legacy shared token before device enrollment; it is not intended to be exposed directly to the public internet.

Before enabling LAN access:

- Prefer per-device credentials created with `python mobile_device_cli.py add "Device name"`; revoke lost devices with `python mobile_device_cli.py revoke DEVICE_ID`. The legacy shared token remains available only until a device registry is created. Once enrolled, the encrypted registry is authoritative and shared-token fallback is disabled.
- Restrict Windows Firewall access to trusted private networks and known devices.
- Assign the minimum required device role from the trusted desktop: `admin`, `operator`, `approver` or `viewer`. New devices default to `operator`; legacy shared-token clients are also restricted to `operator`.
- Never rely on a mobile-supplied role claim. The server resolves roles from the encrypted registry and authorizes every WebSocket command. Only admin/approver devices can receive mobile approval challenges.
- Keep at least one active admin device enrolled. The CLI refuses to demote or revoke the last active admin.
- Prefer a VPN or TLS-terminating reverse proxy (`wss://`) on networks you do not fully trust.
- Do not run the application with unnecessary administrator privileges.
- Review every plugin before enabling it. Plugins run in child workers. On Windows, only plugins whose verified manifest explicitly selects `execution_sandbox: appcontainer` use the AppContainer pilot; the JSON formatter is the first opted-in plugin. Other plugins remain same-user crash-contained workers and are not OS-sandboxed.
- Protect local history, memory, audit and undo files using Windows account permissions and disk encryption.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** feature (Security tab) if private vulnerability reporting is enabled for this repository. Otherwise, open a private contact route with the repository owner before sharing exploit details. Do not publish credentials, personal data or a working exploit in a public issue.

## Important limitations

Plugin manifests and checksums provide integrity checks, not cryptographic publisher signatures. AppContainer isolation is a narrow per-plugin Windows pilot, not a blanket sandbox; plugins without the explicit manifest opt-in remain same-user workers. Command analysis is heuristic and is not a PowerShell sandbox. Direct `ws://` LAN mode does not encrypt traffic. Cross-network mobile connectivity currently depends on an operator-managed VPN or TLS-protected `wss://` endpoint; there is no built-in internet relay or QR pairing flow. Never expose the mobile API directly to the public internet. Review the engineering audit for additional limitations before production deployment.


## Data encryption, backup and key recovery

Review [the Phase 8.3 threat model](docs/PHASE_8_3_SECURITY_THREAT_MODEL.md) and [the local key-management runbook](docs/KEY_MANAGEMENT_RUNBOOK.md) before bootstrap, migration, recovery or key retirement. In particular, retirement requires the desktop application and all runtime writers to be stopped; the key-administration lock does not quiesce ordinary application writes.
