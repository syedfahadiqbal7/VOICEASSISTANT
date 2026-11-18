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


def test_registry_stores_and_changes_roles_without_exposing_secrets(tmp_path, monkeypatch):
    from mobile_api.device_registry import MobileDeviceRegistry

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "61" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    enrolled = registry.register("Operator phone")
    assert enrolled["role"] == ROLE_OPERATOR
    principal = registry.authenticate_principal(enrolled["device_id"], enrolled["device_token"])
    assert principal["role"] == ROLE_OPERATOR

    assert registry.set_role(enrolled["device_id"], ROLE_VIEWER) is True
    principal = registry.authenticate_principal(enrolled["device_id"], enrolled["device_token"])
    assert principal["role"] == ROLE_VIEWER
    listed = registry.list_devices()[0]
    assert listed["role"] == ROLE_VIEWER
    assert "device_token" not in listed
    assert "token_sha256" not in listed


def test_registry_prevents_removing_last_active_admin(tmp_path, monkeypatch):
    import pytest
    from mobile_api.device_registry import MobileDeviceRegistry

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "62" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    admin = registry.register("Owner phone", role=ROLE_ADMIN)

    with pytest.raises(ValueError, match="last active admin"):
        registry.set_role(admin["device_id"], ROLE_OPERATOR)
    with pytest.raises(ValueError, match="last active admin"):
        registry.revoke(admin["device_id"])

    second_admin = registry.register("Backup admin", role=ROLE_ADMIN)
    assert registry.set_role(admin["device_id"], ROLE_OPERATOR) is True

    # The backup admin is now the only active admin and must be protected.
    with pytest.raises(ValueError, match="last active admin"):
        registry.revoke(second_admin["device_id"])

    # Restore the first device's admin role, then revoke the backup safely.
    assert registry.set_role(admin["device_id"], ROLE_ADMIN) is True
    assert registry.revoke(second_admin["device_id"]) is True


def test_server_principal_role_changes_and_revocation_are_rechecked(tmp_path, monkeypatch):
    import mobile_api.server as server

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "63" * 32)
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_HOST", "0.0.0.0")
    monkeypatch.setattr(server.mobile_devices, "path", tmp_path / "mobile_devices.json")
    enrolled = server.mobile_devices.register("Role-change phone", role=ROLE_OPERATOR)

    class FakeSocket:
        headers = {
            "x-voiceassistant-device-id": enrolled["device_id"],
            "x-voiceassistant-device-token": enrolled["device_token"],
        }

    socket = FakeSocket()
    assert server._reauthenticate_mobile_principal(socket)["role"] == ROLE_OPERATOR
    assert server.mobile_devices.set_role(enrolled["device_id"], ROLE_APPROVER) is True
    assert server._reauthenticate_mobile_principal(socket)["role"] == ROLE_APPROVER
    assert server.mobile_devices.revoke(enrolled["device_id"]) is True
    assert server._reauthenticate_mobile_principal(socket) is None


def test_legacy_and_loopback_principals_never_receive_admin_role(monkeypatch, tmp_path):
    import mobile_api.server as server

    monkeypatch.setattr(server.mobile_devices, "path", tmp_path / "missing.json")
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_TOKEN", "legacy-token-long-enough-for-test")
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_HOST", "0.0.0.0")
    assert server._mobile_principal({"x-voiceassistant-token": "legacy-token-long-enough-for-test"})["role"] == ROLE_OPERATOR

    monkeypatch.delenv("VOICEASSISTANT_MOBILE_TOKEN")
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_HOST", "127.0.0.1")
    assert server._mobile_principal({})["role"] == ROLE_OPERATOR


def test_connection_manager_selects_only_authorized_approval_roles():
    from mobile_api.connection_manager import ConnectionManager

    manager = ConnectionManager()
    operator = object()
    approver = object()
    manager.active_connections.extend([operator, approver])
    manager.connection_ids[operator] = "operator-1"
    manager.connection_ids[approver] = "approver-1"
    manager.connection_roles[operator] = ROLE_OPERATOR
    manager.connection_roles[approver] = ROLE_APPROVER

    target, target_id = manager.first_connection(allowed_roles={"admin", "approver"})
    assert target is approver
    assert target_id == "approver-1"



def test_malformed_unhashable_registry_roles_fail_closed(tmp_path, monkeypatch):
    import pytest
    from agent.data_protection import encrypt_bytes
    from mobile_api.device_registry import MobileDeviceRegistry

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "64" * 32)
    path = tmp_path / "mobile_devices.json"
    malformed = {
        "version": 1,
        "devices": [{
            "device_id": "a" * 32,
            "name": "Tampered entry",
            "token_sha256": "0" * 64,
            "role": ["admin"],
            "revoked_at": None,
        }],
    }
    path.write_bytes(encrypt_bytes(
        __import__("json").dumps(malformed).encode("utf-8"),
        context="mobile_devices.json",
    ))
    registry = MobileDeviceRegistry(path)
    with pytest.raises(ValueError, match="unsupported role"):
        registry.list_devices()
    with pytest.raises(ValueError, match="Unsupported device role"):
        registry.register("Bad role", role=["admin"])
    with pytest.raises(ValueError, match="Unsupported device role"):
        registry.set_role("a" * 32, role=["admin"])
