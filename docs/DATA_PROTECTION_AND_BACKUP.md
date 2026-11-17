# Local Data Protection and Portable Backups

## Scope

VoiceAssistant is a local-first desktop agent. This document explains how its private runtime stores are protected, how to create a portable backup, and what the protection does **not** guarantee.

## Encryption at rest

The application uses AES-256-GCM authenticated encryption for these local stores:

- Conversation history and audit log
- Agent memory and policy
- Scheduled jobs
- Undo stack and undo file snapshots
- Skill-pack settings
- Custom-skill manifests and index

The encrypted file format starts with the `VAENC1` marker and a format version, followed by a random 96-bit nonce and AES-GCM ciphertext/tag. The store filename is included as authenticated additional data, so renaming an encrypted store to another store filename fails authentication.

### Key source

**Windows (default):** the application creates a random 256-bit data key and protects it with Windows DPAPI for the current user profile. The protected key is stored at:

```text
history/.voiceassistant_data_key.dpapi
```

**Other platforms or explicit key override:** set `VOICEASSISTANT_DATA_KEY` to a 32-byte hexadecimal or base64 value. A configured but invalid key is an error; the application does not silently fall back to plaintext.

The key file and runtime data are ignored by Git. Protect the Windows profile and the project runtime directory with normal OS account controls and full-disk encryption.

### Legacy migration and failures

Existing plaintext JSON files are migrated atomically to encrypted form the first time the updated application reads them. Authentication failures, wrong keys, truncated ciphertext, unsupported versions and store-name mismatches raise `DataProtectionError`. Protected stores must never be reset to empty defaults after such an error.

If an older version left a malformed plaintext JSON file, the existing quarantine behavior preserves the original bytes before returning a recoverable empty state where that store supports it. Encrypted authentication failures are not treated as malformed plaintext.

## Portable backup format

A portable backup is independent of the source machine's DPAPI key. The backup tool decrypts supported stores in memory, packages the data into a bounded ZIP archive, and encrypts the entire archive using:

- PBKDF2-HMAC-SHA256 with 600,000 iterations
- A random 128-bit salt
- AES-256-GCM with a random 96-bit nonce and authenticated backup context
- A minimum passphrase length of 12 characters

The backup file is written atomically. The passphrase is requested using the interactive CLI's secure prompt and is never passed as a command-line argument.

### Create and restore

Run from the repository root:

```powershell
python backup_cli.py create "D:/Backups/voiceassistant.vabackup"
python backup_cli.py restore "D:/Backups/voiceassistant.vabackup"
```

The CLI asks for the passphrase twice when creating a backup. During restore, it requires the user to type `RESTORE` before asking for the passphrase and writing any files.

### Included data

- Conversation history, audit log, memory and policy
- Scheduled jobs, undo stack and undo snapshots
- Skill-pack settings
- Custom-skill manifests and index

Generated custom-skill loader source is regenerated from validated manifests after restore. Private action arguments are not embedded in generated plugin source.

### Excluded data

- Application source code and third-party plugin source
- API keys, environment variables and cloud credentials
- Explicit Markdown/text conversation exports
- Machine-specific settings and operating-system credentials
- DPAPI key file (portable backups do not depend on it)

Restore replaces only the files present in the backup. It does not delete local files absent from the archive. Each file is atomically replaced, but a multi-file restore is not a single filesystem transaction; a power loss or process termination during restore can leave a partially restored set. Create a fresh backup before restoring over important data.

### Archive validation

Before writing, restore validates the authenticated archive, format version, path allowlist, duplicate names, ZIP-slip/absolute paths, symbolic links, encrypted ZIP members, file count, total uncompressed size, compression ratio and JSON store shapes. Restore paths are confined to the runtime history directory. Invalid paths or data fail closed.

## Recovery and portability

- **Same Windows user/profile:** keep the protected DPAPI key file and Windows user profile available when restoring a raw copy of the runtime directory.
- **Different machine or account:** do not copy the DPAPI key file and expect it to decrypt. Create a portable `.vabackup` with a strong passphrase on the source machine, then restore it on the destination. The destination encrypts restored stores using its own local data key.
- **Explicit environment key:** if the application was configured with `VOICEASSISTANT_DATA_KEY`, configure the same key before opening its encrypted stores. Keep it in a secure secret manager; do not put it in a script, source file, backup archive, or command-line argument.
- **Lost key or backup passphrase:** encrypted data cannot be recovered without the corresponding key or passphrase. OpenAI/VoiceAssistant support cannot reconstruct it.

## Security boundaries and known limitations

- Encryption protects persisted files at rest; it does not protect plaintext while the application is running or from malware executing as the same Windows user.
- Explicit Markdown/text exports remain plaintext and should be stored in a protected location.
- No secure-erasure guarantee is made when files are deleted; filesystem journaling, snapshots and SSD wear-leveling may retain old blocks.
- Key rotation and a guided data-key export workflow are not implemented.
- There is no central user-configurable retention policy or privacy dashboard yet.
- Portable backup creation and restoration hold decrypted content in process memory.
- The backup passphrase must be stored separately from the backup file. Anyone with both can restore the included data.


## User-controlled retention

Retention settings are stored encrypted in `history/data_retention.json`. All retention rules are disabled by default; nothing is purged automatically at application startup. Use the interactive CLI:

```powershell
python privacy_cli.py show
python privacy_cli.py set --conversation-days 90 --audit-days 365 --memory-days 180 --completed-job-days 180
python privacy_cli.py preview
python privacy_cli.py purge
```

A value of `0` disables an individual rule. Conversation history and memory are deleted as whole stores based on file modification time. Audit events are filtered by their ISO timestamp. Only terminal scheduled jobs (completed, failed or cancelled) are eligible; pending/running jobs and records with unknown timestamps are retained. The purge command prints a preview and requires the user to type `PURGE`. Close the desktop app and create a portable backup before purging to avoid races with live writes.

Deletion is ordinary filesystem deletion, not secure erasure. This CLI is not a privacy dashboard and does not purge undo snapshots, custom skills, exports or policy settings.
