"""Interactive command-line interface for encrypted VoiceAssistant data backups.

Passphrases are requested with getpass and are never accepted as command-line
arguments, which avoids leaking them through shell history or process listings.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from agent.backup_manager import BackupManager
from agent.data_protection import DataProtectionError
from config import BASE_DIR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or restore portable encrypted VoiceAssistant data backups."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create an encrypted backup")
    create.add_argument("destination", help="Destination .vabackup file")
    restore = commands.add_parser("restore", help="Restore an encrypted backup")
    restore.add_argument("source", help="Source .vabackup file")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    manager = BackupManager(BASE_DIR)
    try:
        if args.command == "create":
            first = getpass.getpass("Backup passphrase (minimum 12 characters): ")
            second = getpass.getpass("Confirm backup passphrase: ")
            if first != second:
                print("Passphrases do not match; no backup was created.", file=sys.stderr)
                return 2
            result = manager.create_backup(Path(args.destination).expanduser(), first)
            print(f"Encrypted backup created: {result['path']}")
            print(f"Files: {result['files']} | Size: {result['bytes']} bytes")
            return 0

        print("Restore will replace matching local data files; files absent from the backup are left unchanged.")
        confirmation = input("Type RESTORE to continue: ").strip()
        if confirmation != "RESTORE":
            print("Restore cancelled; no files were changed.")
            return 2
        passphrase = getpass.getpass("Backup passphrase: ")
        result = manager.restore_backup(
            Path(args.source).expanduser(),
            passphrase,
            confirm_overwrite=True,
        )
        print(f"Restored {result['count']} files into the local runtime data directory.")
        return 0
    except (DataProtectionError, ValueError, FileExistsError, PermissionError, OSError) as error:
        print(f"Backup operation failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
