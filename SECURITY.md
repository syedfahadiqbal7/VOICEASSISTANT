# Security policy

## Supported deployment posture

This application is designed primarily for a single-user Windows desktop. The mobile API defaults to loopback. LAN mode is an explicit opt-in and requires a shared token; it is not intended to be exposed directly to the public internet.

Before enabling LAN access:

- Prefer per-device credentials created with `python mobile_device_cli.py add "Device name"`; revoke lost devices with `python mobile_device_cli.py revoke DEVICE_ID`. The legacy shared token remains available only until a device registry is created. Once enrolled, the encrypted registry is authoritative and shared-token fallback is disabled.
- Restrict Windows Firewall access to trusted private networks and known devices.
- Prefer a VPN or TLS-terminating reverse proxy (`wss://`) on networks you do not fully trust.
- Do not run the application with unnecessary administrator privileges.
- Review every plugin before enabling it; plugins execute in the desktop process and are not OS-sandboxed.
- Protect local history, memory, audit and undo files using Windows account permissions and disk encryption.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** feature (Security tab) if private vulnerability reporting is enabled for this repository. Otherwise, open a private contact route with the repository owner before sharing exploit details. Do not publish credentials, personal data or a working exploit in a public issue.

## Important limitations

Plugin manifests and checksums provide integrity checks, not cryptographic publisher signatures or sandbox isolation. Command analysis is heuristic and is not a PowerShell sandbox. Direct `ws://` LAN mode does not encrypt traffic. Review the engineering audit for additional limitations before production deployment.
