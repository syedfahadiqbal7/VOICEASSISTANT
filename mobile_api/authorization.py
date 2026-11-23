"""Server-side role and mobile-message permission policy.

Roles are resolved from the encrypted device registry, never from client payloads.
This module is deliberately pure so the permission matrix can be exhaustively tested.
"""
from __future__ import annotations

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_APPROVER = "approver"
ROLE_VIEWER = "viewer"

VALID_ROLES = frozenset({
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_APPROVER,
    ROLE_VIEWER,
})
DEFAULT_ROLE = ROLE_OPERATOR
LEGACY_ROLE = ROLE_OPERATOR

# Keep this map explicit: adding a new WebSocket command must require an
# intentional authorization decision rather than inheriting a permissive default.
ROLE_PERMISSIONS = {
    ROLE_ADMIN: frozenset({
        "ping",
        "get_session_state",
        "list_schedules",
        "list_schedule_history",
        "user_message",
        "cancel_schedule",
        "undo_last",
        "approval_response",
    }),
    ROLE_OPERATOR: frozenset({
        "ping",
        "get_session_state",
        "list_schedules",
        "user_message",
        "cancel_schedule",
        "undo_last",
    }),
    ROLE_APPROVER: frozenset({
        "ping",
        "get_session_state",
        "list_schedules",
        "approval_response",
    }),
    ROLE_VIEWER: frozenset({
        "ping",
        "get_session_state",
        "list_schedules",
    }),
}


def is_valid_role(role: object) -> bool:
    """Return whether role is one of the explicitly supported role names."""
    return isinstance(role, str) and role in VALID_ROLES


def is_allowed(role: object, message_type: object) -> bool:
    """Fail closed for unknown roles, malformed commands, and unmapped commands."""
    if not is_valid_role(role) or not isinstance(message_type, str):
        return False
    return message_type in ROLE_PERMISSIONS[role]
