"""Manage revocable mobile device credentials from the trusted desktop."""
from __future__ import annotations

import argparse
import json

from mobile_api.device_registry import registry


def main() -> int:
    parser = argparse.ArgumentParser(description="VoiceAssistant mobile device administration")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="Enroll a new mobile device")
    add.add_argument("name", help="Human-readable device name")
    commands.add_parser("list", help="List enrolled devices")
    revoke = commands.add_parser("revoke", help="Revoke a device immediately")
    revoke.add_argument("device_id", help="32-character device ID")
    args = parser.parse_args()

    if args.command == "add":
        created = registry.register(args.name)
        print("Device enrolled. Copy these credentials to the mobile app now; the secret is shown only once.")
        print(json.dumps(created, indent=2))
        return 0
    if args.command == "list":
        print(json.dumps(registry.list_devices(), indent=2))
        return 0
    if args.command == "revoke":
        if registry.revoke(args.device_id):
            print("Device revoked.")
            return 0
        print("Device not found or already revoked.")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
