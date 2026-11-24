# Voice Assistant — Desktop Agent + Flutter Companion

A Windows-oriented local AI assistant with a CustomTkinter desktop UI, Ollama-compatible LLM integration, planning and tool execution, security approvals, local history/memory, scheduled jobs, plugins, audio input/output, a FastAPI/WebSocket mobile bridge, and a Flutter companion app.

## Application features

Voice Assistant combines a local-first Windows desktop AI agent with a Flutter companion app. The desktop remains the trusted execution and security authority; the mobile app is a companion interface, not a standalone agent host.

### 1. AI assistant and conversation
- Chat with an Ollama-compatible local language model; select/configure the model in the desktop application.
- Optional cloud-provider failover can be configured separately; it is disabled by default.
- Conversation history and local agent memory persist between sessions.
- Text and voice interaction: microphone capture, Faster-Whisper speech-to-text and pyttsx3 text-to-speech.
- Structured status/transcript feedback, including desktop-to-mobile session updates.

### 2. Planning, tools and controlled execution
- Convert user goals into structured multi-step plans with tool arguments, risk levels and success checks.
- Preview plans and request confirmation before multi-step or sensitive operations.
- Execute registered tools such as file/code writing, PowerShell/process operations and application launching.
- Verify outcomes after actions (for example file/directory existence, expected file content, process state and command exit status).
- Cancel execution, inspect step status and use undo/recovery support where the operation has a recorded inverse or snapshot.
- Apply local policy rules, tool allow/deny controls, risk gates and safe write-root restrictions.

### 3. Desktop workspace and operations UI
- CustomTkinter Windows desktop interface for conversations and agent interaction.
- Operational viewers for audit events, policy, scheduler jobs, undo history and file diffs.
- Encrypted local profile editor for interests, career targets, preferred locations, content tastes, language-learning level, lifestyle preferences, wardrobe items, favorite stores, product watchlists and birthday reminders.
- Assistant profile context is opt-in and includes only selected general preferences; birthday dates store month/day only, and profile validation rejects secret-like values. Profile data is not synchronized to mobile or cloud by this feature.
- Help and skill-authoring interfaces.
- Desktop lifecycle/restart handling and structured, redacted diagnostics.

### 4. Plugins and reusable skills
- Manifest-backed plugin/tool discovery and registration.
- Plugin permission declarations, integrity/checksum validation and archive/manifest validation.
- Built-in plugin capabilities cover common development, Windows administration, document/data handling, browser workflows, productivity and reusable work skills.
- Examples in the repository include Git/GitHub helpers, Docker/process/environment tools, file and PDF/Excel readers, document writing, email/Office helpers, browser and clipboard tools, and architecture/review/planning skills.
- Plugin source and manifests are verified before registration; plugin code is imported in a child process, not in the desktop process.
- The default worker provides crash containment only and still runs with the desktop user's OS privileges.
- Windows plugins may explicitly set `"execution_sandbox": "appcontainer"` in their manifest. For these plugins, both metadata discovery and every invocation run inside a no-network AppContainer with a package-SID ACL named-pipe broker; sandbox failures never fall back to the same-user worker.
- The initial AppContainer pilot is intentionally narrow: the JSON formatter is the first opted-in plugin, and the current broker supports only policy-approved filesystem reads. Plugins with unsupported imports, capabilities or permissions are rejected from AppContainer execution rather than silently downgraded.

### 5. Scheduling and automation
- Create and inspect scheduled jobs from the desktop assistant; interval recurrence accepts integer minutes from 1 through 525,600 and rejects coercive or out-of-range values.
- The scheduler engine also exposes a calendar recurrence API for daily, selected-weekday weekly, and selected-day monthly jobs using IANA timezone names; interval jobs remain backward compatible. Calendar occurrences are stored as offset-aware ISO-8601 instants. A nonexistent DST wall time shifts forward to the first valid local minute; a repeated fall-back time uses the earlier occurrence (fold=0). Monthly dates absent from a month (for example, day 31 in February) are skipped rather than clamped.
- Recover malformed recurrence metadata by disabling the affected job for manual review instead of silently coercing its interval. Mobile schedule entries retain the full ISO-8601 timestamp and explicit UTC offset.
- Persist job state and recover eligible work after restart.
- Retry failed handlers with bounded attempts; desktop and mobile schedule views distinguish Pending, Running, Completed, Failed and Cancelled jobs and show retry/error diagnostics. Both expose cancellation only for pending work.
- Assign a stable `execution_id` to each logical occurrence and preserve it across retries/recovery. Built-in scheduled tool actions claim it in an encrypted local ledger protected by an OS sidecar lock before invocation, skip already successful repeats, defer live in-flight work, and fail closed when a crash leaves the outcome uncertain.
- Persist encrypted, bounded execution history (last 1,000 occurrences) with one record per `execution_id`, retry attempt counts, timestamps and categorical outcomes (`started`, `retrying`, `deferred`, `succeeded`, `failed`, `uncertain`, `skipped`). History deliberately excludes raw prompts and exception text; desktop and mobile schedule screens show the 30/50 most recent records respectively. If history cannot be checkpointed, the scheduler blocks the action or marks its outcome for manual review.
- Missed-run policy is catch-up-once: after restart, one overdue occurrence is eligible to run; the scheduler does not replay every missed interval. After a successful recurring occurrence it calculates the next future run from the current time. Jobs still require the desktop app to be running.
- Shutdown waits for a bounded scheduler grace period and reports if a synchronous handler is still running; handlers cannot be force-killed safely.
- Scheduler delivery for reminders and arbitrary handlers remains at-least-once, not exactly-once. The built-in tool ledger prevents automatic replay of a successful or ambiguous tool invocation, but cannot prove whether an external effect completed if the process crashes in the effect/checkpoint window; that job is failed closed for manual reconciliation. Other integrations must durably enforce `execution_id` at their own side-effect boundary.

### 6. Flutter mobile companion
- Connect to the running desktop over the local WebSocket API.
- View conversation transcripts, assistant speech/status events and synchronized plan checklists.
- Submit text and voice input where platform speech permissions/capabilities are available.
- View scheduled jobs, refresh schedule state and inspect recent execution history.
- Save connection settings using platform secure storage and optionally restore a saved connection.
- Pair individual devices with unique credentials; revoke devices and enforce server-side roles (admin, operator, approver, viewer).
- Route sensitive approval prompts to an authenticated admin/approver device, with desktop approval as fallback.
- Supports secure WSS/TLS deployments; non-loopback cleartext WS requires an explicit warning/confirmation and is intended only for a trusted private LAN/VPN.

### 7. Privacy, data protection and recovery
- Encrypt supported local runtime stores at rest with AES-256-GCM; protect the Windows data key with the current user's DPAPI profile or use the documented environment-key mode.
- Use portable passphrase-encrypted backups with authenticated verification and explicit restore confirmation.
- Inspect registered encrypted stores and explicitly migrate legacy VAENC1 data to VAENC2 after verifying a current backup.
- Bootstrap and rotate data-encryption keys locally while retaining previous keys for old data.
- Export/import a separate encrypted offline recovery kit containing the keyring; import refuses to overwrite an existing keyring.
- Retire a retained key only after registered stores, authenticated backup and recovery-kit checks pass and the operator confirms the action.
- Preview and explicitly confirm retention cleanup; retention is disabled by default. Optional retention windows also cover the encrypted personal profile and scheduled execution history.
- Redacted structured diagnostics and audit/policy controls support troubleshooting and accountability.

### 8. Current limitations to understand
- The desktop application must be running for mobile connectivity and scheduled jobs; the mobile app does not independently execute desktop tools.
- AppContainer enforcement is currently a per-plugin Windows pilot, not a blanket sandbox for every plugin. Plugins without an explicit AppContainer manifest declaration still use the same-user crash-contained worker and retain the desktop user's OS permissions.
- The AppContainer pilot currently supports a narrow low-dependency plugin set and policy-approved filesystem reads; remaining broker capabilities and plugin dependency migrations are not complete.
- Windows GUI, microphone/speaker, Ollama lifecycle, physical Android/iOS behavior and real private-LAN/TLS deployment require target-environment validation.
- CI mobile artifacts are validation/development artifacts, not production-signed distribution packages.
- Key retirement is not secure erasure from old backups, recovery kits, snapshots or storage media. Stop the desktop and all runtime writers before migration or key retirement.

> **Project status:** hardened development release. Python regression checks are included in CI. Windows GUI/audio/Ollama runtime and mobile device testing still require validation on the target machine. Review [WORLD_CLASS_AUDIT_REPORT.md](WORLD_CLASS_AUDIT_REPORT.md) and [SECURITY.md](SECURITY.md) before enabling automation or LAN access.

## Repository layout

- `main.py`, `ui/` — Windows desktop application and operational viewers.
- `agent/` — conversation orchestration, planner/executor, policy, verification, undo, memory, history, scheduler, skills and LLM client.
- `tools/`, `plugins/` — built-in tools and manifest-verified plugin tools.
- `audio/` — microphone capture, Faster-Whisper speech-to-text and pyttsx3 text-to-speech.
- `mobile_api/` — local FastAPI/WebSocket bridge.
- `voice_assistant_mobile/` — Flutter Android/iOS/desktop companion source.
- `tests/` — security/reliability regressions and Phase 0 smoke checks.

## Requirements

- Windows 10/11, 64-bit (primary supported desktop target).
- **Python 3.11 (64-bit)** recommended.
- [Ollama for Windows](https://ollama.com/) for local LLM inference.
- A model compatible with Ollama. The default is `qwen2.5-coder:32b` in `config.py`; this model requires substantial RAM/VRAM. Choose a smaller model in `config.py` if required.
- For speech: a working microphone/speaker. Faster-Whisper may download its model on first use.
- For OCR: install the Tesseract OCR desktop application separately; the Python `pytesseract` package alone is not sufficient.
- For the mobile app: Flutter stable (Dart SDK version constrained by `voice_assistant_mobile/pubspec.yaml`) and Android SDK for Android builds.
- For individual mobile device identity, enroll devices from the trusted desktop with `python mobile_device_cli.py add "Device name"`; see [MOBILE_SECURITY_SETUP.md](MOBILE_SECURITY_SETUP.md).

## Quick start — Windows

Open PowerShell in the repository root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_windows.ps1
.\.venv\Scripts\python.exe main.py
```

The script creates `.venv` and installs the **complete Python runtime dependency set** from `requirements.txt`.

Manual installation:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

For contributors and test tooling, use:

```powershell
.\setup_windows.ps1 -Development
```

This installs `requirements-dev.txt` (runtime dependencies plus pytest).

### Ollama setup

Install and start Ollama, then pull the model selected in `config.py`, for example:

```powershell
ollama pull qwen2.5-coder:32b
ollama list
```

If your machine cannot comfortably run the default model, edit `DEFAULT_MODEL` in `config.py` and select an installed smaller model. This project does not silently switch models.

### Optional cloud failover

Local Ollama is the default. Cloud failover is optional and disabled by default. Copy `history/llm_failover.example.json` to `history/llm_failover.json`, enable failover only if needed, and set `VOICEASSISTANT_CLOUD_API_KEY` in your user environment. **Never commit API keys or real `.env` files.**

## Mobile companion

```powershell
cd voice_assistant_mobile
flutter pub get
flutter analyze
flutter test
flutter build apk
```

By default, the desktop mobile API listens on `127.0.0.1`, so it is local-only. For trusted private-LAN pairing, configure `VOICEASSISTANT_MOBILE_HOST=0.0.0.0`; use either a legacy `VOICEASSISTANT_MOBILE_TOKEN` (at least 32 characters) or enroll per-device credentials with `python mobile_device_cli.py add "Device name"`. Once the encrypted device registry exists, per-device credentials are mandatory and the legacy shared token is disabled. Device roles (admin/operator/approver/viewer) are enforced server-side; manage them only from the trusted desktop with `python mobile_device_cli.py add --role admin`, `list`, or `set-role DEVICE_ID viewer`. See [MOBILE_SECURITY_SETUP.md](MOBILE_SECURITY_SETUP.md) for the permission matrix and setup guidance. The Flutter client supports direct `wss://` TLS or a TLS-terminating reverse proxy. The server allows five concurrent mobile clients by default; set `VOICEASSISTANT_MOBILE_MAX_CONNECTIONS` to 1–32 to change the process-local cap; non-loopback `ws://` requires explicit user confirmation and is suitable only for a trusted private LAN/VPN. Never expose port 8765 directly to the public internet.

## Tests

```powershell
python -m pytest -q tests/test_security_reliability_regressions.py tests/test_user_profile.py tests/test_mobile_device_identity.py tests/test_mobile_rbac.py tests/test_mobile_approval_routing.py tests/test_scheduler_reliability.py tests/test_scheduler_ui_state.py tests/test_scheduled_execution_ledger.py tests/test_scheduled_tool_idempotency.py tests/test_plugin_marketplace_security.py tests/test_data_protection.py tests/test_keyring.py tests/test_data_protection_v2.py tests/test_key_migration.py tests/test_key_migration_cli.py tests/test_key_rotation.py tests/test_key_recovery.py tests/test_key_retirement.py tests/test_scheduled_write_undo_safety.py tests/test_backup_manager.py tests/test_privacy_manager.py tests/test_observability.py
python tests/phase0_smoke_tests.py
```

GitHub Actions runs the Windows Python regression suite, Flutter analysis/tests, Android release APK build and unsigned iOS build on pushes, pull requests and version tags. Version tags (`v*`, e.g. `v1.0.0`) create a GitHub **pre-release** with source and Android APK artifacts after all validation jobs pass. The workflow deliberately marks releases as pre-release and never latest because the Android APK currently uses debug signing and the iOS build is unsigned. Do not distribute these artifacts as production builds. A production release requires a protected Android signing keystore, an Apple signing identity/provisioning profile, and a signed iOS IPA workflow.

## Local data-key administration

Create and verify a recent portable backup before key administration:

```powershell
python backup_cli.py create .\history\before-key-change.vabackup
python data_key_cli.py status
python data_key_cli.py bootstrap --backup .\history\before-key-change.vabackup
```

After bootstrap, a new verified backup is required before each active-key rotation:

```powershell
python backup_cli.py create .\history\before-rotation.vabackup
python data_key_cli.py rotate --backup .\history\before-rotation.vabackup
```

The CLI prompts for the backup passphrase and a typed confirmation; passphrases are never accepted as command-line arguments. Rotation is local-only, changes the active key for future writes, retains all previous keys, and does not rewrite existing files.

### Explicit legacy-store migration

Migration is a separate, operator-triggered operation; it does not run automatically at startup. Close VoiceAssistant and stop every process that can write runtime stores before proceeding. First inspect the registered inventory, create a portable backup, then run the migration CLI:

```powershell
python key_migration_cli.py inspect
python backup_cli.py create "D:/Offline/voiceassistant-pre-migration.vabackup"
python key_migration_cli.py migrate --backup "D:/Offline/voiceassistant-pre-migration.vabackup"
```

The CLI authenticates the backup, checks that it contains every registered store and that its logical contents match the current stores, then requires the exact typed confirmation. It migrates eligible VAENC1 stores to VAENC2 and verifies each rewritten file. Plaintext/unknown or unsupported envelopes cause a refusal; resolve them explicitly rather than bypassing the preflight. Migration is atomic per file, not across the entire store set. Keep the independently verified backup until the application has been validated after migration.

Create a separate offline recovery kit after bootstrap and after meaningful key rotations. The kit contains every active, retained and legacy key, encrypted with AES-256-GCM and a passphrase-derived key (PBKDF2-HMAC-SHA-256, 600,000 iterations). Use a unique passphrase of at least 16 characters and store the kit and passphrase separately:

```powershell
python data_key_cli.py recovery-export --output "D:/Offline/voiceassistant.varecovery"
# On a replacement/recovered installation where the keyring is absent:
python data_key_cli.py recovery-import --kit "D:/Offline/voiceassistant.varecovery"
```

Import requires a typed confirmation, authenticates the complete kit, and refuses to overwrite any existing keyring. The imported keyring is protected using the destination's current Windows DPAPI profile or configured environment-key mode. This is not a portable data backup: it restores encryption keys, not application data. Losing both the original keyring and all valid recovery kits/passphrases makes the encrypted data unrecoverable. Never send kits to mobile clients, cloud logs, telemetry, or source control. Never delete the legacy key or retained keys as a cleanup shortcut. See [Phase 8.3 key rotation and recovery](docs/PHASE_8_3_KEY_ROTATION_RECOVERY_DESIGN.md) and [the key-management runbook](docs/KEY_MANAGEMENT_RUNBOOK.md).

## Diagnostics and observability

The desktop launcher writes structured, secret-redacted JSONL diagnostics to `history/logs/voiceassistant.jsonl`. Files rotate at approximately 2 MiB with up to five backups. Logs are local runtime data, excluded from Git, and may contain operational metadata and file paths; protect the history directory with Windows account ACLs. See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md). Diagnostic logs are not the authoritative audit trail.

## Runtime data and privacy

Conversation history, memory, audit logs, policy, scheduled jobs, undo stacks and undo snapshots are local runtime data and are intentionally excluded from Git. These structured stores and undo snapshots are encrypted at rest with AES-256-GCM (authenticated encryption). On Windows, a random 256-bit data key is protected by the current Windows user profile using DPAPI and stored at `history/.voiceassistant_data_key.dpapi`. On non-Windows systems, set `VOICEASSISTANT_DATA_KEY` to a 32-byte hexadecimal or base64 key before enabling encrypted stores.

Existing plaintext JSON stores are migrated atomically to encrypted files when first read by the updated application. Encrypted records are bound to their store filename; tampering, a wrong key, or a mismatched context causes a hard error. The application must not reset an encrypted store to an empty default after an authentication failure. Explicit conversation exports remain readable Markdown/text files and are not encrypted automatically; store them in a protected location.

**Key recovery is essential:** on Windows, keep the DPAPI key file with the same Windows user profile and back it up as part of a protected device backup. DPAPI-protected key files are not independently portable to another Windows account/device. Losing the active key makes encrypted local data unrecoverable. Never commit the DPAPI key file, environment key, runtime data or exports.

### Portable encrypted backup

Use the interactive CLI; the passphrase is prompted securely and is not passed in the command line:

```powershell
python backup_cli.py create "D:/Backups/voiceassistant.vabackup"
python backup_cli.py restore "D:/Backups/voiceassistant.vabackup"
```

The backup archive is protected with PBKDF2-HMAC-SHA256 (600,000 iterations) and AES-256-GCM, so it can be restored on another machine using a different local data key. Use a strong passphrase of at least 12 characters and keep it separately; losing it makes that backup unrecoverable. Restore requires typing `RESTORE` and replaces matching store files only; it does not delete files absent from the backup. Each restored file is written atomically, but the entire multi-file restore is not a single filesystem transaction. Backups include the supported JSON stores, custom-skill manifests and undo snapshots; they do not include application source, plugin binaries, exports, API credentials or machine-specific settings. Generated custom-skill loader files are rebuilt from validated manifests.

For migration to another machine, create a portable backup on the source machine and restore it on the destination; do not copy a DPAPI key file to another Windows profile and expect it to work.

### User-controlled retention

Retention is disabled by default and never runs automatically. Configure it, inspect the preview, then explicitly confirm a purge:

```powershell
python privacy_cli.py show
python privacy_cli.py set --conversation-days 90 --audit-days 365 --memory-days 180 --completed-job-days 180
python privacy_cli.py preview
python privacy_cli.py purge
```

Use `0` for any setting to disable that rule. Conversation history and memory are removed as whole files based on their last-modified age; audit entries are filtered by timestamp; only old completed/failed/cancelled jobs are eligible. Pending/running jobs and entries with unknown timestamps are retained. Close VoiceAssistant and create a portable backup before purging. Deletion is not secure erasure.

Local data-key bootstrap, rotation, backup-verified VAENC1-to-VAENC2 migration, offline recovery-kit export/import, and guarded retained-key retirement are implemented. Secure erasure is not provided; removing a key from the live keyring does not erase copies from historical backups, recovery kits, snapshots or storage media.

Scheduled jobs use persisted execution states and retry retryable handlers up to three times. Each logical occurrence has a stable `execution_id` reused across retries and crash recovery. Built-in scheduled tool actions persist an encrypted invocation claim before execution, skip successful duplicates, defer work still active in the same process, and fail closed when a prior process left an ambiguous outcome. This is not exactly-once delivery: reminders and arbitrary callbacks remain at-least-once, and external integrations must enforce `execution_id` at their own side-effect boundary. Ambiguous tool outcomes require manual reconciliation rather than automatic replay. Shutdown waits up to a bounded grace period and logs when a synchronous handler remains active, because Python cannot safely force-kill that handler.

## Documentation

- [Comprehensive engineering audit and current phase status](WORLD_CLASS_AUDIT_REPORT.md)
- [Security threat model](docs/PHASE_8_3_SECURITY_THREAT_MODEL.md)
- [Key-management operator runbook](docs/KEY_MANAGEMENT_RUNBOOK.md)
- [Mobile security setup](MOBILE_SECURITY_SETUP.md)
- [Local data protection and portable backups](docs/DATA_PROTECTION_AND_BACKUP.md)
- [Plugin authoring guide](tests/PLUGIN_AUTHORING.md)
- [Plugin security model](docs/PLUGIN_SECURITY_MODEL.md)
- [Help](HELP.md)

## License

No open-source license has been assigned in this repository. Unless the repository owner adds a license, normal copyright restrictions apply; do not assume permission to redistribute or reuse the code.
