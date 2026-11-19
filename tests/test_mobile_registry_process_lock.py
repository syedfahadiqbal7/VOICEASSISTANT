"""Cross-process concurrency regressions for the encrypted mobile registry."""
from __future__ import annotations

import multiprocessing
import os

from mobile_api.device_registry import MobileDeviceRegistry


_TEST_KEY = "ab" * 32


def _register_worker(path: str, name: str, result_queue) -> None:
    os.environ["VOICEASSISTANT_DATA_KEY"] = _TEST_KEY
    result = MobileDeviceRegistry(path).register(name)
    result_queue.put(result["device_id"])


def _revoke_admin_worker(path: str, device_id: str, barrier, result_queue) -> None:
    os.environ["VOICEASSISTANT_DATA_KEY"] = _TEST_KEY
    barrier.wait(timeout=15)
    try:
        result_queue.put(("result", MobileDeviceRegistry(path).revoke(device_id)))
    except ValueError as error:
        result_queue.put(("error", str(error)))


def test_concurrent_process_enrollment_preserves_both_devices(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", _TEST_KEY)
    path = tmp_path / "mobile_devices.json"
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [
        context.Process(target=_register_worker, args=(str(path), "Phone A", results)),
        context.Process(target=_register_worker, args=(str(path), "Phone B", results)),
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    device_ids = {results.get(timeout=5), results.get(timeout=5)}
    assert len(device_ids) == 2
    registry = MobileDeviceRegistry(path)
    assert {item["device_id"] for item in registry.list_devices()} == device_ids
    assert path.with_name(path.name + ".lock").exists()


def test_concurrent_admin_revocation_cannot_remove_last_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", _TEST_KEY)
    path = tmp_path / "mobile_devices.json"
    registry = MobileDeviceRegistry(path)
    first = registry.register("Admin A", role="admin")
    second = registry.register("Admin B", role="admin")

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(target=_revoke_admin_worker, args=(str(path), first["device_id"], barrier, results)),
        context.Process(target=_revoke_admin_worker, args=(str(path), second["device_id"], barrier, results)),
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=5), results.get(timeout=5)]
    assert sum(kind == "result" and value is True for kind, value in outcomes) == 1
    assert sum(kind == "error" and "last active admin" in value for kind, value in outcomes) == 1
    assert sum(item["role"] == "admin" and item["status"] == "active" for item in registry.list_devices()) == 1
