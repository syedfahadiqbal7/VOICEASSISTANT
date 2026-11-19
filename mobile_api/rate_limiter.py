"""Process-wide sliding-window quotas shared by every WebSocket for a device.

The desktop mobile API is intentionally a single-process service. A device
identity therefore has one bucket regardless of how many sockets it opens.
Unknown identities are rejected when the bounded bucket table is full rather
than evicting active quotas and allowing a rate-limit reset.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    close_connection: bool = False
    reason: str = ""


@dataclass
class _DeviceBucket:
    messages: Deque[float]
    user_messages: Deque[float]
    last_seen: float


class DeviceRateLimiter:
    def __init__(
        self,
        window_seconds: int = 60,
        max_messages: int = 120,
        max_user_messages: int = 20,
        max_devices: int = 4096,
    ):
        if window_seconds < 1 or max_messages < 1 or max_user_messages < 1 or max_devices < 1:
            raise ValueError("Rate-limit settings must be positive integers")
        if max_user_messages > max_messages:
            raise ValueError("User-message quota cannot exceed the total-message quota")
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self.max_user_messages = max_user_messages
        self.max_devices = max_devices
        self._buckets: OrderedDict[str, _DeviceBucket] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _prune(queue: Deque[float], now: float, window: int) -> None:
        while queue and now - queue[0] >= window:
            queue.popleft()

    def _prune_expired_buckets(self, now: float) -> None:
        expired = [
            device_id
            for device_id, bucket in self._buckets.items()
            if now - bucket.last_seen >= self.window_seconds
        ]
        for device_id in expired:
            self._buckets.pop(device_id, None)

    def consume(self, device_id: str, message_type: str, now: float | None = None) -> RateLimitDecision:
        identity = str(device_id or "").strip()
        if not identity or len(identity) > 128:
            return RateLimitDecision(False, True, "invalid_device_identity")

        current = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.get(identity)
            if bucket is None:
                if len(self._buckets) >= self.max_devices:
                    self._prune_expired_buckets(current)
                if len(self._buckets) >= self.max_devices:
                    return RateLimitDecision(False, True, "rate_limit_capacity_reached")
                bucket = _DeviceBucket(deque(), deque(), current)
                self._buckets[identity] = bucket
            else:
                bucket.last_seen = current
                self._buckets.move_to_end(identity)

            self._prune(bucket.messages, current, self.window_seconds)
            self._prune(bucket.user_messages, current, self.window_seconds)

            if len(bucket.messages) >= self.max_messages:
                return RateLimitDecision(False, True, "device_message_quota_exceeded")

            # Count every authenticated request attempt against the total quota,
            # including user messages rejected by the narrower user-message cap.
            # Otherwise a client could spam unlimited rejected requests and force
            # an unbounded stream of error responses.
            bucket.messages.append(current)
            if message_type == "user_message":
                if len(bucket.user_messages) >= self.max_user_messages:
                    return RateLimitDecision(False, False, "device_user_message_quota_exceeded")
                bucket.user_messages.append(current)
            return RateLimitDecision(True)

    def device_count(self) -> int:
        with self._lock:
            return len(self._buckets)
