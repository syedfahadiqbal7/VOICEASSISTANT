#!/usr/bin/env python3
"""
Phase 0 — Smoke tests (no GUI, no Ollama required for most cases).

Run from VoiceAssistant project root:
  python tests/phase0_smoke_tests.py

Or:
  python -m tests.phase0_smoke_tests
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CANDIDATES = [Path.cwd(), _HERE.parent, _HERE]
ROOT = Path.cwd()
for c in _CANDIDATES:
    if (c / "agent").is_dir():
        ROOT = c
        break
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Result:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name: str):
        self.passed += 1
        print(f"  PASS  {name}")

    def fail(self, name: str, msg: str):
        self.failed += 1
        self.errors.append((name, msg))
        print(f"  FAIL  {name}: {msg}")


def test_planner_fast_paths(r: _Result):
    try:
        from agent.planner import Planner

        class DummyLLM:
            pass

        p = Planner(DummyLLM())
        cases = [
            (
                "Create a file named pulse.txt on desktop with text alive",
                "pulse.txt",
                "alive",
            ),
            (
                "Write hello world to test.txt on desktop",
                "test.txt",
                "hello world",
            ),
            (
                "Create folder ProjectX on desktop and create file readme.txt with content hello",
                "readme.txt",
                "hello",
            ),
        ]
        for goal, fname, content in cases:
            plan = p._try_fast_file_plan(goal)
            if not plan or not plan.get("steps"):
                r.fail(f"fast_path:{fname}", "no plan")
                continue
            args = plan["steps"][0].get("arguments") or {}
            path = str(args.get("filepath") or "")
            body = str(args.get("content") or "")
            if fname not in path.replace("\\", "/"):
                r.fail(f"fast_path:{fname}", f"path={path}")
            elif content not in body:
                r.fail(f"fast_path:{fname}", f"content={body!r}")
            else:
                r.ok(f"fast_path:{fname}")

        if Planner.should_use_planner("What can you help me with?"):
            r.fail("should_use_planner:chat", "chat should not force planner")
        else:
            r.ok("should_use_planner:chat")

        if not Planner.should_use_planner(
            "Create a file named x.txt on desktop with text y"
        ):
            r.fail("should_use_planner:file", "file create should use planner")
        else:
            r.ok("should_use_planner:file")
    except Exception as e:
        r.fail("planner_fast_paths", traceback.format_exc(limit=3))


def test_verifier_file_checks(r: _Result):
    try:
        from agent.verifier import Verifier

        v = Verifier()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "smoke.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("hello smoke")

            res = v.verify({"type": "file_exists", "path": path})
            if res.get("ok"):
                r.ok("verifier:file_exists")
            else:
                r.fail("verifier:file_exists", res.get("message", ""))

            res = v.verify({"type": "file_contains", "path": path, "text": "smoke"})
            if res.get("ok"):
                r.ok("verifier:file_contains")
            else:
                r.fail("verifier:file_contains", res.get("message", ""))

            res = v.verify({"type": "always_true"}, {"is_error": False, "content": "ok"})
            if res.get("ok"):
                r.ok("verifier:always_true")
            else:
                r.fail("verifier:always_true", res.get("message", ""))

            res = v.verify(
                {"type": "file_exists", "path": os.path.join(td, "missing.txt")}
            )
            if not res.get("ok"):
                r.ok("verifier:missing_file")
            else:
                r.fail("verifier:missing_file", "expected fail")
    except Exception:
        r.fail("verifier", traceback.format_exc(limit=3))


def test_memory_crud(r: _Result):
    try:
        from agent.memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mem.json"
            m = MemoryStore(path=path)
            m.set_pref("desktop_alias", "Desktop")
            if m.get_pref("desktop_alias") != "Desktop":
                r.fail("memory:pref", "get/set mismatch")
            else:
                r.ok("memory:pref")
            m2 = MemoryStore(path=path)
            if m2.get_pref("desktop_alias") != "Desktop":
                r.fail("memory:persist", "reload lost pref")
            else:
                r.ok("memory:persist")
    except Exception:
        r.fail("memory_crud", traceback.format_exc(limit=3))


def test_undo_roundtrip(r: _Result):
    try:
        # Prefer agent.undo_manager; fall back to path load
        try:
            from agent.undo_manager import UndoManager
        except Exception:
            um_path = ROOT / "agent" / "undo_manager.py"
            spec = importlib.util.spec_from_file_location("undo_manager", um_path)
            mod = importlib.util.module_from_spec(spec)
            # config may be required
            spec.loader.exec_module(mod)
            UndoManager = mod.UndoManager

        with tempfile.TemporaryDirectory() as td:
            # Point undo dir via monkeypatch if needed — use real manager on temp file only
            path = os.path.join(td, "note.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("v1")
            u = UndoManager(max_entries=5)
            backup = u.snapshot_before_write(path)
            with open(path, "w", encoding="utf-8") as f:
                f.write("v2")
            u.record_write(path, backup, "v2")
            if not u.list_stack():
                r.fail("undo:stack", "empty after record")
            else:
                r.ok("undo:stack")
            res = u.undo_last()
            if not res.get("ok"):
                r.fail("undo:undo_last", res.get("message", ""))
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
                if data == "v1":
                    r.ok("undo:restored")
                else:
                    r.fail("undo:restored", f"content={data!r}")
    except Exception:
        r.fail("undo_roundtrip", traceback.format_exc(limit=5))


def test_disabled_plugin_constant(r: _Result):
    try:
        from agent.tool_registry import DISABLED_PLUGIN_FILES

        if "task_scheduler.py" in DISABLED_PLUGIN_FILES:
            r.ok("registry:task_scheduler_disabled")
        else:
            r.fail("registry:task_scheduler_disabled", "not in DISABLED_PLUGIN_FILES")
        return
    except Exception:
        pass
    # Fallback: source scan (full ToolRegistry may need Windows tool imports)
    try:
        src = (ROOT / "agent" / "tool_registry.py").read_text(encoding="utf-8")
        if "task_scheduler.py" in src and "DISABLED_PLUGIN_FILES" in src:
            r.ok("registry:task_scheduler_disabled_src")
        else:
            r.fail("registry:task_scheduler_disabled", "not found in tool_registry.py")
    except Exception:
        r.fail("registry", traceback.format_exc(limit=3))


def test_session_sync_jobs_shape(r: _Result):
    try:
        from agent.session_sync import SessionSync

        s = SessionSync()
        s.set_jobs(
            [
                {
                    "id": "abc",
                    "prompt": "Open notepad",
                    "label": "Open notepad",
                    "run_at": "2026-01-01T00:00:00",
                    "enabled": True,
                    "repeat": None,
                }
            ]
        )
        snap = s.snapshot()
        if snap.get("jobs_count") == 1 and snap.get("jobs"):
            r.ok("session_sync:jobs")
        else:
            r.fail("session_sync:jobs", str(snap))
        s.set_undo_available(True)
        if s.snapshot().get("undo_available") is True:
            r.ok("session_sync:undo_flag")
        else:
            r.fail("session_sync:undo_flag", "undo_available not set")
    except Exception:
        r.fail("session_sync", traceback.format_exc(limit=3))


def main():
    print("Phase 0 smoke tests")
    print(f"ROOT={ROOT}")
    r = _Result()
    test_planner_fast_paths(r)
    test_verifier_file_checks(r)
    test_memory_crud(r)
    test_undo_roundtrip(r)
    test_disabled_plugin_constant(r)
    test_session_sync_jobs_shape(r)
    print()
    print(f"Result: {r.passed} passed, {r.failed} failed")
    if r.errors:
        print("Failures:")
        for name, msg in r.errors:
            print(f" - {name}: {msg[:300]}")
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
