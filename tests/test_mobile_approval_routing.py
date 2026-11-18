from threading import Event

from mobile_api.bridge import MobileBridge


def _pending_bridge_request(expected_client="approver-1"):
    bridge = MobileBridge()
    request_id = "approval-request-1"
    event = Event()
    bridge._pending_approvals[request_id] = event
    bridge._approval_results[request_id] = False
    bridge._approval_clients[request_id] = expected_client
    bridge._active_approval_id = request_id
    return bridge, request_id, event


def test_approval_can_only_be_resolved_by_the_challenged_client():
    bridge, request_id, event = _pending_bridge_request()

    assert bridge.resolve_approval(request_id, True, client_id="operator-1") is False
    assert event.is_set() is False
    assert bridge._approval_results[request_id] is False

    assert bridge.resolve_approval(request_id, True, client_id="approver-1") is True
    assert event.is_set() is True
    assert bridge._approval_results[request_id] is True


def test_mobile_approval_rejects_non_boolean_and_unknown_requests():
    bridge, request_id, event = _pending_bridge_request()

    assert bridge.resolve_approval(request_id, "true", client_id="approver-1") is False
    assert bridge.resolve_approval("unknown-request", True, client_id="approver-1") is False
    assert event.is_set() is False


def test_disconnect_denies_only_the_matching_mobile_challenge_without_desktop_fallback():
    bridge, request_id, event = _pending_bridge_request()
    bridge.client_disconnected("another-client")
    assert event.is_set() is False

    bridge.client_disconnected("approver-1")
    assert event.is_set() is True
    assert bridge._approval_results[request_id] is False


def test_disconnect_preserves_desktop_approval_fallback():
    bridge, request_id, event = _pending_bridge_request()
    bridge._original_on_security_gate = lambda *_args: None

    bridge.client_disconnected("approver-1")

    assert event.is_set() is False
    assert bridge._approval_results[request_id] is False



def test_concurrent_security_gates_are_serialized_for_single_prompt_mobile_ui():
    import threading
    import time

    bridge = MobileBridge()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()

    def fake_gate(label, _callback, _risk):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        if label == "first":
            first_entered.set()
            assert release_first.wait(timeout=2)
        else:
            second_entered.set()
        with state_lock:
            active -= 1

    bridge._wrapped_security_gate_serialized = fake_gate
    first = threading.Thread(target=bridge._wrapped_security_gate, args=("first", lambda: None, "high"))
    second = threading.Thread(target=bridge._wrapped_security_gate, args=("second", lambda: None, "high"))
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    time.sleep(0.05)
    assert second_entered.is_set() is False
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert second_entered.is_set() is True
    assert maximum_active == 1
