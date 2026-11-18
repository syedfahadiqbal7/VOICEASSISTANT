# Phase 8.3 — Data Encryption Key Rotation & Recovery Design

**Status:** Design approved for implementation planning; no production key format or runtime behavior changes in this phase.  
**Scope:** Local encrypted application stores managed by `agent/data_protection.py`, plus portable backup/restore integration.  
**Security boundary:** Key administration is local to the trusted desktop host. Mobile devices and remote WebSocket commands must never rotate, export, unwrap, replace, or recover data-encryption keys.

## 1. Current-state findings

- `agent/data_protection.py` currently encrypts with AES-256-GCM.
- The current envelope is `VAENC1 | version byte | 12-byte nonce | ciphertext+tag`; the selected key is not identified in the envelope.
- Windows normally creates a random 32-byte key and protects it using the current Windows user's DPAPI profile at `history/.voiceassistant_data_key.dpapi`.
- `VOICEASSISTANT_DATA_KEY`, when configured, takes precedence over the DPAPI key. On non-Windows systems it is required.
- The active key is therefore a single implicit key. There is no key ID, keyring, rotation journal, or supported local recovery workflow.
- Encryption context is authenticated as AES-GCM AAD, binding ciphertext to its logical store context.
- Existing tests cover round-trip, tampering, wrong-key failure, DPAPI, plaintext migration, and portable passphrase-protected backups.
- `agent/backup_manager.py` exports logical plaintext contents only inside a passphrase-protected AES-GCM archive and restores by encrypting data under the destination host's key. A portable restore must not transplant the source machine's DPAPI key or source keyring.

## 2. Security goals and non-goals

### Goals
1. Support multiple retained data-encryption keys (DEKs) identified by opaque random key IDs.
2. Introduce a versioned envelope that identifies the DEK required for decryption.
3. Preserve read compatibility for all existing `VAENC1` payloads.
4. Rotate without data loss, plaintext-at-rest windows, or an interval where files reference a key that has not been durably persisted.
5. Make interrupted rotation resumable and idempotent.
6. Verify every rewritten file before marking it migrated.
7. Fail closed on missing, malformed, tampered, or unavailable keys.
8. Provide a documented recovery path that does not imply cryptographic recovery is possible after all copies of a key are lost.
9. Preserve portable backup semantics: restore logical data under the destination's own key hierarchy.

### Non-goals
- No cloud KMS dependency or remote key escrow in this phase.
- No automatic key upload, export to mobile, or recovery through a WebSocket API.
- No deletion of old keys until a separately verified retirement step.
- No background key rotation without a local administrative action and a verified backup.
- No claim that DPAPI-protected keys can be recovered without the original Windows user profile or an independent recovery copy.

## 3. Proposed cryptographic model

### 3.1 Key hierarchy
- **DEK:** Random 256-bit AES key used directly for AES-256-GCM store encryption.
- **Key ID:** Random 128-bit identifier (UUID/hex), not derived from the key. IDs are metadata, not secrets.
- **Keyring:** Versioned metadata containing the active key ID, retained DEKs keyed by ID, creation timestamps, and rotation state. Never log key bytes.
- **Keyring protection on Windows:** Protect the complete keyring payload with current-user DPAPI using a new domain-separated entropy label (not the existing single-key entropy). This retains the existing Windows trust boundary.
- **Keyring protection on portable/non-Windows deployments:** Derive a keyring-wrapping key from the configured 32-byte `VOICEASSISTANT_DATA_KEY` using HKDF-SHA-256 with a fixed application-specific salt and a unique info label such as `VoiceAssistant.Keyring.Wrap.v1`; protect the serialized keyring with AES-256-GCM and a fresh nonce. The environment value becomes the keyring unlock secret, not the per-file DEK.
- Do not silently switch protection sources. If an environment key is set, it must continue to take precedence only according to an explicit, documented migration rule. A source change that cannot unlock the existing keyring must fail closed and require an explicit local migration/recovery operation.

### 3.2 Version 2 envelope
Proposed binary layout:

```
magic "VAENC2"
format-version byte
key-id-length byte (bounded; fixed 16 bytes preferred)
key-id bytes
nonce (12 bytes)
ciphertext || 16-byte GCM authentication tag
```

- Use a new AAD domain separator, e.g. `VoiceAssistant.LocalData.v2:<logical-context>:<key-id>`.
- Validate magic, exact version, key ID length, minimum payload length, and key existence before decrypting.
- `VAENC1` remains readable using the legacy key mapping; `VAENC2` resolves the exact key ID.
- New writes use the active key only after the keyring is durably persisted.
- No fallback to plaintext or trying every key when a key ID is missing.

## 4. Safe migration from the current single-key model

1. Acquire the process-wide lock and an OS-level inter-process lock for the keyring/rotation operation.
2. Resolve the exact currently effective legacy key using today's precedence rules. Do not guess if the DPAPI key and environment key conflict.
3. Create a keyring with:
   - a retained legacy entry that references the existing key bytes;
   - a newly generated active DEK with a new key ID;
   - a migration marker stating that legacy `VAENC1` files may still exist.
4. Persist the protected keyring atomically and verify it can be reopened before any file is rewritten.
5. Map all existing `VAENC1` payloads to the legacy key ID for reads. The migration must not rewrite all files merely to introduce the keyring.
6. New writes now use `VAENC2` and the new active key.
7. Enumerate only explicitly registered encrypted stores and their logical contexts. Do not recursively rewrite arbitrary files.
8. Re-encrypt each legacy file to a temporary sibling file using the new active key and the same logical AAD context; flush and fsync; decrypt the temporary file and compare the plaintext digest; atomically replace the original.
9. Record progress only after successful replacement and verification. On restart, inspect the actual envelope and resume safely; never trust a progress marker over file contents.
10. Mark migration complete only after every registered store and eligible undo snapshot has been checked. Retain the legacy key until a separate retirement operation verifies no `VAENC1` payload remains.

The operation must be restartable after interruption at any step. Atomicity is per file, not across the whole filesystem. A failed migration leaves both keys retained and every file decryptable.

## 5. Rotation lifecycle

- **Prepare:** Validate local administrative context, available disk space, writable directories, keyring integrity, and a recent portable backup.
- **Create:** Generate and durably persist a new DEK and key ID. Keep the previous active key.
- **Switch writes:** Atomically change active key ID only after keyring durability is verified.
- **Migrate:** Re-encrypt registered stores in bounded batches with per-file atomic replacement and verification.
- **Verify:** Independently scan registered stores; ensure every encrypted payload is `VAENC2`, references a present key, and decrypts with the expected context.
- **Retain:** Keep previous keys by default. Do not auto-prune.
- **Retire:** A separate explicit local-only operation, requiring verified backup and a clean full scan, may remove a non-active key only when no file references it and no supported restore/recovery requirement depends on it.

A rotation failure must never cause the active key to be discarded or make older files unreadable.

## 6. Recovery model

- Recovery is about restoring access to a keyring/key copy, not deriving an AES key from ciphertext.
- Windows DPAPI loss (profile deletion, machine/profile migration, or corruption) cannot be solved by code alone. A recovery kit or protected keyring backup must be created before the incident.
- Provide a local-only, explicit recovery export/import workflow in a later implementation subphase. Recovery material must be encrypted with a high-entropy recovery secret or passphrase using a memory-hard KDF where supported (Argon2id preferred; otherwise a carefully parameterized PBKDF2-HMAC-SHA-256 fallback with documented work factor).
- Recovery export must be opt-in, require explicit confirmation, warn that anyone holding both the recovery file and secret can decrypt all included data, and never print key material or passphrases.
- Recovery import must validate the complete keyring, key lengths, IDs, active-key reference, and authentication before atomically replacing the local keyring.
- Never send recovery material to logs, telemetry, cloud services, or mobile clients.
- If every usable key copy is lost, existing ciphertext is unrecoverable. The UI/CLI must state this plainly; do not silently reset keys and present empty stores as a successful recovery.

## 7. Backup and restore integration

- Continue exporting logical plaintext entries only inside the existing authenticated passphrase-protected portable archive.
- Portable restore must re-encrypt all restored stores under the destination's active DEK/key ID.
- Never include the source DPAPI key file or source keyring in a portable application backup.
- Add explicit tests proving that a portable restore on a different keyring remains decryptable and does not import source key IDs/DEKs.
- Recovery-kit export is a distinct artifact and workflow from portable data backup. It must have separate warnings and access controls.
- A restore interrupted midway remains a concern because current restore is atomic per file, not filesystem-wide. Add a restore journal/rollback or staging-directory swap before claiming all-or-nothing restore semantics.

## 8. Concurrency, durability, and operational controls

- A Python `RLock` protects threads in one process only. Add a cross-process lock for keyring mutation and rotation; use platform-appropriate locking and bounded timeout behavior.
- Use same-directory temporary files, restrictive permissions, flush/fsync, and `os.replace`; fsync the containing directory where supported.
- Avoid overwriting a valid keyring with an incomplete/corrupt file. Keep a verified previous keyring generation until the new generation has been reopened and validated.
- Prevent two rotation processes from running simultaneously.
- Rotation must be local-admin only; no role permission should expose key administration through mobile.
- Emit structured audit events for rotation start, key activation, per-store migration result, verification, recovery import/export, and retirement. Never log secrets, plaintext, or full sensitive paths.
- On any keyring or authentication error, fail closed and preserve source files for operator recovery.

## 9. Required implementation subphases

| Subphase | Deliverable | Exit criteria |
|---|---|---|
| 8.3.1 | Keyring format, protection adapters, key IDs | Unit tests for DPAPI/env keyring protection, malformed keyrings, and atomic persistence |
| 8.3.2 | `VAENC2` envelope + `VAENC1` compatibility | Round-trip, AAD binding, wrong/missing key, tamper, truncation, unknown version tests |
| 8.3.3 | Store inventory + resumable migration engine | Interruption/restart, per-file atomicity, digest verification, no plaintext staging tests |
| 8.3.4 | Rotation command/API (local CLI only) | Explicit confirmation, backup preflight, concurrency lock, audit events |
| 8.3.5 | Recovery kit export/import | Wrong secret, tampering, malformed keyring, atomic import, no secret leakage |
| 8.3.6 | Backup/restore integration and retirement | Cross-key portable restore, source-keyring exclusion, retirement scan and rollback tests |
| 8.3.7 | Documentation, threat-model review, CI | Full test suite and platform CI green; operational runbook complete |

## 10. Required regression and security tests

- Legacy `VAENC1` decrypts with the preserved legacy key after keyring bootstrap.
- New `VAENC2` writes use only the active key ID.
- Old and new keys coexist during rotation.
- Wrong keyring unlock secret, DPAPI profile, or key ID fails closed.
- Tampered/truncated envelope, keyring, journal, or recovery artifact fails closed.
- AAD context mismatch fails authentication.
- Interrupted migration at each boundary resumes without data loss.
- Crash before atomic replace leaves original intact; crash after replace is recognized as migrated.
- Two processes cannot rotate concurrently or corrupt keyring state.
- No registered store is skipped silently; unknown encrypted files are reported for operator review.
- Rotation never removes a key still referenced by any file.
- Portable backup restore creates destination-encrypted data and never imports source keyring material.
- Recovery import/export never exposes keys in logs, stdout, audit records, or mobile protocol.
- Simulated complete key loss produces a clear unrecoverable-data error rather than empty-store initialization.

## 11. Open decisions before implementation

1. Confirm supported Python/platform matrix and whether non-Windows environment-key mode is an actual deployment target requiring production support.
2. Confirm the recovery-kit KDF dependency policy. Argon2id is preferred only if adding/maintaining a dependency is acceptable; otherwise use PBKDF2-HMAC-SHA-256 with a calibrated work factor and strong passphrase guidance.
3. Define the authoritative registry of encrypted stores, including custom skill manifests, undo snapshots, and any encrypted files added by plugins.
4. Define whether migration runs synchronously at startup, via a local maintenance command, or in a user-visible maintenance workflow. Recommended: explicit local maintenance command with startup compatibility reads.
5. Define key-retention policy and minimum backup age/verification requirements before retirement.
6. Define Windows recovery behavior when DPAPI is unavailable but a valid recovery kit exists.

**Recommendation:** Implement 8.3.1 and 8.3.2 first, with no automatic migration. Prove backward-compatible reads and protected keyring durability before adding any operation that rewrites production data.
