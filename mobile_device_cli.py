"""Manage enrolled mobile devices from the trusted desktop only."""
from __future__ import annotations

import argparse
import json

from mobile_api.authorization import VALID_ROLES, DEFAULT_ROLE
from mobile_api.device_registry import registry


def main() -> int:
    parser = argparse.ArgumentParser(description="VoiceAssistant mobile device administration")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Enroll a new mobile device")
    add.add_argument("name", help="Human-readable device name")
    add.add_argument("--role", choices=sorted(VALID_ROLES), default=DEFAULT_ROLE,
                     help="Initial role (default: operator); use admin only for the owner device")

    commands.add_parser("list", help="List enrolled devices and roles without secrets")

    revoke = commands.add_parser("revoke", help="Revoke a device")
    revoke.add_argument("device_id", help="32-character device ID")

    set_role = commands.add_parser("set-role", help="Change an active device role")
    set_role.add_argument("device_id", help="32-character device ID")
    set_role.add_argument("role", choices=sorted(VALID_ROLES))

    args = parser.parse_args()
    try:
        if args.command == "add":
            created = registry.register(args.name, role=args.role)
            print("Device enrolled. Copy these credentials to the mobile app now; the secret is shown only once.")
            print(json.dumps(created, indent=2))
            return 0
        if args.command == "list":
            print(json.dumps(registry.list_devices(), indent=2))
            return 0
        if args.command == "set-role":
            if registry.set_role(args.device_id, args.role):
                print("Device role updated. Active sessions will observe the change during revalidation.")
                return 0
            print("Device not found, revoked, or already has that role.")
            return 1
        if args.command == "revoke":
            if registry.revoke(args.device_id):
                print("Device revoked. Active sessions will be closed during revalidation.")
                return 0
            print("Device not found or already revoked.")
            return 1
    except (ValueError, OSError) as error:
        print(f"Operation refused: {error}")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
