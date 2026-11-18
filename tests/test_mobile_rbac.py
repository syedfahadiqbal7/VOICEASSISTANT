from mobile_api.authorization import (
    DEFAULT_ROLE,
    LEGACY_ROLE,
    ROLE_ADMIN,
    ROLE_APPROVER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VALID_ROLES,
    is_allowed,
    is_valid_role,
)


def test_supported_roles_and_least_privilege_defaults():
    assert VALID_ROLES == {ROLE_ADMIN, ROLE_OPERATOR, ROLE_APPROVER, ROLE_VIEWER}
    assert DEFAULT_ROLE == ROLE_OPERATOR
    assert LEGACY_ROLE == ROLE_OPERATOR
    assert is_valid_role("superuser") is False


def test_admin_can_use_all_mobile_commands():
    for command in (
        "ping", "get_session_state", "list_schedules", "user_message",
        "cancel_schedule", "undo_last", "approval_response",
    ):
        assert is_allowed(ROLE_ADMIN, command) is True


def test_operator_cannot_approve_or_administer():
    for command in ("ping", "get_session_state", "list_schedules", "user_message", "cancel_schedule", "undo_last"):
        assert is_allowed(ROLE_OPERATOR, command) is True
    assert is_allowed(ROLE_OPERATOR, "approval_response") is False
    assert is_allowed(ROLE_OPERATOR, "enroll_device") is False


def test_approver_can_only_observe_and_approve():
    for command in ("ping", "get_session_state", "list_schedules", "approval_response"):
        assert is_allowed(ROLE_APPROVER, command) is True
    assert is_allowed(ROLE_APPROVER, "user_message") is False
    assert is_allowed(ROLE_APPROVER, "undo_last") is False


def test_viewer_is_read_only():
    for command in ("ping", "get_session_state", "list_schedules"):
        assert is_allowed(ROLE_VIEWER, command) is True
    for command in ("user_message", "cancel_schedule", "undo_last", "approval_response"):
        assert is_allowed(ROLE_VIEWER, command) is False


def test_unknown_role_command_and_malformed_values_fail_closed():
    assert is_allowed("unknown", "user_message") is False
    assert is_allowed(None, "ping") is False
    assert is_allowed(ROLE_ADMIN, None) is False
    assert is_allowed(ROLE_ADMIN, "future_command") is False
