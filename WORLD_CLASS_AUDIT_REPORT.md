# Voice Assistant — Comprehensive Engineering Audit & Hardening Report

**Assessment mode:** source inspection + isolated regression testing  
**Development target:** GitHub repository main branch; original uploaded archive remains untouched  
**Original archive:** not modified  
**Runtime certification:** not completed on the user's Windows machine

## 1. Executive summary

The repository is a functional prototype / advanced local-agent foundation, not yet a production-certified enterprise application. It contains a Windows desktop assistant, Ollama-compatible LLM integration, planner/executor, tool registry, local history and memory, scheduler, policy/undo mechanisms, 55 plugin source files and a Flutter mobile client.

A multi-phase security, reliability and privacy hardening pass has been committed to GitHub main. In addition to mobile API/approval binding, policy fail-closed behavior, canonical write paths, plugin ZIP/manifest/permission enforcement, scheduler recovery and concurrency protection, private local stores now use AES-256-GCM at rest with DPAPI-backed Windows key protection. Portable passphrase-encrypted backup/restore and opt-in retention preview/purge CLIs are available.

Latest validation confirms 70 Python regression tests and 16 Phase 0 smoke checks pass; Python compileall, Flutter analysis/tests and Android release APK build also pass in GitHub Actions. Real Windows desktop GUI/audio/Ollama operation and Android device-level behavior still require testing on target hardware.

## 2. Source inventory

Post-hardening inventory (includes the new helper and regression test files):

| Area | Files | Approx. lines |
|---|---:|---:|
| `agent/` | 19 | 5,415 |
| `audio/` | 3 | 136 |
| `mobile_api/` | 4 | 539 |
| `plugins/` | 56 Python files | 5,235 |
| `tools/` | 4 | 293 |
| `ui/` | 9 | 2,590 |
| `security/` | 1 | 54 |
| `tests/` | 5 | Expanded regression suites and authoring docs |
| Startup/legacy/template files | 10 | 1,796 |

The full tree currently contains 109 Python files. Line/function/class totals are not used as a release-quality measure and were not re-counted after Phase 2. The plugin folder contains 55 tool/plugin Python files and 55 sidecar manifests; one legacy scheduler plugin remains intentionally disabled.

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
10. Replaced the Flutter starter counter test with service-level tests for initial state, disconnected sends and transcript clearing.

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
| Python AST / compile check | Pass | 108 Python files; zero syntax errors |
| Focused security/reliability tests | 25/25 pass | Windows GitHub Actions |
| Scheduler durability/recovery tests | 10/10 pass | Crash recovery, retries, cancellation, concurrency and checkpoint failure |
| Plugin marketplace/security tests | 16/16 pass | ZIP traversal, checksum mismatch, symlink, overwrite, permissions, trust metadata, generator and registry checks |
| Local data protection and backup tests | 15/15 pass | AES-GCM, tamper/wrong-key detection, plaintext migration, encrypted stores/undo, portable restore across different keys, wrong-passphrase and ZIP-slip rejection |
| Privacy retention tests | 4/4 pass | Default-off policy, encrypted settings, preview, explicit purge, pending/unknown-record preservation and tamper detection |
| Combined Python regression tests | 70/70 pass | Security/reliability + scheduler + plugin + data-protection/backup + privacy suites |
| Existing Phase 0 smoke suite | 16/16 pass | Windows GitHub Actions |
| Plugin manifest/checksum scan | 55/55 pass | Checks shipped Python file checksums and manifest shape |
| Plugin manifest/export parity | 54/54 pass | AST check for all active plugin modules; legacy scheduler excluded |
| Python compileall | Pass | Windows GitHub Actions |
| Flutter `flutter analyze` / `flutter test` | Pass | Ubuntu GitHub Actions |
| Android release APK build | Pass | GitHub Actions Android release build succeeded; no release artifact published on branch push |
| Windows desktop startup / audio / Ollama | Not run | Requires the user's Windows desktop, audio hardware and local model runtime |
| Windows desktop startup | Not run | Requires Windows, desktop dependencies and GUI session |
| Microphone/STT/TTS | Not run | Requires supported audio devices and model download |
| Ollama / cloud provider | Not run | Requires running Ollama/model or configured cloud credentials |

## 7. Remaining production risks (not represented as fixed)

### P1 — complete before exposing to a wider network

- **No TLS in the direct mobile transport.** The current Flutter client uses `ws://`. A shared token on an untrusted network can be sniffed/replayed. Use a VPN or TLS-terminating `wss://` proxy before any untrusted-network deployment.
- **Shared-token identity model.** Authentication is one shared secret, not per-device credentials, certificate identity, revocation or user roles. Rotate it if a phone is lost.
- **Plugin sandboxing is absent.** Plugins execute in the desktop Python process with the user's OS permissions. Checksums are integrity checks, not cryptographic publisher signatures; the author field is unverified metadata.
- **Permission declarations are not a sandbox.** Read-only policy blocks declared mutating permissions and missing permissions fail closed to `system:admin`, but a malicious plugin can understate its capabilities. Require code review and explicit trust before adding third-party plugins.
- **Command analysis remains heuristic.** The command analyzer is not a PowerShell sandbox. High-impact commands need OS-level least privilege and strong approval UX.

### P1/P2 — reliability and privacy

- **Scheduler delivery semantics.** A durable Pending/Running/Completed/Failed/Cancelled state machine and crash recovery are implemented. Delivery is at-least-once: if the process crashes after a handler performs an external side effect but before the completion checkpoint is persisted, the handler may run again after restart. Handlers that create files, send messages or otherwise mutate external state still need operation-level idempotency keys/duplicate detection before unattended production use.
- **Key recovery and rotation.** Windows DPAPI key material is bound to the current Windows user profile. Losing the DPAPI key file/profile makes raw encrypted runtime files unrecoverable. Portable backups can be restored on another machine using their separate passphrase, but key rotation for an existing live data set is not implemented. Explicit Markdown/text conversation exports remain plaintext and must be stored securely.
- **Retention and deletion semantics.** User-configurable retention is available through `privacy_cli.py` for conversation history, memory, audit entries and terminal scheduled jobs. It is manual and opt-in; undo snapshots, custom skills, exports and policy data are not covered. Deletion is not secure erasure, and there is no in-app privacy dashboard.
- **Backup transaction boundary.** Restore validates the full archive before writing and atomically replaces each file, but the set of files is not committed as one filesystem transaction. Take a fresh backup before restore.
- **Broad exception handling remains.** Static scan found 337 exception handlers, including 310 `except Exception` handlers and 75 handlers whose only statement is `pass`. These are not all defects, but they need a dedicated exception-observability pass with structured logs and typed recovery behavior.
- **No comprehensive end-to-end suite.** Real Windows desktop GUI/audio/Ollama workflows, Android device-level behavior, concurrent cancellation and external account/plugin integrations still need target-environment validation. Unit/regression coverage already exercises scheduler restart recovery and undo snapshot failure paths.
- **Malformed legacy data recovery.** Encrypted authentication failures fail closed. Malformed legacy plaintext JSON is not quarantined uniformly across every store; some stores raise a typed or validation error and require restore from a portable backup or manual repair.
- **No rate limiting per authenticated mobile connection.** Message and frame size limits are in place, but per-client request quotas and connection limits are still recommended.

### P2 — product and UX

- Add per-device pairing/revocation UI and token rotation workflow.
- Add visible provider status and actionable missing-dependency diagnostics to the desktop UI.
- Add structured task progress and cancellation-state visibility across desktop and mobile.
- Add explicit time-zone handling for schedules and a user-controlled retry-now action for failed jobs.
- Add mobile connection persistence via secure storage, optional TLS/WSS configuration and a connection diagnostics screen.
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

**Implemented:** focused security/reliability hardening, durable scheduler recovery/retries, plugin ZIP/manifest/permission hardening, AES-256-GCM local persistence encryption, DPAPI/environment key handling, plaintext migration, fail-closed integrity checks, portable passphrase-encrypted backup/restore and opt-in retention preview/purge CLI.  
**Verified:** Windows Python compileall, 70 Python regression tests, 15/15 local data-protection/backup tests, 4/4 privacy-retention tests, 16 Phase 0 smoke checks, 55 plugin manifest/checksum checks, 54 plugin export-parity checks and Flutter analysis/tests. Android APK build is pending in CI 44027913630.  
**Not certified:** Windows desktop runtime, audio, Ollama, Flutter analysis/build, Android runtime, TLS/mobile production deployment and external integrations.

This is a meaningful engineering hardening release candidate, not a claim that every original feature is production-ready or that all remaining findings have been resolved.
