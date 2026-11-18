# Phase 8.3 — Data Protection Threat Model

**Review date:** 2026-11-18  
**Scope:** VAENC1/VAENC2 encryption, protected keyring, migration, portable backups, recovery kits, key administration and mobile access boundary.

## 1. Security objectives

- Protect confidentiality and integrity of local runtime stores.
- Prevent silent key replacement, plaintext fallback and destructive recovery.
- Keep key administration local to the trusted desktop host.
- Preserve VAENC1 readability until migration is independently verified.
- Restore portable backups under the destination installation's own key hierarchy.
- Prevent retirement while any registered store still needs a key.
- Make residual risks and operator prerequisites explicit.

## 2. Assets

| Asset | Security requirement |
|---|---|
| Runtime JSON stores, custom skills, undo snapshots | Confidentiality, integrity, correct context binding |
| Active, retained and legacy DEKs | Confidentiality; never log or expose in CLI arguments |
| Protected keyring | Integrity, availability, local-user protection |
| Portable backup | Confidentiality, authenticated integrity, documented store coverage |
| Recovery kit and passphrase | Separate custody; together they grant access to all included DEKs |
| Migration journal | Integrity and restart safety; never authoritative over actual file envelopes |
| Device registry and audit records | Confidentiality, integrity, least privilege |

## 3. Trust boundaries

- **Desktop host:** Primary trust anchor. Key administration and key material remain local.
- **Windows user profile / DPAPI:** Protects the keyring and legacy key in Windows DPAPI mode. Access is bounded by the Windows profile and host security.
- **Environment-key mode:** VOICEASSISTANT_DATA_KEY is high-value secret material. Any process, diagnostic dump, deployment configuration or account able to read it may compromise the keyring.
- **Portable backup:** Passphrase-protected AES-256-GCM archive containing logical application data, not the source keyring or DPAPI key.
- **Recovery kit:** Separate PBKDF2-HMAC-SHA256 (600,000 iterations) + AES-256-GCM artifact containing every key generation. Anyone with both kit and passphrase can recover all included DEKs.
- **Mobile clients:** Authenticated and role-authorized through the desktop service. Mobile protocol must not expose key administration.
- **Plugins:** Execute in the desktop process and are not OS-sandboxed. A malicious plugin may access data available to that process.

## 4. Threats and residual risk

| Threat | Mitigation currently present | Residual risk |
|---|---|---|
| Theft of runtime files without keys | AES-256-GCM; VAENC2 key IDs; context-bound AAD | Compromised host/user account can access unlocked data |
| Ciphertext modification or wrong context | GCM authentication and strict envelope/version checks | Deletion/corruption can still cause availability loss |
| Wrong/missing key or corrupt keyring | Fail-closed lookup and validation; no silent bootstrap on normal writes | Loss of every valid key copy makes ciphertext unrecoverable |
| Theft of portable backup | PBKDF2-HMAC-SHA256 600,000 + AES-256-GCM | Passphrase strength is decisive; KDF is not memory-hard |
| Theft of recovery kit | PBKDF2-HMAC-SHA256 600,000 + AES-256-GCM; interactive passphrase entry | Kit plus passphrase exposes all included keys |
| Interrupted migration | Per-file staging, decrypt-and-digest verification, atomic replace, journal | Atomicity is per file, not across all stores |
| Premature key retirement | Active-key refusal, registered-store scan, no-reference check, matching backup and exact recovery kit | Inventory is allow-list based; unregistered plugin stores are outside the guarantee |
| Concurrent runtime writes during retirement | Key-admin lock serializes administration and migration | Ordinary runtime writers do not acquire that lock; all writer processes must be stopped. This is an operational precondition, not enforced process-wide quiescence |
| Malicious plugin | Review and integrity checks | No OS sandbox; plugin runs with desktop process privileges |
| LAN traffic interception | Device credentials and role authorization | Direct ws:// is not encrypted; use VPN or TLS-terminating reverse proxy |
| Local malware / administrator compromise | Windows permissions and disk encryption recommended | Out of scope once the host is compromised |

## 5. Cryptographic and lifecycle controls

- Data stores use AES-256-GCM. VAENC2 binds logical context and 128-bit key ID in authenticated additional data.
- VAENC1 remains readable through the preserved legacy key mapping.
- Keyring entries contain 256-bit DEKs and exactly one active key; the keyring is protected by DPAPI or an HKDF-derived environment wrapping key.
- Migration is explicit and uses a registered allow-list. It does not recursively rewrite arbitrary files.
- Portable backups are independently passphrase-protected and restore logical data under the destination's own key hierarchy.
- Recovery kits are distinct from portable backups and are never automatically uploaded or sent to mobile.
- Rotation retains previous keys. Retirement is separate, explicit and local-only.
- Retirement removes an entry from the live keyring but does **not** guarantee secure erasure from old backups, recovery kits, snapshots, journals, SSD wear-leveling or other copies.

## 6. Operational security requirements

1. Restrict Windows account access and enable full-disk encryption.
2. Never put environment keys or passphrases in source control, CLI arguments, logs or screenshots.
3. Store portable backups, recovery kits and their passphrases under separate custody; do not keep all artifacts and secrets on the same host.
4. Before migration or retirement, stop VoiceAssistant, scheduled workers, background agents and every other process that can write registered stores.
5. Take a fresh portable backup after stopping writers. Verify it with the backup verification API/CLI.
6. For retirement, export a fresh recovery kit after the final keyring change. Retirement refuses a kit that does not match the exact current keyring.
7. Preserve at least one tested recovery path until policy explicitly authorizes destruction.
8. Use VPN or TLS (wss://) for remote access; do not expose direct plaintext ws:// to untrusted networks.
9. Treat plugin installation as code execution with desktop-user privileges.

## 7. Validation evidence and limits

- Windows Python CI compiles sources, runs security/reliability regression tests and Phase 0 smoke tests.
- Mobile CI validates Flutter, runs Flutter tests, builds Android APK and performs an unsigned iOS build.
- These jobs are regression evidence, not a penetration test, formal cryptographic review, signed production release or proof of complete inventory coverage.
- Retirement inventory covers only the explicit runtime target registry in agent/key_migration.py. Every new encrypted store must be added to migration inventory, backup allow-list/restore validation, tests and this threat model.
- Restore is atomic per file, not a filesystem-wide transaction.

## 8. Release decision

**Engineering status:** 8.3.1–8.3.6 implementation and automated CI checks are present. Phase 8.3.7 records trust boundaries and operator controls.

**Deployment status:** This is not a formally security-certified or penetration-tested product. Production deployment requires an operator-run restore/recovery drill, review of the deployed key-protection mode, TLS/VPN enforcement for remote access and confirmation that every deployed encrypted store is registered.

**Key-retirement gate:** Runtime writers do not share the key-admin lock. The application and all writer processes must be stopped before backup and retirement. If that cannot be guaranteed, do not retire keys.
