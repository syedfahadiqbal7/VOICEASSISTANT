"""Local-only VoiceAssistant data-key administration CLI.

Passphrases are collected with getpass and are never accepted as CLI arguments.
No key administration command is exposed through mobile or remote dispatch.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from agent.key_recovery import (
    RecoveryKitError, export_recovery_kit, import_recovery_kit,
)
from agent.key_rotation import (
    KeyRotationError, bootstrap_keyring, key_status, rotate_data_key, retire_data_key,
)
from config import BASE_DIR


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local-only VoiceAssistant data-key administration.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show key IDs and retention metadata (never key material)")
    bootstrap = commands.add_parser("bootstrap", help="Create the protected keyring from the current legacy key")
    bootstrap.add_argument("--backup", required=True, help="Recent verified .vabackup file")
    rotate = commands.add_parser("rotate", help="Activate a new DEK and retain all existing keys")
    rotate.add_argument("--backup", required=True, help="Recent verified .vabackup file")
    export = commands.add_parser("recovery-export", help="Export all key generations to a passphrase-protected recovery kit")
    export.add_argument("--output", required=True, help="New .varecovery destination; existing files are never overwritten")
    restore = commands.add_parser("recovery-import", help="Restore a recovery kit only when no keyring exists")
    restore.add_argument("--kit", required=True, help="Passphrase-protected .varecovery file")
    retire = commands.add_parser("retire", help="Retire an unused retained/legacy key after full safety verification")
    retire.add_argument("--key-id", required=True, help="Exact 32-character key ID to retire")
    retire.add_argument("--backup", required=True, help="Recent verified .vabackup file")
    retire.add_argument("--recovery-kit", required=True, help="Recent .varecovery containing the exact current keyring")
    return parser


def _confirm(expected: str, warning: str) -> bool:
    print("LOCAL-ONLY DATA KEY OPERATION")
    print(warning)
    return input(f'Type "{expected}" to continue: ').strip() == expected


def _new_recovery_passphrase() -> str:
    first = getpass.getpass("New recovery-kit passphrase (minimum 16 characters): ")
    second = getpass.getpass("Confirm recovery-kit passphrase: ")
    if first != second:
        raise RecoveryKitError("Recovery passphrases did not match")
    return first


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            print(json.dumps(key_status(BASE_DIR), indent=2))
            return 0

        if args.command == "bootstrap":
            if not _confirm("BOOTSTRAP DATA KEY", "A verified recent portable backup is required."):
                print("Cancelled; no keyring was changed.")
                return 2
            phrase = getpass.getpass("Portable backup passphrase: ")
            result = bootstrap_keyring(BASE_DIR, Path(args.backup).expanduser(), phrase, confirm=True)
        elif args.command == "rotate":
            if not _confirm("ROTATE DATA KEY", "A verified recent portable backup is required."):
                print("Cancelled; no key was activated.")
                return 2
            phrase = getpass.getpass("Portable backup passphrase: ")
            result = rotate_data_key(BASE_DIR, Path(args.backup).expanduser(), phrase, confirm=True)
        elif args.command == "recovery-export":
            if not _confirm("EXPORT RECOVERY KIT", "The kit contains every active, retained and legacy key. Store it offline and separately from this device."):
                print("Cancelled; no recovery kit was created.")
                return 2
            phrase = _new_recovery_passphrase()
            result = export_recovery_kit(BASE_DIR, Path(args.output).expanduser(), phrase)
        elif args.command == "recovery-import":
            if not _confirm("IMPORT RECOVERY KIT", "Import is permitted only when no keyring exists. Existing encrypted data depends on these keys."):
                print("Cancelled; no keyring was changed.")
                return 2
            phrase = getpass.getpass("Recovery-kit passphrase: ")
            result = import_recovery_kit(BASE_DIR, Path(args.kit).expanduser(), phrase, confirm=True)
        else:
            expected = f"RETIRE DATA KEY {args.key_id}"
            warning = ("This permanently removes the selected key from the live keyring. "
                       "All registered stores, a recent portable backup and an exact recovery kit must pass verification.")
            if not _confirm(expected, warning):
                print("Cancelled; no key was retired.")
                return 2
            backup_phrase = getpass.getpass("Portable backup passphrase: ")
            recovery_phrase = getpass.getpass("Recovery-kit passphrase: ")
            result = retire_data_key(
                BASE_DIR, args.key_id, Path(args.backup).expanduser(), backup_phrase,
                Path(args.recovery_kit).expanduser(), recovery_phrase, confirm=True,
            )

        print(json.dumps(result, indent=2))
        if result.get("audit_warning"):
            print("WARNING: the key operation committed, but its completion audit event failed.", file=sys.stderr)
            return 3
        return 0
    except (KeyRotationError, RecoveryKitError, PermissionError, ValueError, OSError) as error:
        print(f"Key administration refused: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
