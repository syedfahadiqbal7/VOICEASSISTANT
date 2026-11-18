# Data Protection and Key Management Runbook

This runbook applies to the trusted desktop host. Key administration is local-only; do not expose these commands through mobile, remote dispatch or a public endpoint.

## Before you begin

- Confirm the installation is backed up and the operator is authorized.
- Stop VoiceAssistant, scheduled/background workers and every process that can write runtime stores before migration or retirement.
- Ensure the Windows user profile (DPAPI mode) or VOICEASSISTANT_DATA_KEY (environment mode) is available and protected under organizational policy.
- Use a separate offline location for portable backups and recovery kits.
- Passphrases are collected interactively; never add them as command-line arguments.

## 1. Inspect key state

    python data_key_cli.py status

Record the active key ID and retained/legacy key counts. This command must never print key bytes.

## 2. Create and verify a portable backup

    python backup_cli.py --help

Use the exact create and verify subcommands/options shown by the installed CLI. Enter a strong unique passphrase interactively. Store the backup and passphrase separately.

The portable backup contains logical application data and restores it under the destination installation's key hierarchy. It is not a keyring recovery kit.

## 3. Bootstrap a keyring (one-time)

    python data_key_cli.py bootstrap --backup "D:/Offline/voiceassistant-pre-maintenance.vabackup"

Only perform this when no protected keyring exists and the existing legacy key is known to decrypt every registered VAENC1 store. Type the exact confirmation phrase and enter the backup passphrase interactively. Never delete a working keyring to force bootstrap.

## 4. Rotate the active data key

    python data_key_cli.py rotate --backup "D:/Offline/voiceassistant-pre-rotation.vabackup"

Rotation creates a new active DEK and retains the prior key. It does not rewrite existing stores. Keep old keys until migration and independent verification are complete.

## 5. Migrate registered VAENC1 stores

Migration is an explicit maintenance operation. Use the migration interface documented by the installed version; do not invent target paths or pass backup_verified=True without a freshly authenticated portable backup.

Review the read-only inventory first. Stop all runtime writers. Migrate only registered targets. Confirm the result reports complete and zero failures, then run the read-only inventory again. Every registered store must be VAENC2 and decryptable before retirement is considered.

The migration journal is progress evidence, not a substitute for inspecting actual envelopes.

## 6. Export a recovery kit

    python data_key_cli.py recovery-export --output "D:/Offline/voiceassistant-current.varecovery"

Use a unique passphrase of at least 16 characters. The kit contains active, retained and legacy keys. Store it offline and separately from its passphrase. Anyone holding both can recover every included DEK.

Export a new kit after any subsequent rotation or keyring change.

## 7. Retire an unused key — exceptional operation

**Do not perform this while the application or any writer process is running.** The key-admin lock does not stop ordinary runtime writes.

Preconditions:
- All registered stores are authenticated VAENC2.
- The selected key is neither active nor referenced by any registered store.
- A recent authenticated portable backup contains all registered stores and its logical member digests match the live stores.
- A recent recovery kit decrypts to the exact current keyring.
- The backup and recovery kit are separate files and their passphrases are available.
- The operator accepts that retirement is not secure erasure.

    python data_key_cli.py retire --key-id <32-character-key-id> --backup "D:/Offline/voiceassistant-current.vabackup" --recovery-kit "D:/Offline/voiceassistant-current.varecovery"

Type the exact RETIRE DATA KEY <key-id> confirmation. Enter both passphrases interactively. If any check fails, stop and investigate; do not delete files manually or bypass validation.

Retirement removes the selected key from the live keyring. It cannot erase copies held by old backups, recovery kits, snapshots, journals, filesystem history or SSD wear-leveling.

## 8. Recovery after keyring loss

- Stop the application and preserve the original data directory unchanged.
- Do not run bootstrap and do not delete or replace ciphertext.
- Confirm the correct Windows DPAPI profile or environment unlock secret is available.
- If the keyring is genuinely absent, use the explicit recovery-import workflow with the recovery kit and its passphrase. Import refuses to overwrite an existing keyring.
- Verify key status and decrypt/read representative stores before resuming service.
- If all usable key copies and recovery passphrases are lost, existing ciphertext is unrecoverable. Reinitializing a new key does not recover old data.

## 9. Incident handling

If a backup, recovery kit, passphrase or environment key may have been exposed:
1. Restrict access to the affected host and storage location.
2. Preserve logs and evidence; do not overwrite affected artifacts.
3. Assume every key included in an exposed recovery kit may be compromised.
4. Plan key rotation and data migration under a verified backup and maintenance window.
5. Review old backups and retention obligations before destruction; rotation alone does not revoke access to old copies.
