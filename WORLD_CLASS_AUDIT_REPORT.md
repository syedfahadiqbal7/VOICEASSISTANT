# Voice Assistant — Comprehensive Engineering Audit & Hardening Report

**Assessment mode:** source inspection + isolated regression testing  
**Development target:** separate copy of the supplied archive  
**Original archive:** not modified  
**Runtime certification:** not completed on the user's Windows machine

## 1. Executive summary

The repository is a functional prototype / advanced local-agent foundation, not yet a production-certified enterprise application. It contains a Windows desktop assistant, Ollama-compatible LLM integration, planner/executor, tool registry, local history and memory, scheduler, policy/undo mechanisms, 55 plugin source files and a Flutter mobile client.

A focused security and reliability hardening pass has been implemented in this development copy. The highest-priority fixes address mobile API exposure and approval binding, policy fail-open behavior, canonical file path validation, plugin loading integrity, PowerShell result verification, undo snapshot failures, shared conversation concurrency and atomic JSON persistence.

The source compiles at the Python syntax level and the available Python regression suites pass. A complete Windows desktop run, audio hardware test, Ollama request, Android/iOS build and Flutter analyzer run remain unverified because this execution environment does not have the required Windows runtime, audio hardware or Flutter/Dart SDK.

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
| `tests/` | 2 | 607 |
| Startup/legacy/template files | 10 | 1,796 |

The full tree contains 108 Python files and approximately 16,665 Python source lines (732 functions and 107 classes). The plugin folder contains 55 tool/plugin Python files and 55 sidecar manifests; one legacy scheduler plugin remains intentionally disabled.

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
10. **Permission metadata.** Shipped high-impact plugin manifests now declare relevant file, process, screen, network, service, environment, messaging and clipboard permissions. Read-only policy rejects declared mutating capabilities; PlanExecutor also refuses to auto-approve a tool whose manifest declares mutating permissions.
11. **API key handling.** Cloud LLM API keys are no longer persisted to the failover JSON file; use `VOICEASSISTANT_CLOUD_API_KEY`.
12. **No placeholder API key.** The example failover config no longer contains a fake `sk-...` value.

### Reliability and data integrity

1. PowerShell execution has a 120-second timeout, kills timed-out processes and returns an exit code.
2. `command_exit_zero` verification requires an actual exit code; it no longer treats missing evidence as success.
3. Unavailable window-title verification now fails as unverifiable instead of passing.
4. Undo snapshot failures block the write rather than allowing an overwrite without rollback protection; Desktop-relative and placeholder paths are normalized consistently for both policy and undo snapshotting.
5. Conversation requests are serialized to protect shared messages, queues and cancellation state from simultaneous desktop/voice/mobile turns.
6. JSON-backed history, audit, policy, scheduler, undo, memory, skill-pack and LLM configuration writes use an atomic temp-file-and-replace helper.
7. Scheduler job-list access is guarded by a lock.
8. Desktop shutdown now stops TTS, the scheduler and global hotkeys before destroying the window.
9. STT and TTS initialization fail independently; text mode remains available if voice services fail.
10. Missing `openai` import no longer prevents importing non-LLM agent components; an actual LLM request reports the missing dependency.

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

The initial enforcement pass found a mismatch between plugin manifest `tools` values and actual `BaseTool.name` values. This would have disabled existing browser, Office, VS Code and workflow tools. The shipped manifest declarations were corrected rather than weakening validation.

A real `ToolRegistry()` load in an isolated copy registered **61 tools** (including the three built-in tools). The registry intentionally rejects the duplicate `open_app` class exported by the Office plugin because the dedicated `open_app` plugin already owns that name. The legacy `task_scheduler.py` remains disabled.

## 6. Verification performed

| Check | Result | Notes |
|---|---:|---|
| Python AST / compile check | Pass | 108 Python files; zero syntax errors |
| Focused security/reliability tests | 25/25 pass | Run on an isolated copy |
| Existing Phase 0 smoke suite | 16/16 pass | Run on an isolated copy so undo/history data is not changed |
| Plugin manifest/checksum scan | 55/55 pass | Checks shipped Python file checksums and manifest shape |
| Runtime plugin registry load | 61 tools registered | One legacy plugin disabled; duplicate `open_app` rejected |
| Flutter `flutter analyze` / `flutter test` | Not run | Flutter/Dart executable unavailable in this environment |
| Android/iOS build | Not run | Requires local SDK/toolchain and accepted Android licenses |
| Windows desktop startup | Not run | Requires Windows, desktop dependencies and GUI session |
| Microphone/STT/TTS | Not run | Requires supported audio devices and model download |
| Ollama / cloud provider | Not run | Requires running Ollama/model or configured cloud credentials |

## 7. Remaining production risks (not represented as fixed)

### P1 — complete before exposing to a wider network

- **No TLS in the direct mobile transport.** The current Flutter client uses `ws://`. A shared token on an untrusted network can be sniffed/replayed. Use a VPN or TLS-terminating `wss://` proxy before any untrusted-network deployment.
- **Shared-token identity model.** Authentication is one shared secret, not per-device credentials, certificate identity, revocation or user roles. Rotate it if a phone is lost.
- **Plugin sandboxing is absent.** Plugins execute in the desktop Python process with the user's OS permissions. Checksums are integrity checks, not cryptographic publisher signatures; manifests are self-declared metadata.
- **Permission declarations are not a sandbox.** Read-only policy now blocks declared mutating permissions, but a malicious or incorrectly declared plugin can understate its capabilities. Require code review and explicit trust before adding third-party plugins.
- **Command analysis remains heuristic.** The command analyzer is not a PowerShell sandbox. High-impact commands need OS-level least privilege and strong approval UX.

### P1/P2 — reliability and privacy

- **Scheduler crash window.** One-time jobs are marked disabled before their handler executes. A process crash in that interval can lose the scheduled action. A durable Pending/Running/Completed/Failed state machine with idempotency is still needed.
- **Plaintext local data.** Conversation history, audit log, memory, scheduler state and undo metadata are JSON files without application-level encryption at rest. Protect the Windows profile with BitLocker/ACLs and define retention/redaction policies.
- **Broad exception handling remains.** Static scan found 337 exception handlers, including 310 `except Exception` handlers and 75 handlers whose only statement is `pass`. These are not all defects, but they need a dedicated exception-observability pass with structured logs and typed recovery behavior.
- **No comprehensive integration suite.** Mobile/desktop end-to-end workflows, scheduler restart recovery, file-write rollback under disk failure, concurrent cancellation, and external plugin integrations still need automated Windows tests.
- **History corruption handling.** Some JSON loaders still return empty defaults after parse errors. A quarantine/restore flow should be implemented consistently across all persisted stores.
- **No rate limiting per authenticated mobile connection.** Message and frame size limits are in place, but per-client request quotas and connection limits are still recommended.

### P2 — product and UX

- Add per-device pairing/revocation UI and token rotation workflow.
- Add visible provider status and actionable missing-dependency diagnostics to the desktop UI.
- Add structured task progress, cancellation state and retry visibility across desktop and mobile.
- Add scheduler retry/failed-job UI and explicit time-zone handling.
- Add mobile connection persistence via secure storage, optional TLS/WSS configuration and a connection diagnostics screen.
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

**Implemented:** focused security/reliability hardening and documentation in the separate development copy.  
**Verified:** Python syntax, focused Python regressions, Phase 0 smoke suite and runtime plugin registry load.  
**Not certified:** Windows desktop runtime, audio, Ollama, Flutter analysis/build, Android runtime, TLS/mobile production deployment and external integrations.

This is a meaningful engineering hardening release candidate, not a claim that every original feature is production-ready or that all remaining findings have been resolved.
