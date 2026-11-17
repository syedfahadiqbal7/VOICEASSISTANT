"""Interactive controls for user-configurable local data retention."""
from __future__ import annotations

import argparse
import json
import sys

from agent.data_protection import DataProtectionError
from agent.privacy_manager import PrivacyManager


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview and apply optional retention rules for VoiceAssistant private data."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="Show the current retention policy")
    commands.add_parser("preview", help="Preview what would be removed without deleting anything")
    setting = commands.add_parser("set", help="Set retention in days; use 0 to disable a rule")
    setting.add_argument("--conversation-days", type=int)
    setting.add_argument("--audit-days", type=int)
    setting.add_argument("--memory-days", type=int)
    setting.add_argument("--completed-job-days", type=int)
    commands.add_parser("purge", help="Apply the configured retention rules after confirmation")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        manager = PrivacyManager()
        if args.command == "show":
            print(json.dumps(manager.get_policy(), indent=2))
            return 0
        if args.command == "set":
            values = {
                "conversation_days": args.conversation_days,
                "audit_days": args.audit_days,
                "memory_days": args.memory_days,
                "completed_job_days": args.completed_job_days,
            }
            values = {key: value for key, value in values.items() if value is not None}
            if not values:
                print("Set at least one retention option.", file=sys.stderr)
                return 2
            print(json.dumps(manager.set_retention(**values), indent=2))
            return 0
        preview = manager.preview()
        print(json.dumps(preview, indent=2))
        if args.command == "preview":
            print("Preview only; no data was deleted.")
            return 0
        print("Close VoiceAssistant and create a portable backup before purging important data.")
        if input("Type PURGE to permanently remove the eligible data: ").strip() != "PURGE":
            print("Purge cancelled; no files were deleted.")
            return 2
        result = manager.purge(confirm=True)
        print(json.dumps(result["deleted"], indent=2))
        print("Deletion is not guaranteed secure erasure; filesystem snapshots may retain old blocks.")
        return 0
    except (DataProtectionError, ValueError, PermissionError, OSError) as error:
        print(f"Privacy operation failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
