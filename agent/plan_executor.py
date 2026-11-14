# agent/plan_executor.py
"""
Phase 1 / 1B — Plan Executor
Runs a planner plan step-by-step with confirmation language, security gates + verification.
"""

from __future__ import annotations

import json
import queue
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agent.verifier import Verifier


class PlanExecutor:
    def __init__(
        self,
        tool_registry,
        on_status: Callable[[str, Optional[str]], None],
        on_transcript: Callable[[str], None],
        on_speak: Callable[[str], None],
        on_security_gate: Callable[[str, Callable, str], None],
        analyzer=None,
        memory=None,
    ):
        self.tools = tool_registry
        self.on_status = on_status
        self.on_transcript = on_transcript
        self.on_speak = on_speak
        self.on_security_gate = on_security_gate
        self.analyzer = analyzer
        self.memory = memory
        self.cancel_check = None  # optional callable -> bool
        self.undo = None
        self.session_sync = None  # optional; mobile plan checklist
        self.policy = None  # optional Phase 3 PolicyEngine
        self.verifier = Verifier()

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _log(self, text: str, *, stamp: bool = True):
        """One timestamp per block; stamp=False for low-noise step chatter."""
        text = "" if text is None else str(text)
        if not self.on_transcript:
            return
        if not stamp:
            self.on_transcript(text)
            return
        ts = self._ts()
        lines = text.splitlines() or [""]
        if len(lines) == 1:
            self.on_transcript(f"[{ts}] {lines[0]}")
        else:
            body = lines[0] + chr(10) + chr(10).join(lines[1:])
            self.on_transcript(f"[{ts}] " + body)


    def _publish_plan(
        self,
        plan: Dict[str, Any],
        step_statuses: Dict[Any, str],
        status: str,
        current_step: Any = None,
    ) -> None:
        """Push checklist to session_sync for mobile UI."""
        if not self.session_sync:
            return
        try:
            steps_out = []
            for s in plan.get("steps") or []:
                sid = s.get("id")
                steps_out.append(
                    {
                        "id": sid,
                        "title": s.get("title") or f"Step {sid}",
                        "tool": s.get("tool"),
                        "risk": s.get("risk") or "low",
                        "status": step_statuses.get(sid, "pending"),
                    }
                )
            self.session_sync.set_plan(
                {
                    "goal": plan.get("goal") or "",
                    "status": status,
                    "current_step": current_step,
                    "steps": steps_out,
                }
            )
        except Exception as e:
            self._log(f"[SessionSync] plan publish skipped: {e}")

    def format_plan(self, plan: Dict[str, Any]) -> str:
        lines = [f"Plan for: {plan.get('goal', '')}", ""]
        for step in plan.get("steps", []):
            risk = (step.get("risk") or "low").upper()
            lines.append(
                f"  {step.get('id')}. [{risk}] {step.get('title')} "
                f"→ {step.get('tool')}"
            )
        if plan.get("needs_confirmation"):
            lines.append("")
            lines.append("Confirmation required before executing mutating steps.")
        return "\n".join(lines)

    def _confirmation_speech(self, plan: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
        titles = [
            str(s.get("title") or s.get("tool") or f"step {s.get('id')}")
            for s in steps
        ]
        if not titles:
            return "I have an empty plan."
        if len(titles) == 1:
            return f"I will do this: {titles[0]}. Please approve when asked."
        numbered = "; ".join(f"{i+1}) {x}" for i, x in enumerate(titles))
        return (
            f"I will do {len(titles)} steps: {numbered}. "
            "Please confirm the plan, then approve each sensitive action."
        )

    def _confirm_plan_gate(self, plan: Dict[str, Any], steps: List[Dict[str, Any]]) -> bool:
        lines = [
            "Action: Confirm multi-step plan",
            f"Goal: {plan.get('goal', '')}",
            "",
        ]
        for s in steps:
            lines.append(
                f"  {s.get('id')}. [{(s.get('risk') or 'low').upper()}] "
                f"{s.get('title')} → {s.get('tool')}"
            )
        display = "\n".join(lines)
        done: queue.Queue = queue.Queue()

        def approve():
            done.put(True)

        self.on_security_gate(display, approve, "low")
        try:
            return bool(done.get(timeout=300))
        except queue.Empty:
            return False

    def execute_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = plan.get("steps") or []
        results = []
        step_statuses: Dict[Any, str] = {
            s.get("id"): "pending" for s in steps
        }
        self._log("[Planner] " + self.format_plan(plan).replace("\n", "\n[Planner] "))
        self._publish_plan(plan, step_statuses, status="pending", current_step=None)

        spoken = self._confirmation_speech(plan, steps)
        self._log(f"[Planner] {spoken}")
        self.on_speak(spoken)

        mutating_tools = {
            "write_developer_code",
            "write_docx",
            "write_pdf",
            "write_docx",
            "write_pdf",
            "execute_powershell_admin",
            "install_python_dependencies",
            "screen_controller",
        }
        mutating = [
            s
            for s in steps
            if (s.get("tool") or "") in mutating_tools
            or (s.get("risk") or "low") in ("medium", "high")
        ]
        need_confirm = (
            bool(plan.get("needs_confirmation"))
            or len(mutating) >= 2
            or len(steps) >= 2
        )

        if need_confirm and steps:
            self._publish_plan(plan, step_statuses, status="awaiting_confirm", current_step=None)
            if not self._confirm_plan_gate(plan, steps):
                msg = "Plan cancelled by user before execution."
                self._log(f"[Planner] {msg}")
                self.on_speak("Okay, I cancelled the plan.")
                self._publish_plan(plan, step_statuses, status="cancelled", current_step=None)
                return {
                    "ok": False,
                    "completed_steps": 0,
                    "failed_step": None,
                    "message": msg,
                    "results": [],
                }

        self.on_speak(f"Starting {len(steps)} step plan now.")
        self._publish_plan(plan, step_statuses, status="running", current_step=None)

        for step in steps:
            if callable(self.cancel_check) and self.cancel_check():
                msg = "Plan stopped by user."
                self._log(f"[Planner] {msg}")
                self.on_speak("Stopped.")
                self._publish_plan(plan, step_statuses, status="cancelled", current_step=None)
                self.on_status("Ready", "#27AE60")
                return {
                    "ok": False,
                    "completed_steps": sum(1 for s in step_statuses.values() if s == "done"),
                    "failed_step": None,
                    "message": msg,
                    "results": results,
                }
            step_id = step.get("id")
            title = step.get("title") or f"Step {step_id}"
            tool_name = step.get("tool")
            args = step.get("arguments") or {}
            risk = (step.get("risk") or "low").lower()
            success_check = step.get("success_check") or {"type": "always_true"}

            step_statuses[step_id] = "running"
            self._publish_plan(plan, step_statuses, status="running", current_step=step_id)

            self.on_status(f"Step {step_id}/{len(steps)}: {title}", "#9B59B6")
            self._log(f"[Plan] Running step {step_id}: {title} ({tool_name})", stamp=False)

            if tool_name == "execute_powershell_admin" and self.analyzer:
                try:
                    detected, reason = self.analyzer.analyze(args.get("command", ""))
                    if detected != "low":
                        risk = detected
                except Exception:
                    pass

            display = (
                f"Plan step {step_id}: {title}\n"
                f"Tool: {tool_name}\n"
                f"Args: {json.dumps(args, ensure_ascii=False)[:500]}\n"
                f"Risk: {risk.upper()}"
            )

            if self.policy:
                try:
                    decision = self.policy.evaluate_tool_call(
                        tool_name, args, permissions=self.tools.get_permissions(tool_name)
                    )
                    if not decision.get("allowed", True):
                        msg = decision.get("reason") or "Blocked by policy"
                        self._log(f"[Policy] Denied step {step_id}: {msg}")
                        result = {"content": f"Policy denied: {msg}", "is_error": True}
                        results.append({"step": step_id, "tool": tool_name, "result": result})
                        step_statuses[step_id] = "failed"
                        self._publish_plan(plan, step_statuses, status="failed", current_step=step_id)
                        return {
                            "ok": False,
                            "completed_steps": step_id - 1 if isinstance(step_id, int) else 0,
                            "failed_step": step_id,
                            "message": f"Step {step_id} blocked by policy: {msg}",
                            "results": results,
                        }
                    if decision.get("risk_hint") in ("medium", "high"):
                        risk = decision["risk_hint"]
                except Exception as e:
                    msg = f"Policy evaluation failed; step blocked for safety: {e}"
                    self._log(f"[Policy] {msg}")
                    result = {"content": msg, "is_error": True}
                    results.append({"step": step_id, "tool": tool_name, "result": result})
                    step_statuses[step_id] = "failed"
                    self._publish_plan(plan, step_statuses, status="failed", current_step=step_id)
                    return {
                        "ok": False,
                        "completed_steps": step_id - 1 if isinstance(step_id, int) else 0,
                        "failed_step": step_id,
                        "message": msg,
                        "results": results,
                    }

            result = self._run_with_gate(display, tool_name, args, risk)
            results.append({"step": step_id, "tool": tool_name, "result": result})

            if result.get("is_error"):
                msg = f"Step {step_id} failed: {result.get('content')}"
                self._log(f"[Plan] {msg}")
                step_statuses[step_id] = "failed"
                self._publish_plan(plan, step_statuses, status="failed", current_step=step_id)
                return {
                    "ok": False,
                    "completed_steps": step_id - 1 if isinstance(step_id, int) else 0,
                    "failed_step": step_id,
                    "message": msg,
                    "results": results,
                }

            # Memory learn
            if self.memory and not result.get("is_error"):
                try:
                    self.memory.observe_tool_success(
                        tool_name, args, result.get("content") or ""
                    )
                except Exception as e:
                    self._log(f"[Memory] observe skipped: {e}")

            self.on_status(f"Verifying step {step_id}...", "#F39C12")
            verification = self.verifier.verify(success_check, result)
            self._log(
                f"[Verify] Step {step_id}: "
                f"{'OK' if verification['ok'] else 'FAIL'} — {verification['message']}"
            )

            if not verification["ok"]:
                msg = f"Step {step_id} verification failed: {verification['message']}"
                step_statuses[step_id] = "failed"
                self._publish_plan(plan, step_statuses, status="failed", current_step=step_id)
                return {
                    "ok": False,
                    "completed_steps": (step_id - 1) if isinstance(step_id, int) else 0,
                    "failed_step": step_id,
                    "message": msg,
                    "results": results,
                    "verification": verification,
                }

            step_statuses[step_id] = "done"
            self._publish_plan(plan, step_statuses, status="running", current_step=step_id)
            self._log(f"[Plan] Step {step_id} completed successfully.", stamp=False)
            # Suggest trusting newly opened apps (policy learning)
            if tool_name == "open_app" and not result.get("is_error"):
                try:
                    launched = str(result.get("content") or "")
                    if launched.startswith("Launched:"):
                        launched = launched.split("Launched:", 1)[-1].strip()
                    tgt = launched or (args.get("target") or args.get("app_name") or "")
                    if self.policy and tgt:
                        base = str(tgt).replace("\\", "/").split("/")[-1]
                        if not self.policy.is_trusted_scheduled_open(base):
                            self._log(
                                f"[Policy] Opened {base}. "
                                f"Say: trust app {base} — to auto-approve scheduled opens."
                            )
                except Exception:
                    pass

            # Refresh undo flag for mobile after successful mutating step
            try:
                if self.session_sync and self.undo:
                    self.session_sync.set_undo_available(bool(self.undo.list_stack()))
            except Exception:
                pass

        summary = f"Plan completed successfully ({len(steps)} steps)."
        self._log(f"[Planner] {summary}")
        self.on_speak("All steps completed successfully.")
        self.on_status("Ready", "#27AE60")
        self._publish_plan(plan, step_statuses, status="done", current_step=None)
        return {
            "ok": True,
            "completed_steps": len(steps),
            "failed_step": None,
            "message": summary,
            "results": results,
        }

    

    # Explicitly allowlisted low-risk tools skip the security gate (still logged).
    READ_ONLY_AUTO_TOOLS = {
        "web_search_inapp",
        "list_installed_apps",
        "web_search_open",
        "browser_open",
        "read_document",
        "extract_text",
        "forjob_ats_check",
        "forjob_daily_plan",
        "github_list_repos",
        "forjob_apply_checklist",
        "forjob_draft_tracker_row",
        "forjob_score_jd",
    }

    def _can_auto_approve_read_tool(self, tool_name: str, risk: str) -> bool:
        """Auto-approve only allowlisted low-risk tools with non-mutating manifest permissions."""
        if tool_name not in self.READ_ONLY_AUTO_TOOLS or (risk or "low").lower() != "low":
            return False
        permissions = {p.lower() for p in self.tools.get_permissions(tool_name)}
        mutating = {
            "filesystem:write", "filesystem:delete", "process:execute", "process:terminate",
            "system:admin", "system:environment:write", "network:write", "clipboard:write",
            "registry:write", "screen:control", "email:send", "messaging:send",
            "docker:control", "service:control",
        }
        return not bool(permissions & mutating)

    def _run_with_gate(
        self, display: str, tool_name: str, args: dict, risk: str
    ) -> Dict[str, Any]:
        done: queue.Queue = queue.Queue()

        def execute_callback():
            try:
                if tool_name == "write_developer_code" and getattr(self, "undo", None):
                    backup = self.undo.snapshot_before_write(args.get("filepath", ""))
                    if not backup:
                        raise OSError("Undo snapshot was not created; file write blocked")
                    args["_undo_backup"] = backup
                result = self.tools.execute(tool_name, args)
                if (
                    tool_name == "write_developer_code"
                    and getattr(self, "undo", None)
                    and not result.get("is_error")
                ):
                    try:
                        self.undo.record_write(
                            args.get("filepath", ""),
                            args.pop("_undo_backup", None),
                            str(args.get("content", ""))[:200],
                        )
                    except Exception:
                        pass
            except Exception as e:
                result = {"content": str(e), "is_error": True}
            self._log(
                f"[{'Error' if result.get('is_error') else 'Success'}] "
                f"{result.get('content')}"
            )
            done.put(result)

        # Auto-run safe read-only tools (no secrets mutation)
        if self._can_auto_approve_read_tool(tool_name, risk):
            self._log(f"[Gate] Auto-approved read-only tool: {tool_name}")
            execute_callback()
        else:
            self.on_security_gate(display, execute_callback, risk)
            self.on_speak("Action requested. Please review and approve.")

        try:
            return done.get(timeout=300)
        except queue.Empty:
            return {
                "content": "Timed out waiting for approval or execution.",
                "is_error": True,
            }
