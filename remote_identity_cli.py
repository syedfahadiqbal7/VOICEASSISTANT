"""Explicit local administration for the desktop remote signing identity."""
from __future__ import annotations

import argparse
import json

from agent.remote_identity import RemoteIdentityError, RemoteIdentityStore


_CREATE_CONFIRMATION = "CREATE DESKTOP IDENTITY"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage the VoiceAssistant desktop identity used for authenticated remote sessions"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show whether a desktop identity is initialized")
    commands.add_parser("public", help="Show the public key and fingerprint only")
    commands.add_parser("bootstrap", help="Create the first desktop signing identity")

    args = parser.parse_args(argv)
    store = RemoteIdentityStore()
    try:
        if args.command == "status":
            print(json.dumps(store.status(), indent=2))
            return 0
        if args.command == "public":
            status = store.status()
            if not status["initialized"]:
                print("Desktop identity is not initialized. Run bootstrap locally first.")
                return 1
            print(json.dumps({
                "identity_id": status["identity_id"],
                "algorithm": status["algorithm"],
                "public_key": status["public_key"],
                "fingerprint_sha256": status["fingerprint_sha256"],
            }, indent=2))
            return 0
        if args.command == "bootstrap":
            print("This creates a long-lived desktop signing identity for future remote mobile sessions.")
            print("The private key is encrypted locally and is never displayed.")
            confirmation = input(f'Type "{_CREATE_CONFIRMATION}" to continue: ')
            if confirmation != _CREATE_CONFIRMATION:
                print("Identity creation cancelled.")
                return 1
            created = store.bootstrap(confirmed=True)
            print("Desktop identity created and encrypted. Keep a verified portable backup.")
            print(json.dumps({
                "identity_id": created["identity_id"],
                "algorithm": created["algorithm"],
                "public_key": created["public_key"],
                "fingerprint_sha256": created["fingerprint_sha256"],
                "created_at": created["created_at"],
            }, indent=2))
            return 0
    except (RemoteIdentityError, PermissionError, OSError, ValueError) as error:
        print(f"Operation refused: {error}")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
