# Voice Assistant — Comprehensive Engineering Audit & Hardening Report

**Assessment mode:** source inspection + isolated regression testing  
**Development target:** GitHub repository main branch; original uploaded archive remains untouched  
**Original archive:** not modified  
**Runtime certification:** not completed on the user's Windows machine

## 1. Executive summary

The repository is a functional prototype / advanced local-agent foundation, not yet a production-certified enterprise application. It contains a Windows desktop assistant, Ollama-compatible LLM integration, planner/executor, tool registry, local history and memory, scheduler, policy/undo mechanisms, 55 plugin source files and a Flutter mobile client.

A multi-phase security, reliability, privacy, mobile and desktop UX hardening pass has been committed to GitHub main. The desktop includes safer modal lifecycles, UI-thread dispatch, destructive-action confirmations, scheduler input validation and orderly restart behavior. Mobile includes connection-scoped approval, acknowledgement/retry behavior, message quotas, optional TLS and platform permission/backup declarations. In addition to mobile API/approval binding, policy fail-closed behavior, canonical write paths, plugin ZIP/manifest/permission enforcement, scheduler recovery and concurrency protection, private local stores now use AES-256-GCM at rest with DPAPI-backed Windows key protection. Portable passphrase-encrypted backup/restore and opt-in retention preview/purge CLIs are available.

Latest fully green cross-platform CI run [44038035438](https://github.com/syedfahadiqbal7/VOICEASSISTANT/actions/runs/44038035438) passes 92 Python regression tests, 16 Phase 0 smoke checks, Python compileall, Flutter analysis/tests, Android release APK build and unsigned iOS build. Mobile security includes secure credential storage, bounded reconnect, connection-scoped approval acknowledgements, optional WSS/TLS, per-client message quotas, an atomic connection cap and consent-first microphone permission. Real Windows desktop GUI/audio/Ollama operation and physical Android/iOS device behavior still require testing on target hardware.

## Phase completion map

| Phase | Scope | Status | Remaining boundary |
|---|---|---|---|
| 0.1–0.3 | Security baseline | Complete | Ongoing threat-model review still applies |
| 1 | Reliability and recovery | Complete for the implemented scope | Full real-desktop end-to-end validation remains |
| 2 | Plugin and integration hardening | Core hardening complete | Third-party sandboxing, publisher signatures and full dependency lifecycle remain |
| 3 | Persistence and privacy | Complete for the implemented scope | Retention is opt-in/manual; secure erasure and in-app privacy dashboard remain |
| 4 | Mobile companion | Source and CI validation complete | Physical device, real LAN and signed distribution testing remain |
| 5 | Desktop UI/UX and lifecycle | Source-level hardening complete | Interactive Windows GUI, audio and Ollama runtime validation remain |
| 6 | QA and observability | Foundation implemented; broader QA in progress | Performance/load, full E2E and remaining exception-handler cleanup remain |
| 7 | Deployment and release | Safety guard implemented; production release pending | GitHub tags now publish non-latest development pre-releases only; Windows installer and production Android/iOS signing remain |
| 8 | Enterprise capabilities | Pending | Plugin sandbox, per-device identity/revocation, roles and key-rotation lifecycle remain |

A phase marked complete here means its scoped source changes and automated checks are implemented; it does not imply physical-device or production-environment certification.

## 2. Source inventory

Post-hardening inventory (includes the new helper and regression test files):

| Area | Files | Approx. lines |
|---|---:|---:|
| `agent/` | 23 | Core orchestration, persistence, scheduler and observability |
| `audio/` | 3 | Speech capture and output |
| `mobile_api/` | 4 | FastAPI/WebSocket bridge and connection lifecycle |
| `plugins/` | 111 total (56 Python + 55 JSON manifests) | Plugin implementations and manifests |
| `tools/` | 4 | Built-in tools |
| `ui/` | 9 | Desktop UI and operational viewers |
| `security/` | 1 | Policy/security support |
| `tests/` | 8 | Regression suites, smoke tests and authoring docs |
| `voice_assistant_mobile/` | 137 | Flutter app, platform projects and tests |

The current tracked tree contains 336 files, including 118 Python files. The plugin folder contains 55 tool/plugin Python files and 55 sidecar manifests; one legacy scheduler plugin remains intentionally disabled.

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

The installer does not claim cryptographic publisher authentication or OS-level sandboxing. SHA-256 checks integrity against the included manifest; a malicious publisher can replace both. The `author` field is unverified. Plugins still execute inside the desktop process.

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

- **TLS certificate lifecycle and device identity.** Direct `wss://` can be configured with a certificate/key or TLS reverse proxy, and the Flutter client relies on the operating-system trust store. Certificate provisioning/rotation, certificate pinning, per-device client certificates and token rotation UI are not implemented. Plain `ws://` remains available after explicit confirmation and must be restricted to a trusted private LAN/VPN.
- **Shared-token identity model.** Authentication is one shared secret, not per-device credentials, certificate identity, revocation or user roles. Rotate it if a phone is lost. A device-specific enrollment/revocation flow is still required for wider deployment.
- **Mobile release signing.** Android's release build currently uses the debug signing configuration and iOS CI uses `--no-codesign`. Configure protected Android signing material and Apple signing/provisioning before public distribution.
- **Plugin sandboxing is absent.** Plugins execute in the desktop Python process with the user's OS permissions. Checksums are integrity checks, not cryptographic publisher signatures; the author field is unverified metadata.
- **Permission declarations are not a sandbox.** Read-only policy blocks declared mutating permissions and missing permissions fail closed to `system:admin`, but a malicious plugin can understate its capabilities. Require code review and explicit trust before adding third-party plugins.
- **Command analysis remains heuristic.** The command analyzer is not a PowerShell sandbox. High-impact commands need OS-level least privilege and strong approval UX.

### P1/P2 — reliability and privacy

- **Scheduler delivery semantics.** A durable Pending/Running/Completed/Failed/Cancelled state machine and crash recovery are implemented. Delivery is at-least-once: if the process crashes after a handler performs an external side effect but before the completion checkpoint is persisted, the handler may run again after restart. Handlers that create files, send messages or otherwise mutate external state still need operation-level idempotency keys/duplicate detection before unattended production use.
- **Key recovery and rotation.** Windows DPAPI key material is bound to the current Windows user profile. Losing the DPAPI key file/profile makes raw encrypted runtime files unrecoverable. Portable backups can be restored on another machine using their separate passphrase, but key rotation for an existing live data set is not implemented. Explicit Markdown/text conversation exports remain plaintext and must be stored securely.
- **Retention and deletion semantics.** User-configurable retention is available through `privacy_cli.py` for conversation history, memory, audit entries and terminal scheduled jobs. It is manual and opt-in; undo snapshots, custom skills, exports and policy data are not covered. Deletion is not secure erasure, and there is no in-app privacy dashboard.
- **Backup transaction boundary.** Restore validates the full archive before writing and atomically replaces each file, but the set of files is not committed as one filesystem transaction. Take a fresh backup before restore.
- **Broad exception handling remains.** Static scan previously found 337 exception handlers, including 310 `except Exception` handlers and 75 handlers whose only statement is `pass`. These are not all defects. Structured, rotating, secret-redacting JSONL diagnostics are now configured for the desktop entry point and mobile API; broad handler cleanup and typed recovery remain incomplete.
- **No comprehensive end-to-end suite.** Real Windows desktop GUI/audio/Ollama workflows, Android device-level behavior, concurrent cancellation and external account/plugin integrations still need target-environment validation. Unit/regression coverage already exercises scheduler restart recovery and undo snapshot failure paths.
- **Malformed legacy data recovery.** Encrypted authentication failures fail closed. Malformed legacy plaintext JSON is not quarantined uniformly across every store; some stores raise a typed or validation error and require restore from a portable backup or manual repair.
- **Mobile connection quotas.** Per-connection sliding-window quotas limit total WebSocket messages and user prompts, and an atomic process-local concurrent-connection cap defaults to five (configurable 1–32). Per-device identity/revocation and cross-process limits are not implemented.

### P2 — product and UX

- Add per-device pairing/revocation UI and token rotation workflow.
- Add visible provider status and actionable missing-dependency diagnostics to the desktop UI.
- Add structured task progress and cancellation-state visibility across desktop and mobile.
- Add explicit time-zone handling for schedules and a user-controlled retry-now action for failed jobs.
- Add a mobile connection diagnostics screen; secure credential persistence, reconnect handling and optional TLS/WSS configuration are implemented.
- Add plugin dependency/version resolution, publisher signatures, hot-reload lifecycle and isolated worker-process sandboxing.
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
