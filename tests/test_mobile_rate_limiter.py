"""Mobile process-wide per-device rate-limit regressions."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from mobile_api.rate_limiter import DeviceRateLimiter


def test_multiple_websockets_share_the_same_device_quota():
    limiter = DeviceRateLimiter(window_seconds=60, max_messages=3, max_user_messages=2)
    # Each call represents a message arriving on any socket for this identity.
    assert limiter.consume("device-A", "ping", now=10).allowed
    assert limiter.consume("device-A", "ping", now=11).allowed
    blocked = limiter.consume("device-A", "ping", now=12)
    assert blocked.allowed is False
    assert blocked.close_connection is True
    assert blocked.reason == "device_message_quota_exceeded"


def test_different_devices_have_independent_quotas():
    limiter = DeviceRateLimiter(window_seconds=60, max_messages=1, max_user_messages=1)
    assert limiter.consume("device-A", "ping", now=1).allowed
    assert limiter.consume("device-B", "ping", now=1).allowed
    assert limiter.consume("device-A", "ping", now=2).close_connection
    assert limiter.consume("device-B", "ping", now=2).close_connection


def test_user_message_quota_is_shared_but_does_not_close_socket():
    limiter = DeviceRateLimiter(window_seconds=60, max_messages=5, max_user_messages=2)
    assert limiter.consume("device-A", "user_message", now=1).allowed
    assert limiter.consume("device-A", "ping", now=2).allowed
    assert limiter.consume("device-A", "user_message", now=3).allowed
    blocked = limiter.consume("device-A", "user_message", now=4)
    assert blocked.allowed is False
    assert blocked.close_connection is False
    assert blocked.reason == "device_user_message_quota_exceeded"
    # A non-user command still works because only the user-message quota is full.
    assert limiter.consume("device-A", "ping", now=5).allowed


def test_window_expiry_restores_device_quota():
    limiter = DeviceRateLimiter(window_seconds=10, max_messages=2, max_user_messages=1)
    assert limiter.consume("device-A", "ping", now=0).allowed
    assert limiter.consume("device-A", "ping", now=1).allowed
    assert limiter.consume("device-A", "ping", now=2).close_connection
    assert limiter.consume("device-A", "ping", now=10).allowed


def test_concurrent_connections_cannot_exceed_total_device_quota():
    limiter = DeviceRateLimiter(window_seconds=60, max_messages=20, max_user_messages=20)

    def send(_):
        return limiter.consume("device-A", "ping", now=100).allowed

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(send, range(100)))

    assert sum(results) == 20


def test_bucket_capacity_fails_closed_instead_of_evicting_active_quota():
    limiter = DeviceRateLimiter(
        window_seconds=60,
        max_messages=10,
        max_user_messages=5,
        max_devices=2,
    )
    assert limiter.consume("device-A", "ping", now=1).allowed
    assert limiter.consume("device-B", "ping", now=1).allowed
    denied = limiter.consume("device-C", "ping", now=2)
    assert denied.allowed is False
    assert denied.close_connection is True
    assert denied.reason == "rate_limit_capacity_reached"
    assert limiter.device_count() == 2


def test_expired_identity_bucket_is_reclaimed_when_capacity_is_reached():
    limiter = DeviceRateLimiter(
        window_seconds=10,
        max_messages=10,
        max_user_messages=5,
        max_devices=1,
    )
    assert limiter.consume("device-A", "ping", now=1).allowed
    assert limiter.consume("device-B", "ping", now=11).allowed
    assert limiter.device_count() == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window_seconds": 0},
        {"max_messages": 0},
        {"max_user_messages": 0},
        {"max_devices": 0},
        {"max_messages": 2, "max_user_messages": 3},
    ],
)
def test_invalid_rate_limit_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        DeviceRateLimiter(**kwargs)


@pytest.mark.parametrize("identity", ["", " ", None, "x" * 129])
def test_invalid_identity_is_denied(identity):
    limiter = DeviceRateLimiter()
    result = limiter.consume(identity, "ping", now=1)
    assert result.allowed is False
    assert result.close_connection is True


def test_rejected_user_messages_still_consume_total_request_quota():
    limiter = DeviceRateLimiter(window_seconds=60, max_messages=3, max_user_messages=1)
    assert limiter.consume("device-A", "user_message", now=1).allowed
    throttled = limiter.consume("device-A", "user_message", now=2)
    assert throttled.allowed is False
    assert throttled.close_connection is False
    assert limiter.consume("device-A", "ping", now=3).allowed
    # Rejected user-message attempts count toward the total quota too.
    flooded = limiter.consume("device-A", "ping", now=4)
    assert flooded.allowed is False
    assert flooded.close_connection is True
