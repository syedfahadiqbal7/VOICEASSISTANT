"""Explicit local CLI for inspecting and migrating registered encrypted stores.

Migration is never automatic. The CLI authenticates a portable backup and
checks its registered-store coverage/content before calling the migration API.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
from pathlib import Path

from agent.backup_manager import BackupManager
from agent.data_protection import DataProtectionError
from agent.key_migration import MigrationError, inspect_migration, migrate_to_vaenc2
from config import BASE_DIR


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local-only VoiceAssistant encrypted-store migration."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect", help="Read-only inventory of registered encrypted stores")
    migrate = commands.add_parser("migrate", help="Explicitly migrate registered VAENC1 stores to VAENC2")
    migrate.add_argument("--backup", required=True, help="Recent portable backup covering current registered stores")
    return parser


def _verify_pre_migration_backup(backup_path: Path, passphrase: str) -> dict:
    manager = BackupManager(BASE_DIR)
    verified = manager.verify_backup(backup_path, passphrase)
    inventory = inspect_migration(BASE_DIR)
    if inventory["total"] == 0:
        raise MigrationError("No registered stores were inventoried; refusing migration")
    required = {
        row["path"][len("history/"):] if row["path"].startswith("history/") else row["path"]
        for row in inventory["files"]
    }
    missing = required - set(verified.get("file_names", []))
    if missing:
        raise MigrationError(
            "Verified backup is missing registered stores: " + ", ".join(sorted(missing))
        )
    current = manager._collect_entries()
    for name in sorted(required):
        digest = verified.get("file_sha256", {}).get(name)
        if name not in current or digest != hashlib.sha256(current[name]).hexdigest():
            raise MigrationError(
                f"Backup is stale or differs from current logical store: {name}"
            )
    return {
        "backup_sha256": verified["sha256"],
        "files": verified["files"],
        "registered_stores": inventory["total"],
        "legacy_vaenc1": inventory["legacy_vaenc1"],
        "current_vaenc2": inventory["current_vaenc2"],
    }


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            print(json.dumps(inspect_migration(BASE_DIR), indent=2))
            return 0

        print("This operation rewrites registered encrypted stores. Stop VoiceAssistant and every runtime writer first.")
        if input('Type "MIGRATE ALL REGISTERED DATA STORES" to continue: ').strip() != "MIGRATE ALL REGISTERED DATA STORES":
            print("Cancelled; no stores were changed.")
            return 2
        passphrase = getpass.getpass("Portable backup passphrase: ")
        backup = _verify_pre_migration_backup(Path(args.backup).expanduser(), passphrase)
        if backup["legacy_vaenc1"] == 0:
            print("No VAENC1 stores remain; no migration was necessary.")
            return 0
        result = migrate_to_vaenc2(BASE_DIR, confirm=True, backup_verified=True)
        print(json.dumps({"backup": backup, "migration": result}, indent=2))
        return 0 if result.get("complete") and not result.get("failed") else 1
    except (DataProtectionError, MigrationError, ValueError, PermissionError, OSError) as error:
        print(f"Migration refused or failed safely: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
