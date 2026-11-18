"""Local-only VoiceAssistant data-key administration CLI.

Never accepts backup passphrases as command-line arguments. This utility is
not registered with the mobile API, plugin system, or remote tool dispatcher.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from agent.key_rotation import (
    KeyRotationError,
    bootstrap_keyring,
    key_status,
    rotate_data_key,
)
from config import BASE_DIR


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local-only VoiceAssistant data-key administration."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show key IDs and retention metadata (never key material)")
    bootstrap = commands.add_parser("bootstrap", help="Create the protected keyring from the current legacy key")
    bootstrap.add_argument("--backup", required=True, help="Recent verified .vabackup file")
    rotate = commands.add_parser("rotate", help="Activate a new DEK and retain all existing keys")
    rotate.add_argument("--backup", required=True, help="Recent verified .vabackup file")
    return parser


def _confirm(expected: str) -> bool:
    print("This is a local data-key administration operation.")
    print("Create a fresh portable encrypted backup first; the supplied backup will be authenticated.")
    return input(f'Type "{expected}" to continue: ').strip() == expected


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            print(json.dumps(key_status(BASE_DIR), indent=2))
            return 0

        if args.command == "bootstrap":
            if not _confirm("BOOTSTRAP DATA KEY"):
                print("Cancelled; no keyring was changed.")
                return 2
            passphrase = getpass.getpass("Portable backup passphrase: ")
            result = bootstrap_keyring(
                BASE_DIR, Path(args.backup).expanduser(), passphrase, confirm=True
            )
        else:
            if not _confirm("ROTATE DATA KEY"):
                print("Cancelled; no key was activated.")
                return 2
            passphrase = getpass.getpass("Portable backup passphrase: ")
            result = rotate_data_key(
                BASE_DIR, Path(args.backup).expanduser(), passphrase, confirm=True
            )

        print(json.dumps(result, indent=2))
        if result.get("audit_warning"):
            print("WARNING: rotation completed, but the completion audit event could not be written.", file=sys.stderr)
            return 3
        return 0
    except (KeyRotationError, PermissionError, ValueError, OSError) as error:
        print(f"Key administration refused: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
