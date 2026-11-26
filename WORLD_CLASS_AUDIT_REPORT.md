# Voice Assistant — Comprehensive Engineering Audit & Hardening Report

**Assessment mode:** source inspection + isolated regression testing  
**Development target:** GitHub repository main branch; original uploaded archive remains untouched  
**Original archive:** not modified  
**Runtime certification:** not completed on the user's Windows machine

## 1. Executive summary

Voice Assistant is a local-first Windows AI desktop agent with a Flutter companion, a structured planner/executor, registered tools and plugins, local persistence, scheduling, audio I/O, and a FastAPI/WebSocket bridge. It has undergone multi-phase security, reliability, privacy, mobile and desktop UX hardening. It is a hardened development build, not yet a production-certified enterprise application.

The repository now includes mobile per-device identity and revocation, server-enforced mobile RBAC and approval routing, fail-closed policy gates, plugin manifest/permission/archive enforcement, a Windows AppContainer pilot with package-SID named-pipe broker and explicit per-plugin registry routing, encrypted local runtime stores, DPAPI-backed Windows key protection, portable encrypted backup/restore, protected keyring bootstrap/rotation, explicit resumable VAENC1-to-VAENC2 migration, offline recovery kits, guarded retained-key retirement, structured redacted diagnostics, and opt-in retention controls. The README has a user-facing feature catalogue and documents the remaining per-plugin sandbox boundary.

PR #46 has been merged into `main` (merge commit `88990d296ad0e7e59b0df96531f1d7672ccd7d2f`). The post-merge CI run [#45414269392](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45414269392) completed successfully. The validated pipeline covers the Windows Python regression suite, Phase 0 smoke checks, Flutter analysis/tests, Android APK build and unsigned iOS build. The previously reported baseline was **344 Python tests** and **16 Phase 0 smoke checks**; do not interpret CI success as physical-device or production certification. Real Windows GUI/audio/Ollama operation and physical Android/iOS behavior still require target-environment validation.

## Phase completion map

| Phase | Scope | Status | Remaining boundary |
|---|---|---|---|
| 0.1–0.3 | Security baseline | Complete for scoped source controls | Ongoing threat-model review |
| 1 | Reliability and recovery | Implemented and regression-tested | Full real-desktop end-to-end validation |
| 2 | Plugin and integration hardening | Manifest, permissions, installer, crash-contained worker, broker and opt-in AppContainer pilot implemented | JSON formatter and no-permission hello example are the current AppContainer plugins; other plugins remain same-user workers; broader broker capabilities, publisher signing and independent review remain outstanding |
| 3 | Persistence and privacy | Encrypted stores, backup/restore and opt-in retention implemented | No secure erasure or in-app privacy dashboard |
| 4 | Mobile companion | Flutter app, API, identity, RBAC and approval routing implemented; CI builds | Physical-device, real LAN and signed distribution testing |
| 5 | Desktop UI/UX and lifecycle | Source-level hardening implemented | Interactive Windows GUI, audio and Ollama runtime validation |
| 6 | QA and observability | Automated regression suite, smoke checks and structured diagnostics implemented | Performance/load testing, full E2E and broader exception-path review |
| 7 | Deployment and release | CI and guarded development pre-release workflow implemented | Windows installer and production Android/iOS signing |
| 8.1–8.2 | Mobile device identity and RBAC | Implemented and CI validated | Multi-device physical acceptance testing |
| 8.3.1–8.3.7 | Protected keyring, VAENC2, migration, rotation, recovery and retirement | Implemented and CI validated | Operational recovery drill and all-writer quiescence controls |

A phase marked complete means its scoped source changes and automated checks are implemented; it does not imply physical-device or production-environment certification.

## 2. Source inventory

Current tracked source inventory on `main` (362 files):

| Area | Files | Notes |
|---|---:|---|
| `agent/` | 28 | Orchestration, persistence, scheduler, security, key management and observability |
| `audio/` | 3 | Speech capture, STT and TTS |
| `mobile_api/` | 6 | FastAPI/WebSocket bridge, connection lifecycle, device identity and authorization |
| `plugins/` | 111 | 56 Python plugin/tool modules and 55 JSON manifests |
| `tools/` | 4 | Built-in tools |
| `ui/` | 9 | Desktop UI and operational viewers |
| `security/` | 1 | Command/policy security support |
| `tests/` | 20 | 19 Python test/support files and plugin authoring documentation |
| `voice_assistant_mobile/` | 137 | Flutter app, platform projects and tests |

The tracked tree contains 140 Python files overall. One legacy scheduler plugin remains intentionally disabled.
## 3. Existing feature inventory (source-confirmed)

### Desktop and agent core

- Text input and microphone-driven voice loop.
- Faster-Whisper speech-to-text and pyttsx3 text-to-speech.
- Ollama OpenAI-compatible chat client with optional cloud failover.
- Model selection / refresh and Dry-Run mode for PowerShell.
- Conversation history, export, audit log and local memory store.
- Intent fast paths, LLM-generated plans, outcome templates and multi-step execution.
- Post-action verification for files, processes, windows and command results.
- User approval gates, file diff preview, undo history and policy viewer.
- Scheduled one-time and interval jobs while the desktop app remains running.
- Plugin and skill-pack registry, custom skill authoring, session synchronization.

### Plugin/tool catalogue

- Browser/search, Office, VS Code, YouTube and document helpers.
- File read/write, PDF, Excel, Word/PDF document generation and backups.
- Git/GitHub, Docker, process/service/port and environment management.
- Clipboard, screen control, OCR, Teams/email helpers and ForJob workflows.
- Operational and planning skill packs (incident, release, PR review, standup, ADR, etc.).

### Flutter companion

- Desktop host connection over WebSocket.
- Text chat and mobile speech-to-text input.
- Transcript/status streaming, security approval, session state and schedule viewing/cancellation.
- Undo availability and plan/session status presentation.

Feature presence in source does not imply every integration was successfully exercised against real Windows applications or external accounts.

## 4. Implemented hardening

### Security and authorization

1. **Mobile listener defaults to loopback.** Desktop startup no longer binds to `0.0.0.0` by default.
2. **Explicit LAN opt-in.** Remote binding requires `VOICEASSISTANT_MOBILE_HOST` and a `VOICEASSISTANT_MOBILE_TOKEN` of at least 32 characters.
3. **WebSocket authentication.** The shared token is compared in constant time from a handshake header; it is not embedded in the URL.
4. **Approval connection binding.** A security challenge is delivered to one authenticated mobile connection; approval responses from other connected clients are rejected. Desktop approval remains supported.
5. **Strict approval payload validation.** `approved` must be a JSON boolean; the string `"false"` can no longer become truthy approval.
6. **Mobile input limits.** Incoming messages must be strings and are limited to 10,000 characters; WebSocket frame size is capped at 1 MiB; approval values must be JSON booleans (not truthy strings).
7. **Policy fail-closed.** Exceptions during policy evaluation now stop direct tool calls and plan steps.
8. **Canonical write-path checks.** Allowed write roots are resolved before path comparison, preventing folder-name lookalikes and existing symlink escapes.
9. **Manifest enforcement.** Plugins without manifests, with invalid checksums, or exporting undeclared tool names are blocked. Duplicate tool names no longer overwrite existing registrations.
10. **Plugin ZIP hardening.** ZIP-slip/absolute paths, backslashes, symlinks, encrypted entries, unsupported compression, oversized archives, excessive member counts and unexpected files are rejected. The installer reads bounded members without `extractall`, verifies the supplied SHA-256 exactly (no silent checksum refresh), stages installation, refuses overwrites by default and requires explicit desktop confirmation for replacement.
11. **Exact plugin exports.** All active plugin manifests now declare the actual `BaseTool.name` exports. Registry loading is all-or-nothing: undeclared/missing exports, duplicate names and collisions block the whole plugin rather than partially registering it.
12. **Permission metadata and fail-closed defaults.** Shipped plugin manifests declare file, process, screen, network, service, environment, registry, messaging, email and clipboard capabilities. Read-only policy rejects declared mutating capabilities and common mutating tool-name verbs. Plugins with missing/empty permission metadata default to `system:admin`, except the two built-in pure utilities `say_hello` and `json_formatter`.
13. **Safe manifest regeneration.** The manifest generator derives actual tool names from Python AST without importing plugin code and preserves existing permissions. Unknown or missing permission metadata defaults to `system:admin`; the disabled legacy scheduler plugin is skipped.
14. **Publisher identity is not spoofable metadata.** The manifest author field is exposed only as an unverified claim; it is never treated as verified publisher trust. Direct scheduled tool execution now passes through policy evaluation and fails closed on policy errors.
15. **API key handling.** Cloud LLM API keys are no longer persisted to the failover JSON file; use `VOICEASSISTANT_CLOUD_API_KEY`.
16. **No placeholder API key.** The example failover config no longer contains a fake `sk-...` value.
17. **Authenticated local data encryption.** Conversation history, audit logs, memory, policy, scheduled jobs, undo metadata/snapshots, skill-pack settings and custom-skill manifests/indexes use AES-256-GCM encryption at rest.
18. **Protected key management.** Windows uses a random 256-bit data key protected by the current Windows user profile with DPAPI. Other platforms require an explicit 32-byte `VOICEASSISTANT_DATA_KEY`; a malformed key never falls back to plaintext.
19. **Fail-closed decryption and migration.** Existing plaintext JSON stores migrate atomically on first encrypted read. Wrong keys, tampering and store-context mismatch raise a data-protection error; affected stores are not silently reset to empty defaults.
20. **Custom skill source minimization.** Generated plugin source no longer embeds private triggers, URLs or action arguments; it reads the protected custom-skill manifest at runtime.
21. **Portable encrypted backup/restore.** A bounded allowlisted runtime-data archive is protected by PBKDF2-HMAC-SHA256 (600,000 iterations) and AES-256-GCM. Restore verifies archive authentication, path safety, symlinks, duplicate entries, size/compression limits and JSON shapes before writing; each restored file is atomic. The interactive CLI requests passphrases via `getpass` and requires an explicit `RESTORE` confirmation.
22. **Opt-in data retention.** An encrypted retention policy and `privacy_cli.py` support show/set/preview/purge for conversation history, audit entries, memory and terminal scheduled jobs. Rules are disabled by default; purging requires explicit `PURGE` confirmation and preserves pending/running jobs and records with unknown timestamps.

### Reliability and data integrity

1. PowerShell execution has a 120-second timeout, kills timed-out processes and returns an exit code.
2. `command_exit_zero` verification requires an actual exit code; it no longer treats missing evidence as success.
3. Unavailable window-title verification now fails as unverifiable instead of passing.
4. Undo snapshot failures block the write rather than allowing an overwrite without rollback protection; Desktop-relative and placeholder paths are normalized consistently for both policy and undo snapshotting.
5. Conversation requests are serialized to protect shared messages, queues and cancellation state from simultaneous desktop/voice/mobile turns.
6. JSON-backed history, audit, policy, scheduler, undo, memory, skill-pack and custom-skill stores use atomic temp-file-and-replace writes; protected stores are encrypted before the atomic replace.
7. Scheduler job-list access is guarded by a lock.
8. Desktop shutdown now stops TTS, the scheduler and global hotkeys before destroying the window.
9. STT and TTS initialization fail independently; text mode remains available if voice services fail.
10. Missing `openai` import no longer prevents importing non-LLM agent components; an actual LLM request reports the missing dependency.
11. Scheduler jobs now persist `pending/running/completed/failed/cancelled` states, recover interrupted `running` jobs after restart, retry failed handlers up to three attempts with bounded delays, and prevent concurrent duplicate firing. Completion checkpoints are atomic; if a checkpoint fails, the job is re-queued for at-least-once recovery. Corrupt scheduler JSON is quarantined instead of silently discarded.
12. Scheduler handlers now report success/failure to the scheduler; the mobile schedules view receives job status, attempt count and last error.
13. **Portable backup and restore.** `BackupManager` supports portable passphrase-encrypted backups of approved runtime stores and undo snapshots, with custom-skill loader regeneration on restore. Multi-file restore is per-file atomic, not a single transaction.
14. **User-controlled retention.** `PrivacyManager` keeps retention disabled by default and provides preview-before-purge controls for conversation, memory, audit and terminal scheduler data.

### Mobile UX and lifecycle

1. Removed the hardcoded desktop IP from the mobile connection field.
2. Added a masked token input for LAN authentication.
3. Added a mounted check after asynchronous speech initialization.
4. Stops speech listening during screen disposal.
5. Caps in-memory transcript history at 500 messages.
6. Avoids scheduling auto-scroll on every widget rebuild when the message count is unchanged.
7. Cleans up WebSocket resources when the service is disposed.
8. Uses `IOWebSocketChannel` so the token header is supported by the pinned `web_socket_channel` 2.4.0 package; supports `ws://` and `wss://` endpoints.
9. Speech permission, initialization, listen and stop failures are handled without uncaught platform exceptions.
10. Replaced the Flutter starter counter test with service-level tests for initial state, disconnected sends, transcript clearing and visible-error dismissal.
11. Stores the desktop host and shared token with `flutter_secure_storage`; saved credentials are removed when the user forgets the connection, which also closes the live socket and stops reconnect attempts.
12. Uses bounded exponential reconnect delays and stops retrying after authentication rejection; stale connection handshakes cannot restore credentials after disconnect/forget.
13. Binds approval requests to the exact authenticated connection, acknowledges mobile decisions, synchronizes desktop decisions back to the phone, and dismisses stale/rejected challenges without leaving the UI stuck.
14. Applies per-WebSocket limits of 120 messages/minute and 20 user messages/minute, caps frames at 1 MiB, and validates bounded schedule IDs and approval payload types.
15. Supports direct TLS termination using configured certificate/key files or a TLS reverse proxy. The Flutter app warns and requires confirmation before non-loopback cleartext `ws://`; remote iOS connections require `wss://`.
16. Adds an explicit mobile undo confirmation and schedule-cancellation confirmation, plus visible connection/approval error feedback.
17. Requests microphone permission only after the user taps the microphone button and uses the device/system default speech locale instead of forcing `en_US`.
18. Adds Android microphone/network permissions, disables Android app backup for Keystore-backed credentials, and declares iOS microphone, speech-recognition and local-network usage descriptions.
19. Adds CI validation for an unsigned iOS release build in addition to Flutter analysis/tests and the Android release APK build.

### Documentation and developer experience

- Added root README and Windows dependency manifests.
- Updated plugin authoring contract to require exact tool names, checksums and permission declarations.
- Updated mobile README and LAN security setup guide.
- Added regression coverage for security and reliability fixes.

## 5. Plugin registry verification

The initial enforcement pass found mismatches between plugin manifest `tools` values and actual `BaseTool.name` values. The shipped manifests were corrected rather than weakening validation. A static AST regression checks exact manifest/export parity for all **54 active plugin modules**; the legacy `task_scheduler.py` remains disabled.

Registry registration is atomic. A plugin is registered only when its complete exported tool-name set matches its manifest and no existing tool name conflicts. Plugins with no capability declarations are treated as privileged by default, with only the two built-in pure utilities explicitly allowlisted for an empty permission set.

The installer does not claim cryptographic publisher authentication. SHA-256 checks integrity against the included manifest; a malicious publisher can replace both. The `author` field is unverified. Plugins default to a same-user child worker; only plugins explicitly marked `execution_sandbox: appcontainer` use the Windows AppContainer pilot.

## 6. Verification performed

| Check | Result | Notes |
|---|---:|---|
| Python AST / compile check | Pass | 118 Python files; zero syntax errors |
| Focused security/reliability tests | 25/25 pass | Windows GitHub Actions |
| Scheduler durability/recovery tests | 10/10 pass | Crash recovery, retries, cancellation, concurrency and checkpoint failure |
| Plugin marketplace/security tests | 16/16 pass | ZIP traversal, checksum mismatch, symlink, overwrite, permissions, trust metadata, generator and registry checks |
| Local data protection and backup tests | 15/15 pass | AES-GCM, tamper/wrong-key detection, plaintext migration, encrypted stores/undo, portable restore across different keys, wrong-passphrase and ZIP-slip rejection |
| Privacy retention tests | 4/4 pass | Default-off policy, encrypted settings, preview, explicit purge, pending/unknown-record preservation and tamper detection |
| Observability tests | 3/3 pass | JSON formatting, secret redaction in messages/tracebacks and rotating sink idempotency |
| Mobile connection capacity tests | 3/3 pass | Configured bounds, concurrent connection rejection and atomic reservation under simultaneous handshakes |
| Mobile shutdown lifecycle tests | 3/3 pass | Graceful signal, safe no-server behavior and startup/shutdown race |
| Combined Python regression tests | 92/92 pass | All Windows regression suites including mobile, observability, lifecycle and concurrent connection-capacity cases |
| Existing Phase 0 smoke suite | 16/16 pass | Windows GitHub Actions |
| Plugin manifest/checksum scan | 55/55 pass | Checks shipped Python file checksums and manifest shape |
| Plugin manifest/export parity | 54/54 pass | AST check for all active plugin modules; legacy scheduler excluded |
| Python compileall | Pass | Windows GitHub Actions |
| Flutter `flutter analyze` / `flutter test` | Pass | Ubuntu GitHub Actions |
| Android release APK build | Pass | GitHub Actions release-variant build succeeded; branch pushes do not publish a release artifact. Current Gradle release variant is debug-signed, not Play Store-ready |
| iOS release build (unsigned) | Pass | macOS GitHub Actions; compilation only, no signed IPA |
| Mobile platform privacy configuration | Pass | Python regression validates Android manifest and iOS Info.plist permission/backup declarations |
| Windows desktop GUI/runtime | Not run | Requires Windows, desktop dependencies and interactive GUI session |
| Physical Android/iOS device, microphone/STT and background/resume | Not run | Requires supported devices, OS permission prompts, audio hardware and real LAN connectivity |
| Ollama / cloud provider | Not run | Requires running Ollama/model or configured cloud credentials |

## 7. Remaining production risks (not represented as fixed)

### P1 — complete before exposing to a wider network

- **TLS certificate lifecycle.** Direct TLS now preflights the PEM certificate, matching private key, validity window and optional public-host SAN at startup, with a 30-day renewal warning. Certificate hot-reload, online OCSP/CRL checks, certificate pinning, per-device client certificates and an in-app token rotation workflow are not implemented. Reverse-proxy deployments must explicitly trust proxy source IPs/CIDRs for forwarded HTTPS headers. Per-device credentials, revocation and server-side device roles are implemented through the trusted desktop CLI. Plain `ws://` remains available after explicit confirmation and must be restricted to a trusted private LAN/VPN.
- **Legacy shared-token compatibility.** The shared-token mode remains available only before a device registry is created and provides operator-level permissions without per-device attribution. Once the encrypted registry exists, per-device credentials and roles are mandatory. There is not yet a per-human identity provider, device certificate identity or in-app token rotation.
- **Mobile release signing.** Android's release build currently uses the debug signing configuration and iOS CI uses `--no-codesign`. Configure protected Android signing material and Apple signing/provisioning before public distribution.
- **Plugin isolation is partial and opt-in.** The JSON formatter and no-permission hello example are the currently reviewed plugins routed through AppContainer for both metadata discovery and execution. Plugins in the default worker mode still run with the desktop user's OS permissions; checksums are integrity controls, not cryptographic publisher signatures, and the author field is unverified metadata.
- **Permission declarations are not an OS boundary in default worker mode.** The AppContainer pilot enforces a no-network token and scoped ACLs for explicitly opted-in plugins, but currently supports only a narrow import/capability set and policy-approved filesystem reads. Continue code review and explicit trust for all third-party plugins.
- **Command analysis remains heuristic.** The command analyzer is not a PowerShell sandbox. High-impact commands need OS-level least privilege and strong approval UX.

### P1/P2 — reliability and privacy

- **Scheduler delivery semantics.** The scheduler state machine and retries are at-least-once for arbitrary callbacks/reminders. Built-in scheduled tool fast paths additionally use a durable at-most-once invocation ledger: stable occurrence IDs survive retries/restarts, completed IDs are no-ops, and ambiguous started IDs fail closed instead of being replayed. This is not exactly-once external effect delivery; third-party integrations must enforce the execution ID at their own side-effect boundary.
- **Key recovery and rotation.** Windows DPAPI key material is bound to the current Windows user profile. Losing the DPAPI key file/profile makes raw encrypted runtime files unrecoverable. Portable backups can be restored on another machine using their separate passphrase, but key rotation for an existing live data set is not implemented. Explicit Markdown/text conversation exports remain plaintext and must be stored securely.
- **Retention and deletion semantics.** User-configurable retention is available through `privacy_cli.py` for conversation history, memory, audit entries and terminal scheduled jobs. It is manual and opt-in; undo snapshots, custom skills, exports and policy data are not covered. Deletion is not secure erasure, and there is no in-app privacy dashboard.
- **Backup transaction boundary.** Restore validates the full archive before writing and atomically replaces each file, but the set of files is not committed as one filesystem transaction. Take a fresh backup before restore.
- **Broad exception handling remains.** Static scan previously found 337 exception handlers, including 310 `except Exception` handlers and 75 handlers whose only statement is `pass`. These are not all defects. Structured, rotating, secret-redacting JSONL diagnostics are now configured for the desktop entry point and mobile API; broad handler cleanup and typed recovery remain incomplete.
- **No comprehensive end-to-end suite.** Real Windows desktop GUI/audio/Ollama workflows, Android device-level behavior, concurrent cancellation and external account/plugin integrations still need target-environment validation. Unit/regression coverage already exercises scheduler restart recovery and undo snapshot failure paths.
- **Malformed legacy data recovery.** Encrypted authentication failures fail closed. Malformed legacy plaintext JSON is not quarantined uniformly across every store; some stores raise a typed or validation error and require restore from a portable backup or manual repair.
- **Mobile connection quotas.** Per-connection sliding-window quotas limit total WebSocket messages and user prompts, and an atomic process-local concurrent-connection cap defaults to five (configurable 1–32). Per-device identity, revocation and server-side roles are implemented, but registry file locking and connection quotas remain process-local; cross-process limits are not implemented.

### P2 — product and UX

- Add an in-app per-device pairing/revocation UI and secure token rotation workflow; trusted desktop CLI enrollment, role management and revocation are implemented.
- Add visible provider status and actionable missing-dependency diagnostics to the desktop UI.
- Add structured task progress and cancellation-state visibility across desktop and mobile.
- Add explicit time-zone handling for schedules and a user-controlled retry-now action for failed jobs.
- Add a mobile connection diagnostics screen; secure credential persistence, reconnect handling and optional TLS/WSS configuration are implemented.
- Add plugin dependency/version resolution, publisher signatures, hot-reload lifecycle, additional brokered capabilities, broader AppContainer-compatible dependency staging and staged migration of the remaining plugins.
- Add accessibility, responsive layout, keyboard navigation and visual regression checks.
- Replace remaining placeholder/starter project metadata and validate app branding/package identifiers before release.

## 8. Release checklist for the user's Windows machine

1. Extract the development ZIP to a new folder; keep the original archive untouched.
2. Create a Python virtual environment and install `requirements.txt` plus `requirements-windows.txt`.
3. Confirm Ollama is running and the configured model is installed.
4. Run the two Python test commands from the root README.
5. Run `python main.py`; verify text chat first, then microphone/STT, then TTS.
6. Verify local API health on `127.0.0.1:8765`.
7. If mobile LAN is needed, set a random 32+ character token, restrict Windows Firewall to the private LAN and test both wrong-token rejection and correct-token pairing.
8. Verify mobile approval from the authorized phone and rejection from a second client.
9. Test representative read-only and mutating plugins, including file-write undo, process termination, service control, document generation and ForJob tracker creation.
10. In `voice_assistant_mobile`, run `flutter pub get`, `flutter analyze`, `flutter test`, then build/install on a real Android device.
11. Test scheduler restart behavior and document any at-least-once/at-most-once decision before production.
12. Review plaintext history retention, Windows ACLs, plugin trust and network TLS before deployment.

## 9. Final status

**Implemented:** focused security/reliability hardening, durable scheduler recovery/retries, plugin ZIP/manifest/permission enforcement, AES-256-GCM local persistence encryption, DPAPI/environment key handling, plaintext migration, fail-closed integrity checks, portable passphrase-encrypted backup/restore, opt-in retention preview/purge, mobile connection quotas/capacity limits, graceful mobile API shutdown, desktop lifecycle/confirmation improvements and structured secret-redacting rotating diagnostics.  
**Verified:** 92 Python regression tests, 16 Phase 0 smoke checks, 118-file Python compileall, Flutter analysis/tests, Android release APK build and unsigned iOS build. Full cross-platform CI: [44038035438](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44038035438).  
**Not certified:** Windows desktop GUI/runtime, microphone/STT/TTS, Ollama/cloud-provider integration, real Android device behavior, direct-LAN TLS/WSS deployment and external account integrations.

This is a meaningful engineering hardening release candidate, not a claim that every original feature is production-ready or that all remaining findings have been resolved.


## 8.1 Per-device mobile identity — implementation and CI validation complete

The mobile API now supports an encrypted device registry and unique per-device credentials:

- `mobile_api/device_registry.py` issues a random 256-bit-equivalent URL-safe secret per enrollment and stores only its SHA-256 digest.
- The registry is persisted as AES-256-GCM encrypted `history/mobile_devices.json`, using the existing local data-protection key.
- `mobile_device_cli.py` supports trusted-desktop enrollment, listing and revocation.
- Once the registry file exists, it becomes authoritative. Legacy shared-token authentication is disabled, including when every enrolled device is revoked; this prevents a revoked device from falling back to the former shared secret.
- The WebSocket server revalidates device credentials on each incoming message and at 10-second idle intervals, closing revoked sessions.
- The Flutter client accepts a device ID and device secret and stores both using the existing secure-storage plugin. Blank device ID retains legacy shared-token behavior only before device registry enrollment.
- Automated tests cover secret-at-rest behavior, wrong/unknown credentials, revocation, legacy compatibility and shared-token fallback prevention.

**Operational boundary:** a registry file must be protected together with the desktop's DPAPI/data-encryption key. The one-time device secret cannot be recovered from the server; lost credentials require revocation and re-enrollment. The current registry lock is process-local; concurrent enrollment/revocation from multiple administrative processes is not yet protected by an OS-level inter-process lock.

**Verification status:** source changes and regression tests are cross-platform CI validated in [run #44326987049](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44326987049). Real Android/iOS secure-storage and LAN/WSS tests remain target-device work.


## 8.2 Mobile device RBAC — implementation and CI validation complete

Phase 8.2 implementation now includes:

- A server-owned role policy in `mobile_api/authorization.py` with explicit `admin`, `operator`, `approver` and `viewer` permissions. Unknown roles, malformed commands and unmapped commands fail closed.
- The encrypted device registry stores roles and returns a minimal authenticated principal. Existing registry entries without a role are migrated to `operator`; unsupported role values fail closed.
- The trusted desktop CLI supports `add --role`, `list` and `set-role`. Device listing never returns credentials. New devices default to `operator`.
- The registry prevents demotion or revocation of the last active admin device. A second admin must be enrolled before replacing the owner device.
- The WebSocket server resolves roles from server-side credentials, ignores any client-supplied role claim, and enforces the permission matrix before dispatching every message.
- Connection manager tracks server-resolved roles. Mobile approval challenges are routed only to active `admin` or `approver` connections. The existing desktop approval UI remains the fallback.
- Role changes and revocations are re-resolved on every message and on the existing approximately 10-second idle revalidation cycle.
- Legacy shared-token and unauthenticated loopback compatibility clients are assigned only `operator` permissions; they cannot approve security challenges.
- RBAC regression tests cover the permission matrix, least-privilege defaults, role updates, last-admin protection, revocation, legacy identity and approver selection. CI includes `tests/test_mobile_rbac.py`.

**Current verification status:** Phase 8.2 source changes, regression tests, CI integration and security documentation are cross-platform CI validated in [run #44326987049](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44326987049). Real multi-device approval behavior still requires target-device integration testing.


---

# Current engineering status addendum — 2026-11-18

This addendum is authoritative where earlier sections in this report describe older phase states, test counts, or CI runs. The historical audit text is retained for traceability; it must not be used as the current completion summary.

## Current phase status

| Area | Current status | Evidence / remaining boundary |
|---|---|---|
| Phases 0–5 | Implemented for scoped requirements | Source-level hardening is present; target Windows GUI/audio/Ollama and physical-device tests remain |
| Phases 6–7 | Implemented foundation; production release not certified | Regression CI, diagnostics and guarded development pre-release workflow; no signed production installer/mobile artifacts |
| Phase 8.1 | Device identity, enrollment, revocation | Server-side registry and CLI; process-local registry locking remains |
| Phase 8.2 | Mobile RBAC and approval routing | Server-owned roles, fail-closed command matrix and approval routing |
| Phase 8.3.1–8.3.2 | Protected keyring and VAENC2 envelope | DPAPI/environment keyring protection, key IDs, authenticated encryption and VAENC1 compatibility |
| Phase 8.3.3 | Explicit resumable migration engine | Registered-store inventory, per-file atomic replacement, digest verification and journal |
| Phase 8.3.4 | Local key bootstrap/rotation CLI | Explicit confirmation, authenticated backup gate and retained prior keys |
| Phase 8.3.5 | Recovery kit export/import | Separate passphrase-protected all-key recovery artifact; import refuses overwrite |
| Phase 8.3.6 | Backup-integrated key retirement | Active/reference checks, authenticated backup and exact recovery-kit checks; ordinary writers must be stopped operationally |
| Phase 8.3.7 | Threat model, runbook and migration CLI | Implemented and cross-platform CI validated; latest inventory/preflight regression fixes are included in the green run below |

## Latest verified CI

The latest fully green cross-platform run is [#44326987049](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44326987049), commit `a7ca3e242a53cb30149f3b93d91e0aa459f8be2e`. Python Windows regression tests and smoke checks, Flutter analysis/tests, Android release APK build, and unsigned iOS build all completed successfully. The Python suite reported 178 passing regression tests and 16 Phase 0 smoke checks. This run validates the migration inventory/preflight hardening and journal newline correction.

A final audit found a migration CLI edge case: when no VAENC1 files remained, the CLI could report “no migration necessary” even if the inventory also contained plaintext/unknown or unsupported encrypted envelopes. The CLI now performs an inventory preflight and refuses migration if either category is present, before it verifies the backup or attempts any rewrite. New regression tests cover plaintext and unsupported-envelope refusal and assert the original bytes remain unchanged.

The preflight fix is committed in:
- `3e349030accd7d1a863ebb4ba622cf12175b97a0` — migration preflight guard.
- `ff82534ff53bce7c840920b66023282686b70697` — regression tests.

The intermediate runs [#44326176355](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44326176355) and [#44326188667](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44326188667) exposed the migration-journal newline regression. The issue was fixed and the complete latest cross-platform run #44326987049 passed.

## Security and production-readiness decision

**Engineering implementation status:** Phases 0–8.3.7 are implemented in the repository for their documented scope, and the latest migration-preflight changes are cross-platform CI validated. The README now contains a user-facing application feature catalogue and updated key-management documentation.

**Not production-certified.** Remaining material boundaries:
1. No real Windows end-to-end certification of desktop GUI, microphone/STT/TTS, Ollama lifecycle, device drivers or external application automation.
2. No physical Android/iOS LAN pairing, secure-storage, reconnect and approval acceptance test; CI builds/tests are not device certification.
3. No independent penetration test or formal cryptographic review. CI is regression evidence only.
4. Plugins default to a same-user child worker with crash containment only; the first explicitly opted-in plugin uses AppContainer. Manifest checksums are integrity controls, not signed publisher identity.
5. Direct remote `ws://` traffic is unencrypted; production remote access must use VPN or correctly configured TLS/WSS and firewall restrictions.
6. Runtime key-store inventory is allow-list based. Every future encrypted store must be added to migration discovery, backup/restore allow-list, tests and the threat model.
7. Ordinary runtime writers do not acquire the key-administration lock. Stop the application and all writer processes before migration or key retirement; if quiescence cannot be guaranteed, do not retire keys.
8. Multi-file migration and restore are not whole-directory transactions. Maintain independently verified backups and test recovery before operational use.
9. Key retirement removes a key from the live keyring but is not secure erasure from historical backups, recovery kits, snapshots or storage media.
10. Production packaging/signing, installer upgrade/uninstall behavior, secrets provisioning and deployment rollback still need an environment-specific release process.

**Release recommendation:** continue as a hardened development/beta build. Do not label it enterprise production-ready until the above deployment and verification gates are completed and signed off by the system owner/security reviewer.


## README feature catalogue update — 2026-11-18

The repository README previously described the product in one introductory paragraph and did not provide a discoverable, user-facing feature inventory. Commit `04fc286e45dcff2d11ecf16086092ec516c64dbd` adds a structured feature catalogue covering the AI assistant, planning and guarded tool execution, desktop operations UI, plugin/skill system, scheduling, Flutter companion, privacy/key management and explicit limitations. It also corrects the stale statement that retained-key retirement was not implemented and links the security threat model and operator runbook.

The README feature-catalogue update passed full CI in [#44328917878](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44328917878). Follow-up commit `59d5117457e6530b21ce60c732647e41d8b7b41e` adds explicit VAENC1-to-VAENC2 migration instructions and aligns the README test command with the CI suite.


## Additional reliability hardening — file writes and undo recovery

A further source review found that the scheduled file-write path could continue after an undo snapshot failed and could silently ignore a failed undo-journal save. The same journal-save failure was also swallowed in the interactive file-write path. Both paths now share failure handling: writes are blocked unless a valid undo snapshot/new-file marker is created; if the undo entry cannot be persisted after a successful write, the pre-write state is restored immediately. If rollback also fails, the result is marked critical and explicitly instructs the operator to inspect the target and recovery snapshot.

`UndoManager` now:
- retains undo entries when snapshot decryption, restore, or verification fails;
- verifies restored bytes before consuming the undo entry;
- refuses snapshots outside the managed undo directory and rejects symbolic links;
- preserves the snapshot if undo-stack cleanup cannot be persisted;
- removes only newly-created empty folders, never recursively deleting non-empty user folders.

Regression coverage is included in `tests/test_scheduled_write_undo_safety.py`. The latest Windows Python CI run reports 192 passed and 16 smoke checks. The full cross-platform run for the current `main` commit is [#44332948180](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44332948180); Android build is running and iOS is queued at the time of this report update.

## GitHub Actions runtime modernization

The CI workflow now uses Node 24-compatible major releases for checkout, Python/Java setup, artifact upload/download and GitHub Release publishing. The Python job no longer reports the earlier Node 20 deprecation warning. The current workflow has passed Python compile/tests and Flutter analysis/tests; Android/iOS completion for the latest commit remains pending. The tag-only release-publishing job is intentionally not exercised on branch pushes because doing so would publish a release artifact.


## Continuation checkpoint — 2026-11-19

This section supersedes earlier statements in this report that the Android build or iOS validation for the current main branch were still pending.

- **Source of truth:** `main` at `3505b58b4db3a53fe2408411d85783667f070fb8`.
- **Latest CI:** [run #44333839694](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44333839694) — overall conclusion `success`.
- **Python Windows:** passed; compileall, security/reliability regression suite and Phase 0 smoke checks all succeeded.
- **Flutter/Android:** dependency resolution, analysis, Flutter tests and release APK build all succeeded.
- **iOS:** unsigned iOS build succeeded.
- **Release publishing:** correctly skipped on a branch push; no release was published.

### Remaining work — do not confuse CI with runtime certification

1. **Windows desktop acceptance:** install from a clean checkout using supported Python 3.11, validate setup/upgrade, UI startup/shutdown, text chat, Ollama unavailable/restart/model states, microphone/STT, TTS, and representative tool/undo workflows.
2. **Physical mobile acceptance:** install on real Android and iOS devices; verify secure credential storage, pairing, revocation, reconnect, WSS/TLS, role restrictions, approval routing, schedule state and disconnect cleanup.
3. **Recovery exercise:** in an isolated test profile, verify portable backup authentication, restore to a clean destination, recovery-kit import, interrupted migration recovery, and keyring-loss handling. Never experiment on the user's live runtime data.
4. **Security review:** independent review/penetration test remains outstanding. Only the explicit AppContainer pilot is OS-isolated; default-worker plugins retain same-user privileges and publisher trust is unsigned. Direct remote WS is not encrypted; ordinary runtime writers do not participate in the key-administration lock.
5. **Reliability/product backlog:** propagate stable execution IDs into third-party side-effecting integrations and add external reconciliation; continue typed-exception cleanup and malformed legacy-store recovery; add process-wide mobile registry/quota coordination; improve desktop/mobile progress, diagnostics, accessibility and release packaging/signing.
6. **Release controls:** retain development/beta status until Windows and physical-device acceptance, recovery drill, security sign-off, and production signing/installer rollback are complete.

### Current recommendation

All repository CI gates are green at this checkpoint. Continue with a source-level security/reliability audit and targeted regression tests, while treating Windows hardware/runtime checks, physical-device checks, recovery drills and independent security review as external acceptance gates requiring the user's environment or an authorized test environment. No claim of production certification is made.


## Continuation checkpoint — mobile registry concurrency hardening (2026-11-19)

A source-level review identified that the encrypted per-device registry used only a process-local thread lock. Two independently started desktop processes sharing the same Windows profile could therefore race while enrolling/revoking devices or changing roles. In particular, concurrent administrator revocations could both observe two active administrators and both remove one, violating the last-active-admin safeguard.

The registry now combines its existing in-process re-entrant mutex with an OS-backed exclusive lock on a stable sidecar file. Registry read/modify/write operations, authentication reads, role changes, revocation and device listing are serialized across processes. The sidecar lock file is deliberately retained after release; deleting it would risk different processes locking different file objects. Windows uses the standard-library `msvcrt` locking API and POSIX uses `fcntl.flock`. Unsupported platforms fail closed instead of silently reverting to a thread-only lock.

New Windows CI regressions use separate spawned Python processes to verify:
- simultaneous device enrollment preserves both records (no lost update);
- simultaneous revocation of two administrator devices cannot remove the last active administrator;
- the stable lock sidecar is created.

The new test module is included explicitly in the Windows CI regression command. This hardening does not coordinate multiple machines or network filesystems, and it does not turn the desktop application into a supported multi-instance service. The intended deployment remains one trusted desktop instance per user profile.

### Verification status

- Merged to `main` via [PR #16](https://github.com/syedfahadiqbal7/VOICEASSISTANT/pull/16), merge commit `bb9d13dab3b137fc7173248e1a2d6182a6b571b6`.
- Pull-request CI [#44471469317](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44471469317) passed: Windows compile + 194 Python regression tests + 16 Phase 0 smoke checks; Flutter analysis/tests + Android release APK build; unsigned iOS build.
- The Android artifact is a CI build, not physical-device acceptance; iOS remains unsigned.


## Continuation checkpoint — mobile rate limiting across connections (2026-11-19)

The previous WebSocket limiter instantiated a separate sliding-window bucket for every accepted connection. A single authenticated device could therefore multiply its effective quota by opening additional sockets. The limiter is now process-wide and keyed by the authenticated server-side device identity, so all WebSocket connections for one device share the same total-message and user-message quotas. Different enrolled devices retain independent quotas.

The new `mobile_api/rate_limiter.py` provides:
- monotonic-clock sliding windows with a shared per-device total-message quota and a separate user-message quota;
- thread-safe quota consumption so simultaneous socket handlers cannot race past the limit;
- independent buckets per device identity;
- bounded bucket storage (4,096 identities by default); expired identities are reclaimed, while new identities are denied closed if the table remains full rather than evicting an active quota and resetting an attacker's allowance;
- distinct handling for user-message throttling (reject message, retain socket) versus total-device flooding (reject and close the offending socket);
- every authenticated request attempt, including a user message rejected by the narrower user-message quota, consumes the total-message budget. This prevents unlimited rejected requests from producing an unbounded stream of error responses.

The WebSocket endpoint now uses this shared limiter after authentication and role authorization. The existing default quotas remain 120 messages and 20 user messages per 60-second window. The desktop mobile API is a single-process service; this change coordinates sockets and threads in that process, not multiple server machines or independently deployed worker processes. Legacy shared-token and loopback clients intentionally share the quota associated with their common server identity.

Regression coverage includes multi-socket quota sharing, device isolation, user-message limits, window expiry, concurrent message attempts, bounded identity capacity, stale bucket reclamation, invalid configuration and malformed identities. The new test module is included in the Windows CI command.

### Verification status

- Source changes and tests are committed to `hardening/mobile-per-device-rate-limits`.
- Pull-request CI is pending; no pass is claimed until the workflow completes.


## Continuation checkpoint — scheduler shutdown lifecycle (2026-11-19)

A scheduler worker executes handlers synchronously on its daemon thread. The prior shutdown method joined for two seconds but returned no indication whether the worker had actually stopped. The desktop close path could therefore proceed to shut down TTS and destroy the UI while a scheduled handler was still executing, without recording that the grace period had expired.

Changes on `reliability/scheduler-shutdown-lifecycle`:
- `JobScheduler.stop(timeout=2.0)` now returns a boolean: true only when the worker is no longer alive; false when the timeout expires or the caller is the active worker thread.
- The scheduler does not create a competing worker when `start()` is called while an earlier handler is still running. A later restart is allowed after that worker exits.
- Desktop shutdown now requests scheduler stop before shutting down TTS, allowing the scheduler its grace period while speech output is still available.
- If a handler outlives the grace period, the desktop emits a structured `shutdown.scheduler_timeout` warning. It does not claim the handler was cancelled or that external side effects stopped.
- Regression tests cover stop with no worker, a blocked handler exceeding the grace period, no competing worker on premature restart, and restart after the original worker exits.

This remains cooperative/bounded shutdown, not forced cancellation. Synchronous tool handlers cannot safely be terminated by Python; integrations with long-running external effects need their own cancellation/timeouts and idempotency controls.

### Verification status

- PR #18 merged to `main` as `1753a5251996e0f95d9f4f05cef42e0b4fdbb318`.
- PR CI [#44535538127](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44535538127) passed Windows compile/regressions/Phase 0 smoke tests, Flutter analysis/tests + Android APK build, and unsigned iOS build.
- Shutdown lifecycle changes are submitted for CI in the next PR; no validation pass is claimed yet.



## Continuation checkpoint — truthful scheduler status in desktop UI (2026-11-19)

The desktop scheduler list previously inferred status only from the legacy enabled flag, rendering every enabled job as `ON` and every disabled job as `DONE`. This hid the scheduler's real persisted lifecycle states and could make an actively executing job appear cancellable even though the scheduler correctly rejects cancellation while a handler is running.

The UI now uses a pure, independently testable presentation helper:
- Distinguishes Pending, Running, Completed, Failed and Cancelled from the persisted scheduler status.
- Shows next-run time for pending jobs, start time for running jobs, last-run time for terminal completed/failed jobs, retry attempts and a whitespace-normalized, bounded last-error excerpt.
- Offers the Cancel action only for pending jobs in both desktop and Flutter mobile schedule views. Running work is not represented as cancellable; terminal and unknown states do not expose a misleading action.
- Preserves enabled-based status inference only for legacy records that have no explicit status. Unknown explicit states display as Unknown and fail closed for cancellation.
- Adds focused pure-helper regression tests for desktop and Flutter mobile cancellation visibility; the desktop tests are included explicitly in the Windows CI command and the mobile tests run under Flutter CI.

This is a presentation correction; it does not change scheduler execution, retry, persistence, or cancellation semantics. The desktop UI still requires Windows runtime acceptance.

### Verification status

- Changes are on branch `ui/scheduler-accurate-job-status`.
- Awaiting pull-request CI; no pass is claimed until the workflow completes.


## Continuation checkpoint — scheduled tool execution idempotency and crash ambiguity (2026-11-20)

The scheduler already persisted a stable `execution_id`, but the built-in scheduled fast-path tool invocation did not consume it. A retry or process restart could therefore invoke the same open-app/file-write action again. The scheduled tool path also treated a failed fast-path action as an unhandled reminder, potentially reporting the job as completed even though the requested tool action failed.

### Changes implemented on the PR branch

- Added `agent/scheduled_execution_ledger.py`, an encrypted-at-rest local ledger using the existing authenticated storage layer and an OS-backed sidecar lock for atomic read/modify/write across desktop processes. It persists an invocation claim before a scheduled tool is called and records success only after required post-execution bookkeeping. Recorded owner PID/process-start identity distinguishes a live invocation from a stale claim after process exit.
- Repeated IDs already marked successful are treated as successful no-ops. An invocation still active in this process is deferred without consuming retry budget or changing the logical occurrence ID.
- A persisted `started` record found after process restart is treated as ambiguous, not as permission to replay. The scheduler marks that occurrence failed for manual reconciliation rather than blindly repeating a potentially completed external effect.
- Tool errors after invocation are recorded as uncertain and are not automatically retried. Policy denials and undo-snapshot failures that occur before the tool invocation remain retryable.
- Added `JobExecutionResult` to distinguish normal retryable failures, terminal ambiguous failures and in-progress deferrals while retaining backward compatibility with existing bool/None scheduler handlers.
- A deferred job is presented as Waiting in desktop and mobile schedule views, and cancellation is blocked while its callback remains active.
- Delayed approval callbacks are rejected if the approval wait already timed out; duplicate approval callback invocations are ignored.
- Corrected the scheduled fast-path result handling so a recognized tool action that fails is not silently reinterpreted as a reminder.
- Added focused ledger, scheduler and ConversationManager regression tests; added the new Python test modules to Windows CI. Updated README scheduler guarantees and limitations.

### Reliability boundary — do not overstate guarantees

This is an at-most-once invocation guard for the built-in scheduled tool fast paths, not exactly-once external effect delivery. A crash can occur after the OS/tool has performed an action but before the ledger records success. That outcome is intentionally failed closed and requires operator reconciliation. Reminders and arbitrary external scheduler callbacks remain at-least-once; third-party integrations must enforce the stable `execution_id` at their own side-effect boundary. The ledger is local to the desktop profile and is not a distributed multi-server idempotency service. Its bounded capacity is 20,000 occurrence IDs; when full, new scheduled tool side effects are blocked rather than run without a durable claim.

### Verification status

- Branch: `reliability/scheduled-execution-idempotency`.
- New tests: `tests/test_scheduled_execution_ledger.py` and `tests/test_scheduled_tool_idempotency.py`; scheduler deferral/cancellation and UI waiting-state regressions were added to their existing suites.
- Windows CI and Flutter CI are pending PR execution. No test pass or runtime acceptance is claimed yet.


---

# Continuation checkpoint — 2026-11-24

This addendum supersedes earlier pending-state notes where the corresponding work has since been merged. It records repository evidence only; target-machine and physical-device acceptance remain external gates.

## Merged changes since the 2026-11-20 checkpoint

### Encrypted personal profile and preference management — PR #28

- Added `agent/user_profile.py`: schema-validated, bounded and encrypted local preferences with OS-backed cross-process file locking.
- Added an opt-in assistant-context projection limited to general interests, career targets, industries, news topics and language-learning preferences. Birthday, wardrobe and shopping-watchlist records are excluded from model context.
- Added the desktop Profile editor and explicit refresh of the current conversation's system context after save.
- Added profile and scheduler-execution-history stores to backup/restore and registered key-migration inventory.
- Added optional retention windows for the profile and scheduler execution history; retention remains disabled by default.
- Birthday entries store month/day only, without a birth year. The feature does not connect external accounts or synchronize profile data to mobile/cloud.

**Verification:** PR #28 merged to `main` as `0f871e678c9b15ef6c74c1f89a26e7eb0fa28317`. CI run [#45328657247](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45328657247) passed **305 Python regression tests**, **16 Phase 0 smoke checks**, Flutter analysis/tests, Android APK build and unsigned iOS build. The first CI attempt exposed a missing test import; the import was fixed and the full workflow rerun passed.

### Mobile registry symlink-path hardening — PR #29

- The encrypted mobile-device registry and its OS-lock sidecar now reject symbolic links in every existing path component.
- Paths are checked before and after lock acquisition to reduce redirection race exposure.
- Added regression tests for symlinked registry-file and lock-sidecar paths. Existing encryption, process/thread locking, device RBAC and legacy compatibility remain unchanged.

**Verification:** PR #29 merged to `main` as `b1d6c96d35cf35a569684b16a7db3bb7076246b3`. CI run [#45329401793](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45329401793) passed **307 Python regression tests**, **16 Phase 0 smoke checks**, Flutter analysis/tests, Android APK build and unsigned iOS build.

## Current repository checkpoint

- Source of truth: public GitHub repository `syedfahadiqbal7/VOICEASSISTANT`, branch `main`.
- Latest verified main commit at this checkpoint: `b1d6c96d35cf35a569684b16a7db3bb7076246b3`.
- The feature catalogue and setup guidance remain in the root README.
- The original uploaded ZIP remains an older preserved snapshot and is not used as the implementation source.

## Remaining acceptance and engineering boundaries

1. **Windows desktop acceptance:** GUI layout, profile editor interaction, microphone/STT/TTS, Ollama lifecycle, Windows process/tool integrations and shutdown behavior still require testing on the target Windows machine.
2. **Physical mobile acceptance:** Android/iOS pairing, secure-storage behavior, reconnect, approval routing, microphone permissions and real LAN/WSS behavior require physical-device tests.
3. **Plugin isolation:** the default worker remains same-user crash containment, while the JSON formatter and no-permission hello example are explicitly AppContainer-isolated. Manifest, permission, archive and checksum checks alone do not provide an OS sandbox or verified publisher identity.
4. **Network security lifecycle:** use VPN or TLS/WSS for remote access. Certificate provisioning/rotation, certificate pinning and per-device client certificates are not implemented.
5. **Release signing:** Android CI artifacts remain debug-signed; iOS CI builds remain unsigned. Production distribution requires protected Android signing and Apple signing/provisioning plus a Windows installer/signing and rollback plan.
6. **Scheduler guarantees:** built-in scheduled fast-path tools use a durable at-most-once invocation guard, but arbitrary callbacks/reminders remain at-least-once. External side-effecting integrations must enforce their own idempotency keys.
7. **Security assurance:** independent penetration testing and formal cryptographic review remain outstanding. CI demonstrates regression coverage, not certification.

## Next engineering priority

Continue source-level security hardening without weakening existing compatibility: complete the staged AppContainer rollout, expand only reviewed broker operations, verify ACL/profile cleanup and resource-exhaustion controls, and migrate compatible plugins individually. Do not describe the default subprocess worker as a complete OS sandbox. Keep physical-device, target-Windows runtime, recovery-drill and independent-security-review items as explicit external acceptance gates.


### Plugin worker crash-containment implementation — PR #32

- Third-party plugin module imports, class construction, metadata evaluation and tool execution now run in one-shot child processes rather than inside the desktop Python process.
- The host retains manifest/checksum verification, declared-tool parity, duplicate-name checks, permission metadata and the existing `BaseTool`/OpenAI schema contract through host-side proxy objects.
- Worker IPC uses a versioned JSON envelope over stdin/stdout, bounded request/response payload checks, source SHA-256 revalidation before import, categorical error responses and a configurable execution timeout.
- Added Windows CI regressions for process separation, source-tamper rejection, timeout handling and symlinked plugin source rejection. Updated the explicit CI test list so the new test module is actually executed.

**Verification:** [PR #32](https://github.com/syedfahadiqbal7/VOICEASSISTANT/pull/32) merged to `main` as `8383c0dba99a35d5a11046fa0a136d3371867d63`. CI run [#45333862364](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45333862364) passed **311 Python regression tests**, **16 Phase 0 smoke checks**, Flutter analysis/tests, Android APK build and unsigned iOS build.

**Security boundary:** This is crash containment only, not an OS sandbox. Workers still run with the desktop user's privileges. They can access resources available to that user; the worker has no restricted token/AppContainer, Job Object resource enforcement, network isolation or filesystem ACL boundary. A child process may still perform direct OS operations. Plugin packages must remain trusted. The 1 MiB protocol checks do not replace OS memory/disk quotas against hostile code. Do not advertise this phase as plugin sandboxing.

## Next plugin security phase

Implement and test the host-owned capability broker, then implement Windows restricted-token/AppContainer containment with explicit filesystem ACLs, network denial by default, process/resource limits and process-tree termination. The current subprocess worker is an intermediate compatibility/crash-containment step only. Do not enable untrusted third-party plugins until negative OS-boundary tests and independent security review pass.


### Windows worker process/resource containment — PR #34

- Windows plugin workers are attached to a Job Object while blocked on stdin, before the host releases the request and plugin code can run.
- The Job Object enforces kill-on-close, a 16-process active-process limit, 2 GiB per-process memory and 3 GiB aggregate job memory.
- POSIX workers run in a separate process group; timeout terminates the group.
- Stdout is drained with a 1 MiB cap; an oversized response terminates the worker.
- Added regression coverage proving timeout also terminates a child process spawned by a plugin.

**Verification:** [PR #34](https://github.com/syedfahadiqbal7/VOICEASSISTANT/pull/34) merged as `d2b13f710b0d3456fb2d0a9a28e160b25dfe270c`. CI run [#45335729490](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45335729490) passed **312 Python regression tests**, **16 Phase 0 smoke checks**, Flutter analysis/tests, Android APK build and unsigned iOS build.

**Remaining security gap:** Job Objects are process/resource controls, not an OS security boundary for file/network access. Workers still run as the desktop user. A restricted token/AppContainer, explicit ACL isolation, default-deny networking and broker-only resource access are still required before untrusted plugins can be supported.

## AppContainer plugin pilot and registry routing — 2026-11-24

**Implemented in the current development branch; PR #46 is awaiting merge.**

- Added a Windows AppContainer runner with no network capabilities, scoped ACL grants for the Python runtime/staged plugin/private IPC directory, bounded file IPC, timeout and Job Object process/resource limits.
- Added a package-SID ACL named-pipe transport and host-owned per-invocation capability broker. The broker currently exposes policy-approved filesystem reads only; unsupported operations remain default-deny.
- Routed plugin metadata discovery and every invocation through AppContainer when the verified manifest declares `execution_sandbox: appcontainer`. Failure never falls back to the same-user worker.
- Marked `json_formatter.py` and `example_hello_tool.py` as reviewed no-permission AppContainer plugins. All other plugins retain the default same-user crash-contained worker until separately reviewed and migrated.
- The AppContainer runner rejects unsupported imports, native/dynamic APIs, undeclared capabilities and permissions outside the current broker contract. Python starts with `-I -S`; the trusted worker clears inherited environment variables before importing plugin code.
- Added regressions for manifest generation/validation, dynamic-import detection, no-fallback behavior, AppContainer metadata discovery and real plugin execution.

**CI verification before merge:** [run #45399119080](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45399119080) — **344 Python tests passed**, **16 Phase 0 smoke checks passed**, Flutter analysis/tests passed, Android APK build passed and unsigned iOS build passed. PR #46 was subsequently merged; post-merge validation is recorded in [run #45414269392](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/45414269392).

**Remaining gaps:** this is not blanket sandboxing. Additional plugins need dependency/capability migration; a bounded AppContainer IPC workspace watchdog and best-effort ACL/profile cleanup logging are implemented; this is not a hard NTFS volume quota and target-machine disk-pressure/ACL cleanup verification remains outstanding. Publisher signatures and independent security review are also outstanding. Validate on the target Windows machine before production use.



## 10. Mobile connectivity boundary (reviewed after AppContainer merge)

- The current mobile companion connects to the desktop WebSocket API. The desktop API defaults to loopback; trusted-LAN access is an explicit host-binding configuration.
- Cross-network access is possible when the operator provides network reachability through a VPN or a TLS-protected `wss://` reverse proxy/direct TLS endpoint. The application does not currently provide a built-in internet relay service.
- The desktop now displays a QR code with a short-lived random challenge; the Flutter app scans it, submits a pairing request over HTTPS and waits for explicit desktop approval.
- QR pairing creates a normal per-device entry in the encrypted registry with the least-privilege `operator` role. QR cannot grant `admin`; owner-admin enrollment remains a trusted local CLI operation.
- The challenge is single-request, expires after two minutes by default, is held only in memory, is limited to five invalid-secret attempts and is invalidated on desktop restart. Device credentials are stored by the phone in platform secure storage before an idempotent receipt acknowledgement. If receipt is never acknowledged, the provisional device is revoked on expiry, cancellation or challenge lockout.
- Pairing administration endpoints are loopback-only and reject browser-originated requests. Mobile credential exchange endpoints require HTTPS; the QR contains no permanent device token.
- Cross-network connectivity still requires a reachable trusted HTTPS/WSS endpoint supplied through a VPN, direct TLS or a user-managed TLS reverse proxy. QR scanning alone cannot traverse NAT/firewalls. A hosted relay and automatic NAT traversal remain unimplemented.
- Recommended future architecture: retain direct LAN and user-managed VPN/WSS, and add an optional hosted or self-hosted relay using outbound authenticated TLS connections from both endpoints. Relay payloads should be end-to-end authenticated/encrypted where feasible, with no relay access to desktop authorization decisions or stored assistant data.


## Continuation checkpoint — scheduled execution ledger identity hardening (2026-11-25)

- Scoped the in-memory active-claim set by canonical ledger path plus execution ID, so separate ledger files cannot accidentally treat each other's claims as active.
- Bound each execution ID to the original tool name; reuse with a different tool now fails closed.
- Ledger loading rejects entries without a valid tool binding rather than interpreting malformed metadata permissively.
- Added regression tests for independent ledgers sharing an ID, tool-binding mismatch and malformed ledger entries.
- Added [Scheduled Execution Reliability](SCHEDULED_EXECUTION_RELIABILITY.md) documenting at-most-once invocation, at-least-once callback behavior, ambiguous crash outcomes and the manual reconciliation boundary.

**Remaining:** this ledger is local to one desktop profile and has a 20,000-entry fail-closed capacity. It is not a distributed idempotency service. External plugins must propagate the stable execution ID to their provider or implement their own reconciliation.


## 11. AppContainer resource and cleanup hardening (2026-11-25)

- Reduced the Job Object ceilings to 512 MiB per process, 1 GiB per job and eight active processes. Wall-clock timeout is strictly validated from 1 to 120 seconds.
- The AppContainer writable workspace is a flat directory with non-inheritable directory modify access needed for Windows atomic response-file replacement; request/response files have explicit ACLs.
- A 50 ms watchdog samples the private IPC workspace, rejecting more than three files, any file over 1 MiB or aggregate usage over 2 MiB. Overshoot between samples is possible; this is not a hard filesystem quota.
- Source files are rejected above 5 MiB before staging. Response size is checked from file metadata before loading it into host memory.
- ACL removal retries and reports failures. Broker shutdown, each ACL removal, profile deletion, SID release and workspace deletion are isolated so one cleanup failure does not skip subsequent cleanup.
- Static plugin inventory distinguishes read-only `open(..., "r")` from write/append/update modes and treats dynamic open modes conservatively as read+write.
- Remaining gate: validate ACL cleanup and disk-pressure behavior on supported Windows 10/11 target builds, including forced termination and cleanup failure injection. A watchdog does not replace independent Windows security review.

## 12. Mobile TLS certificate preflight and reverse-proxy trust

- Direct TLS startup validates the PEM X.509 certificate, matching private key, validity window and (when configured) Subject Alternative Name against `VOICEASSISTANT_MOBILE_PUBLIC_URL`.
- Expired, not-yet-valid, malformed, mismatched-key and public-host mismatch configurations fail startup. Certificates with 30 days or fewer remaining produce a renewal warning.
- TLS certificate renewal is restart-based; Uvicorn does not hot-reload the configured certificate files. Reverse-proxy TLS is renewed/reloaded at the proxy.
- Uvicorn forwarded-header trust is explicit: loopback-only by default, configurable through `VOICEASSISTANT_TRUSTED_PROXY_IPS`, and rejects wildcard/world-wide networks.
- Remaining: validate certificate renewal/restart and proxy-forwarded scheme behavior on target Windows deployments; this preflight does not perform online OCSP/CRL revocation checks or CA-chain trust verification on behalf of mobile clients.
