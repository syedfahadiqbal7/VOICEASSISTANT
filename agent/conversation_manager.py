# agent/conversation_manager.py
import json
import time
from datetime import datetime
import queue
import re
import threading
from typing import Callable, Optional, List, Dict, Any
from config import SYSTEM_PROMPT
from agent.llm_client import LLMClient
from agent.tool_registry import ToolRegistry
from agent.history_manager import HistoryManager
from security.command_analyzer import CommandAnalyzer
from agent.planner import Planner
from agent.plan_executor import PlanExecutor
from agent.memory_store import MemoryStore
from agent.user_profile import UserProfileStore
from agent.undo_manager import UndoManager
from agent.scheduler import JobScheduler, JobExecutionResult
from agent.scheduled_execution_ledger import ScheduledExecutionLedger
from agent.session_sync import SessionSync
from agent.policy import PolicyEngine
from agent.outcome_compiler import OutcomeCompiler
from agent.skill_packs import SkillPackManager
from datetime import datetime as _dt


class ConversationManager:
    def __init__(
        self,
        llm: LLMClient,
        tool_registry: ToolRegistry,
        history_manager: HistoryManager,
        on_status: Callable[[str, Optional[str]], None],
        on_transcript: Callable[[str], None],
        on_speak: Callable[[str], None],
        on_security_gate: Callable[[str, Callable, str], None],
        on_diff_preview: Callable[[str, str, Callable, Callable], None] | None = None,
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.history = history_manager
        self.analyzer = CommandAnalyzer()

        self.on_status = on_status
        self.on_transcript = on_transcript
        self.on_speak = on_speak
        self.on_security_gate = on_security_gate
        self.on_diff_preview = on_diff_preview

        self.master_queue = queue.Queue()
        self.cancel_requested = False
        # session_messages, queues and cancellation state are shared by desktop,
        # voice, scheduler and authenticated mobile entry points. Serialize turns.
        self._processing_lock = threading.Lock()
        # Phase 1B memory first (for system prompt enrichment)
        self.memory = MemoryStore()
        self.profile = None
        try:
            self.profile = UserProfileStore()
        except Exception as error:
            # Keep the assistant available, but never replace unreadable profile data.
            print(f"[Profile] User profile unavailable; personalization context disabled: {type(error).__name__}")
        system_content = SYSTEM_PROMPT
        context_blocks = []
        mem_ctx = self.memory.context_snippet()
        if mem_ctx:
            context_blocks.append("[Memory]\n" + mem_ctx)
        profile_ctx = self.profile.context_snippet() if self.profile is not None else ""
        if profile_ctx:
            context_blocks.append("[User Preferences]\n" + profile_ctx)
        if context_blocks:
            system_content = SYSTEM_PROMPT + "\n\n" + "\n\n".join(context_blocks)


        self.session_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]

        saved = self.history.load_conversation()
        if saved:
            # Drop any stored system messages to avoid duplicates
            non_system = [m for m in saved if m.get("role") != "system"]
            self.session_messages = [
                {"role": "system", "content": system_content}
            ] + non_system[-30:]

        # Phase 1 — Planner + Plan Executor + Memory
        self.planner = Planner(llm)
        self.plan_executor = PlanExecutor(
            tool_registry=tool_registry,
            on_status=on_status,
            on_transcript=on_transcript,
            on_speak=on_speak,
            on_security_gate=on_security_gate,
            analyzer=self.analyzer,
            memory=self.memory,
        )
        self.undo = UndoManager()
        try:
            self.plan_executor.cancel_check = lambda: bool(self.cancel_requested)
        except Exception:
            pass
        self.plan_executor.undo = self.undo
        self.session_sync = SessionSync()
        self.plan_executor.session_sync = self.session_sync
        self.policy = PolicyEngine()  # Phase 3 — local policy
        self.plan_executor.policy = self.policy
        self.tool_registry.set_policy(self.policy)
        self.outcome_compiler = OutcomeCompiler()
        self.skill_packs = SkillPackManager()
        # Scheduled tool side effects are guarded by a separate encrypted
        # execution ledger. If it cannot be loaded, scheduled tools fail closed;
        # ordinary conversation and reminder behavior remain available.
        self.scheduled_execution_ledger = None
        self._scheduled_execution_ledger_error = None
        try:
            self.scheduled_execution_ledger = ScheduledExecutionLedger()
        except Exception as error:
            self._scheduled_execution_ledger_error = str(error)
            print(f"[Scheduler] Durable execution ledger unavailable; scheduled tools blocked: {error}")

        self.scheduler = JobScheduler(on_job=None)
        self.scheduler.on_job = self._on_scheduled_job
        # Reconcile explicit automation consent before the worker can fire overdue jobs.
        try:
            self.sync_profile_automations()
        except Exception as error:
            print(f"[Automations] Profile automation sync deferred: {type(error).__name__}")
        try:
            self.scheduler.start()
        except Exception as e:
            print(f"[Scheduler] start failed: {e}")

    def refresh_profile_context(self) -> None:
        """Refresh the system context after an explicit profile preference change."""
        blocks = []
        mem_ctx = self.memory.context_snippet()
        if mem_ctx:
            blocks.append("[Memory]\n" + mem_ctx)
        profile_ctx = self.profile.context_snippet() if self.profile is not None else ""
        if profile_ctx:
            blocks.append("[User Preferences]\n" + profile_ctx)
        content = SYSTEM_PROMPT + (("\n\n" + "\n\n".join(blocks)) if blocks else "")
        # Replacing the system-message content is a single list-item assignment;
        # do not block the UI thread waiting for a potentially long LLM turn.
        if self.session_messages and self.session_messages[0].get("role") == "system":
            self.session_messages[0]["content"] = content
        else:
            self.session_messages.insert(0, {"role": "system", "content": content})
        try:
            self.sync_profile_automations()
        except Exception as error:
            print(f"[Automations] Profile automation sync failed: {type(error).__name__}")

    def _set_local_automation_enabled(self, setting_key: str, automation_id: str, enabled: bool) -> Dict[str, Any]:
        """Change one explicit local-automation opt-in and reconcile its schedule."""
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        if self.profile is None:
            raise RuntimeError("Encrypted profile storage is unavailable")
        profile = self.profile.get_profile()
        settings = profile["settings"]
        if enabled:
            from agent.automation_catalog import automation_readiness
            readiness = automation_readiness(
                automation_id,
                profile=profile,
                connected_integrations=(),
                registered_handlers={"birthday_reminders", "daily_language_lesson"},
                master_enabled=settings["personal_automations_enabled"],
            )
            if not readiness["can_enable"]:
                raise ValueError("Cannot enable " + automation_id + ": " + "; ".join(readiness["blockers"]))
            if self.scheduler.history is None:
                raise RuntimeError("Scheduler execution history is unavailable; automation remains disabled")
        previous = settings[setting_key]
        self.profile.update_profile({"settings": {setting_key: enabled}})
        try:
            result = self.sync_profile_automations()[automation_id]
        except Exception:
            self.profile.update_profile({"settings": {setting_key: previous}})
            raise
        if enabled and not result.get("active"):
            self.profile.update_profile({"settings": {setting_key: False}})
            raise RuntimeError(result.get("reason") or "Automation schedule could not be activated")
        return result

    def set_birthday_reminders_enabled(self, enabled: bool) -> Dict[str, Any]:
        return self._set_local_automation_enabled("birthday_reminders_enabled", "birthday_reminders", enabled)

    def set_daily_language_lesson_enabled(self, enabled: bool) -> Dict[str, Any]:
        return self._set_local_automation_enabled("daily_language_lesson_enabled", "daily_language_lesson", enabled)

    def _sync_local_calendar_automation(
        self, *, marker: str, label: str, desired: bool, recurrence: Dict[str, Any], blockers: List[str]
    ) -> Dict[str, Any]:
        existing = [job for job in self.scheduler.list_jobs() if job.get("prompt") == marker]
        if not desired or blockers:
            stopping = False
            for job in existing:
                if job.get("status") == "pending" and job.get("execution_waiting"):
                    stopping = self.scheduler.stop_recurring_after_current(job.get("id")) == "stopping" or stopping
                elif job.get("status") == "running":
                    stopping = self.scheduler.stop_recurring_after_current(job.get("id")) == "stopping" or stopping
                elif job.get("status") == "pending":
                    if not self.scheduler.cancel(job.get("id")):
                        return {"active": False, "reason": "An existing schedule could not be cancelled"}
            reason = "; ".join(blockers) if blockers else "Automation is disabled"
            if stopping:
                reason += "; current execution will finish without another occurrence"
            return {"active": False, "stopping_after_current": stopping, "reason": reason}
        for job in existing:
            if job.get("enabled") and job.get("status") in {"pending", "running"}:
                if job.get("repeat") == recurrence:
                    return {"active": True, "job_id": job.get("id"), "status": job.get("status")}
                if job.get("status") == "running":
                    return {"active": False, "reason": "The current execution is running; retry the schedule change after it finishes"}
                if not self.scheduler.cancel(job.get("id")):
                    return {"active": False, "reason": "The previous schedule could not be replaced"}
        job = self.scheduler.add_calendar(marker, recurrence, label=label)
        return {"active": True, "job_id": job["id"], "status": "pending"}

    def sync_profile_automations(self) -> Dict[str, Any]:
        """Reconcile enabled local profile automations before and after profile changes."""
        if self.profile is None:
            unavailable = {"active": False, "reason": "Encrypted profile storage is unavailable"}
            return {"birthday_reminders": unavailable, "daily_language_lesson": unavailable}
        profile = self.profile.get_profile()
        settings = profile["settings"]
        master = bool(settings["personal_automations_enabled"])
        history_ok = self.scheduler.history is not None

        birthday_blockers = []
        if not profile["birthdays"]:
            birthday_blockers.append("add at least one birthday in Profile")
        if not settings["timezone"]:
            birthday_blockers.append("set an IANA timezone in Profile")
        if not history_ok:
            birthday_blockers.append("scheduler execution history is unavailable")
        birthday = self._sync_local_calendar_automation(
            marker="__automation__:birthday_reminders",
            label="Birthday reminders",
            desired=master and settings["birthday_reminders_enabled"],
            recurrence={
                "type": "calendar", "frequency": "daily",
                "local_time": settings["birthday_reminder_time"], "timezone": settings["timezone"],
            },
            blockers=birthday_blockers,
        )

        language_blockers = []
        language = profile["language"]
        if not language["target_language"]:
            language_blockers.append("set a target language in Profile")
        if not settings["timezone"]:
            language_blockers.append("set an IANA timezone in Profile")
        if not history_ok:
            language_blockers.append("scheduler execution history is unavailable")
        lesson = self._sync_local_calendar_automation(
            marker="__automation__:daily_language_lesson",
            label="Daily language lesson",
            desired=master and settings["daily_language_lesson_enabled"],
            recurrence={
                "type": "calendar", "frequency": "daily",
                "local_time": settings["daily_language_lesson_time"], "timezone": settings["timezone"],
            },
            blockers=language_blockers,
        )
        return {"birthday_reminders": birthday, "daily_language_lesson": lesson}

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _log(self, text: str, *, stamp: bool = True):
        """One timestamp per message block (multi-line safe)."""
        text = "" if text is None else str(text)
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


    def _looks_like_outcome(self, text: str) -> bool:
        """Goals that OutcomeCompiler may handle even if planner heuristics skip."""
        t = (text or "").strip().lower()
        if t.startswith(("i need ", "open ", "launch ", "start ")):
            # open/start app style — avoid long file-create sentences
            if " file " not in t and "folder" not in t and len(t) < 80:
                return True
        keys = (
            "project ",
            "start a project",
            "create a project",
            "daily log",
            "add to log",
            "save this idea",
            "save idea",
            "create a note",
            "make a note",
        )
        return any(k in t for k in keys)

    def _offer_trust_app(self, launched: str) -> None:
        """After a successful open, invite user to trust for faster scheduled opens."""
        try:
            name = (launched or "").strip()
            if not name:
                return
            # Prefer exe basename for policy list
            base = name.replace("\\", "/").split("/")[-1]
            if self.policy.is_trusted_scheduled_open(base) or self.policy.is_trusted_scheduled_open(name):
                return
            tip = (
                f"[Policy] Opened {base}. "
                f"Say: trust app {base}  — to auto-approve this for scheduled opens next time."
            )
            self._log(tip)
            self.on_speak(
                f"Opened. You can say trust app {base} to open it faster on a schedule later."
            )
        except Exception:
            pass


    def request_cancel(self, reason: str = "user"):
        """Cancel in-flight work. Idempotent — one log per cancel wave."""
        if getattr(self, "cancel_requested", False):
            return
        self.cancel_requested = True
        try:
            if getattr(self, "plan_executor", None) is not None:
                self.plan_executor.cancel_requested = True
        except Exception:
            pass
        try:
            self.master_queue.put(
                {"content": "Cancelled by user.", "is_error": True, "cancelled": True}
            )
        except Exception:
            pass
        self._log(f"[System] Stop requested ({reason}).")
        try:
            self.on_status("Stopping…", "#E67E22")
        except Exception:
            pass


    def clear_cancel(self):
        self.cancel_requested = False

    def process_user_input(self, user_text: str):
        """Serialize turns so shared conversation state cannot interleave."""
        with self._processing_lock:
            return self._process_user_input_serial(user_text)

    def _process_user_input_serial(self, user_text: str):
        self.clear_cancel()
        self._log(f"You: {user_text}")
        try:
            self.session_sync.set_user_text(user_text)
            self.session_sync.set_jobs(self.scheduler.list_jobs())
            self.session_sync.set_undo_available(bool(self.undo.list_stack()))
        except Exception:
            pass
        self.session_messages.append({"role": "user", "content": user_text})
        self.history.save_conversation(self.session_messages)

        if self._handle_meta_command(user_text):
            return
            return

        # ===== Phase 3: Outcome templates first (any matching goal, no LLM) =====
        try:
            outcome_plan = self.outcome_compiler.compile(user_text)
        except Exception as e:
            outcome_plan = None
            self._log(f"[Outcome] compile skipped: {e}")
        if outcome_plan and outcome_plan.get("steps"):
            # Drop steps whose tools belong to disabled skill packs
            try:
                blocked = set(self.skill_packs.tools_for_disabled_packs())
                if blocked:
                    kept = [s for s in outcome_plan["steps"] if s.get("tool") not in blocked]
                    if not kept:
                        self._log(
                            "[Skill] Matching outcome uses a disabled skill pack. "
                            "Say: list skills / enable skill browser|office|vscode"
                        )
                        outcome_plan = None
                    else:
                        outcome_plan["steps"] = kept
            except Exception:
                pass
        if outcome_plan and outcome_plan.get("steps"):
            self.on_status("Planning steps...", "#8E44AD")
            if self.cancel_requested:
                self._log("[System] Stopped before planning.")
                return
            self._log("[System] Complex goal detected — building plan...")
            self._log("[Outcome] Compiled plan from template — skipped LLM")
            outcome_plan = self._fill_content_from_last_assistant(outcome_plan)
            summary = self.plan_executor.execute_plan(outcome_plan)
            # Offer trust after successful open_app steps
            try:
                for step in outcome_plan.get("steps") or []:
                    if step.get("tool") == "open_app" and summary.get("ok"):
                        tgt = (step.get("arguments") or {}).get("target") or ""
                        # Prefer path from last success log is hard; use target
                        self._offer_trust_app(str(tgt))
            except Exception:
                pass
            self.session_messages.append({"role": "assistant", "content": self._plan_summary_text(summary)})
            self.history.save_conversation(self.session_messages)
            if not summary.get("ok"):
                self._log(f"[Outcome] Stopped: {summary.get('message')}")
            return

        # ===== Phase 1: Plan → Act → Verify for complex goals =====
        if self._is_analysis_only(user_text):
            pass  # fall through to tools-off LLM chat
        elif Planner.should_use_planner(user_text) or self._looks_like_outcome(user_text):
            self.on_status("Planning steps...", "#8E44AD")
            self._log(
                "[System] Complex goal detected — building plan..."
            )
            plan_result = self.planner.create_plan(
                user_goal=user_text,
                tool_schemas=self.tool_registry.get_openai_schemas(),
            )
            if plan_result.get("ok"):
                plan = plan_result["plan"]
                raw = str(plan_result.get("raw") or "")
                if raw.startswith("fast_path"):
                    self._log(f"[Planner] Fast-path ({raw}) — skipped LLM")
                plan = self._fill_content_from_last_assistant(plan)
                summary = self.plan_executor.execute_plan(plan)
                self.session_messages.append({"role": "assistant", "content": self._plan_summary_text(summary)})
                self.history.save_conversation(self.session_messages)
                if not summary.get("ok"):
                    self._log(
                        f"[Planner] Stopped: {summary.get('message')}"
                    )
                return
            else:
                self._log(
                    f"[Planner] Could not build plan ({plan_result.get('error')}). "
                    "Falling back to normal mode..."
                )
        # ===== End Phase 1 branch =====

        repair_attempts = 0
        max_repairs = 1
        while True:
            self.on_status("Agent thinking...", "#9B59B6")
            try:
                # Performance: pure chat questions pe tools mat bhejo (warna Ollama hang/slow)
                use_tools = self._should_enable_tools(user_text)
                tools = self.tool_registry.get_openai_schemas() if use_tools else None
                # Context chhota rakho — last 16 msgs + system
                messages_for_llm = self._messages_for_llm()
                if not use_tools:
                    messages_for_llm = list(messages_for_llm) + [{
                        "role": "system",
                        "content": (
                            "Answer using the conversation context and any prior tool outputs "
                            "(GitHub repos, CV text, ATS results). Do not invent project features. "
                            "Do not propose tool calls, Python code, or fake function calls. Never output code blocks as the answer. Plain professional text only.\n"
                            "This is a conversational question only. "
                            "Do NOT invent shell commands, echo redirects, or claim Dry-Run mode. "
                            "Briefly explain what you can do: files, apps, YouTube, system tools, "
                            "multi-step plans with user approval. Do not pretend a command was run."
                        ),
                    }]
                t0 = time.perf_counter()
                response = self.llm.chat(
                    messages=messages_for_llm,
                    tools=tools,
                    timeout=90,
                )
                dt = time.perf_counter() - t0
                if self.cancel_requested:
                    self._log("[System] Stopped before handling LLM response.")
                    self.on_status("Ready", "#27AE60")
                    return
                self._log(f"[Timing] LLM response in {dt:.1f}s (tools={'on' if tools else 'off'})")
                response_message = response.choices[0].message
                content_text = response_message.content or ""
                self.session_messages.append(self.message_to_dict(response_message))
                self.history.save_conversation(self.session_messages)

                active_tool_call = None
                if response_message.tool_calls:
                    active_tool_call = (
                        response_message.tool_calls[0]
                        if isinstance(response_message.tool_calls, list)
                        else response_message.tool_calls
                    )

                if active_tool_call:
                    self._handle_tool_call(active_tool_call)
                    result_event = self.master_queue.get()
                    self.session_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": active_tool_call.id,
                            "name": active_tool_call.function.name,
                            "content": result_event["content"],
                        }
                    )
                    self.history.save_conversation(self.session_messages)
                    if result_event.get("is_error"):
                        if repair_attempts < max_repairs:
                            repair_attempts += 1
                            self._log(
                                f"[Repair {repair_attempts}/{max_repairs}] Tool failed — asking model to fix arguments."
                            )
                            self.session_messages.append({
                                "role": "system",
                                "content": (
                                    "The previous tool call FAILED. Error:\n"
                                    f"{result_event.get('content')}\n\n"
                                    "Call the SAME tool again with CORRECTED arguments only. "
                                    "Do not explain. Use a proper tool call. "
                                    "Required fields must not be missing "
                                    "(e.g. write_developer_code needs filepath AND content)."
                                ),
                            })
                            continue
                        self._log("[Repair] Max attempts reached — stopping.")
                        self.on_speak("I could not complete that action after a repair attempt.")
                        break
                    break

                if content_text:
                    cleaned = content_text.strip()
                    if cleaned.startswith("```"):
                        lines = cleaned.split("\n")
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].strip() == "```":
                            lines = lines[:-1]
                        cleaned = "\n".join(lines).strip()

                    if (
                        cleaned.startswith("{")
                        and "name" in cleaned
                        and "arguments" in cleaned
                    ):
                        try:
                            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
                            if match:
                                cleaned = match.group(0)
                            tool_data = json.loads(cleaned)
                            func_name = tool_data.get("name")
                            arguments = tool_data.get("arguments", {})
                            if func_name and isinstance(arguments, dict):

                                class FakeFunction:
                                    def __init__(self, name, arguments):
                                        self.name = name
                                        self.arguments = json.dumps(arguments)

                                class FakeToolCall:
                                    def __init__(self, name, arguments):
                                        self.id = "fallback_tool_call"
                                        self.function = FakeFunction(name, arguments)

                                fake_call = FakeToolCall(func_name, arguments)
                                self._log(
                                    f"[System] Detected tool call from text: {func_name}"
                                )
                                self._handle_tool_call(fake_call)
                                result_event = self.master_queue.get()
                                self.session_messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": "fallback_tool_call",
                                        "name": func_name,
                                        "content": result_event["content"],
                                    }
                                )
                                self.history.save_conversation(self.session_messages)
                                if result_event.get("is_error"):
                                    if repair_attempts < max_repairs:
                                        repair_attempts += 1
                                        self._log(
                                            f"[Repair {repair_attempts}/{max_repairs}] Tool failed — asking model to fix arguments."
                                        )
                                        self.session_messages.append({
                                            "role": "system",
                                            "content": (
                                                "The previous tool call FAILED. Error:\n"
                                                f"{result_event.get('content')}\n\n"
                                                "Call the SAME tool again with CORRECTED arguments only. "
                                                "Do not explain. Use a proper tool call."
                                            ),
                                        })
                                        continue
                                    self._log("[Repair] Max attempts reached — stopping.")
                                    break
                                break
                        except Exception as e:
                            self._log(
                                f"[System] Could not parse tool JSON: {e}"
                            )
                            self._log(f"AI: {content_text}")
                            self.on_speak(content_text)
                            break
                    else:
                        self._log(f"AI: {content_text}")
                        self.on_speak(content_text)
                    break
            except Exception as e:
                self._log(f"[Ollama Error] {e}")
                break

    def _record_write_or_rollback(
        self, filepath: str, backup: str, content_preview: str
    ) -> Optional[dict]:
        """Persist undo metadata or restore the pre-write file immediately."""
        try:
            self.undo.record_write(filepath, backup, content_preview)
            return None
        except Exception as error:
            try:
                rollback = self.undo.rollback_unrecorded_write(filepath, backup)
                return {
                    "content": (
                        "File write was rolled back because the undo entry could not "
                        f"be saved: {error}. {rollback.get('message', '')}"
                    ),
                    "is_error": True,
                }
            except Exception as rollback_error:
                return {
                    "content": (
                        "CRITICAL: file write completed but its undo entry could not "
                        f"be saved ({error}) and automatic rollback failed "
                        f"({rollback_error}). Inspect the target file and recovery "
                        "snapshot before continuing."
                    ),
                    "is_error": True,
                    "rollback_failed": True,
                }

    def _handle_tool_call(self, tool_call):
        func_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        display_text = self._build_display_text(func_name, args)
        risk_level = "low"

        # Phase 3 policy pre-check (deny / path rules)
        try:
            decision = self.policy.evaluate_tool_call(
                func_name, args, permissions=self.tool_registry.get_permissions(func_name)
            )
            if not decision.get("allowed", True):
                msg = decision.get("reason") or "Blocked by policy"
                self._log(f"[Policy] Denied {func_name}: {msg}")
                self.on_speak("That action is blocked by policy.")
                self.master_queue.put({"content": f"Policy denied: {msg}", "is_error": True})
                return
            if decision.get("risk_hint") in ("medium", "high"):
                risk_level = decision["risk_hint"]
        except Exception as e:
            msg = f"Policy evaluation failed; action blocked for safety: {e}"
            self._log(f"[Policy] {msg}")
            self.on_speak("Action blocked because the security policy could not be evaluated.")
            self.master_queue.put({"content": msg, "is_error": True})
            return

        if func_name == "execute_powershell_admin":
            risk_level, reason = self.analyzer.analyze(args.get("command", ""))
            if risk_level != "low":
                display_text += (
                    f"\n\n⚠️ RISK LEVEL: {risk_level.upper()}\n{reason}"
                )

        if func_name == "write_developer_code":
            filepath = args.get("filepath", "")
            content = args.get("content", "")
            display_text = (
                f"Action: Create / Write File\n"
                f"Path: {filepath}\n"
                f"Content Preview:\n{content[:300]}"
                f"{'...' if len(content) > 300 else ''}"
            )

            def execute_callback():
                filepath = args.get("filepath", "")
                try:
                    backup = self.undo.snapshot_before_write(filepath)
                    if not backup:
                        raise OSError("Undo snapshot was not created; file write blocked")
                except Exception as e:
                    result = {"content": f"File write blocked because undo protection failed: {e}", "is_error": True}
                    self._log(f"[Undo] {result['content']}")
                    self.master_queue.put(result)
                    return
                result = self.tool_registry.execute(func_name, args)
                if not result.get("is_error"):
                    undo_failure = self._record_write_or_rollback(
                        filepath, backup, str(args.get("content", ""))[:200]
                    )
                    if undo_failure:
                        result = undo_failure
                self._log(
                    f"[{'Error' if result['is_error'] else 'Success'}] "
                    f"{result['content']}"
                )
                self.history.log_audit(
                    action=func_name,
                    details=display_text,
                    approved=True,
                    result=result["content"][:300],
                )
                self.master_queue.put(result)

            self.on_security_gate(display_text, execute_callback, "low")
            self.on_speak("File write requested. Please review and approve.")
            return

        def execute_callback():
            result = self.tool_registry.execute(func_name, args)
            self._log(
                f"[{'Error' if result['is_error'] else 'Success'}] "
                f"{result['content']}"
            )
            self.history.log_audit(
                action=func_name,
                details=display_text,
                approved=True,
                result=result["content"][:300],
            )
            self.master_queue.put(result)

        self.on_security_gate(display_text, execute_callback, risk_level)
        self.on_speak("System action requested. Please review and approve.")

    def _build_display_text(self, func_name: str, args: dict) -> str:
        if func_name == "write_developer_code":
            return (
                f"Action: Deploy Code File\n"
                f"Path: {args.get('filepath')}\n"
                f"Content Snippet:\n{args.get('content', '')[:120]}..."
            )
        elif func_name == "execute_powershell_admin":
            return (
                f"Action: Run PowerShell Command\n"
                f"Command: {args.get('command')}"
            )
        elif func_name == "install_python_dependencies":
            return (
                f"Action: Python Package Installer (Pip)\n"
                f"Modules to Install: {args.get('modules')}"
            )
        return f"Unknown action: {func_name}"


    def _plan_summary_text(self, summary: dict) -> str:
        """Include tool outputs so follow-up questions can use ATS/CV results."""
        parts = [str((summary or {}).get("message") or "Plan finished.")]
        for r in (summary or {}).get("results") or []:
            if not isinstance(r, dict):
                if r:
                    parts.append(str(r)[:8000])
                continue
            # shape: {step, tool, result: {content, is_error}}
            inner = r.get("result") if isinstance(r.get("result"), dict) else r
            c = None
            if isinstance(inner, dict):
                c = inner.get("content") or inner.get("message")
            if c:
                parts.append(str(c)[:8000])
        return (chr(10)+chr(10)).join(parts)[:12000]



    def _fill_content_from_last_assistant(self, plan: dict) -> dict:
        """Replace __FROM_LAST_ASSISTANT__ placeholders with last AI text."""
        if not plan or not isinstance(plan, dict):
            return plan
        last = ""
        for m in reversed(self.session_messages or []):
            if m.get("role") == "assistant" and (m.get("content") or "").strip():
                last = str(m.get("content") or "").strip()
                break
        if not last:
            return plan
        steps = plan.get("steps") or []
        for step in steps:
            args = step.get("arguments") or {}
            if args.get("content") == "__FROM_LAST_ASSISTANT__":
                args = dict(args)
                args["content"] = last[:50000]
                step["arguments"] = args
            # write_docx / write_pdf with empty content + tool expects text
            tool = step.get("tool")
            if tool in ("write_docx", "write_pdf") and not (args.get("content") or "").strip():
                if not args.get("source_docx"):
                    args = dict(args)
                    args["content"] = last[:50000]
                    step["arguments"] = args
        return plan

    def _is_analysis_only(self, user_text: str) -> bool:
        """True for pure reasoning / context-setting — no tools / no planner."""
        t = (user_text or "").lower().strip()
        if not t:
            return False
        cues = [
            "summarize", "summarise", "list gaps", "what gaps", "gap vs", "gaps vs",
            "how each", "supports solution", "supports the", "map each", "map to cv",
            "cv bullets", "based on the", "from the previous", "from the cv",
            "from the ats", "from the github", "explain how", "in roman urdu",
            "do not invent", "without inventing", "use my existing", "from this conversation",
            "target roles", "my target", "propose a master cv", "write the full master",
            "ats-friendly", "linkedin headline", "linkedin about", "professional summary",
        ]
        if not any(c in t for c in cues):
            return False
        if any(x in t for x in ("create file", "write file", "save to", "create folder", "open http")):
            return False
        return True

    def _should_enable_tools(self, user_text: str) -> bool:
        """Analysis follow-ups stay tools-off; only real actions enable tools."""
        t = (user_text or "").lower().strip()
        if not t:
            return False
        analysis_cues = [
            "list gaps", "what gaps", "gap vs", "gaps vs", "gaps versus",
            "compare", "summarize", "summarise", "in roman urdu",
            "based on the", "from the cv", "from the ats", "from the github",
            "improve bullet", "rewrite", "feedback", "weakness",
            "what did you find", "next action", "next actions",
            "vs solution architect", "how each", "supports solution",
            "cv bullets", "map each", "each project", "each repo",
            "do not invent", "use my existing", "from this conversation",
            "target roles", "my target", "master cv", "ats-friendly",
            "linkedin headline", "linkedin about",
        ]
        if any(c in t for c in analysis_cues):
            return False
        action_cues = [
            "create", "write", "open", "play", "install", "delete", "copy", "move",
            "run", "start", "stop", "execute", "screenshot", "click", "type",
            "search", "download", "kill", "folder", "file", "command", "powershell",
            "docker", "git", "email", "schedule", "read document", "read my",
            "list installed", "list apps", "list skills", "show process",
            "audit my", "ats check", "forjob",
        ]
        if any(c in t for c in action_cues):
            return True
        chat_cues = [
            "what can you", "who are you", "help me with", "how are you",
            "explain", "what is", "why ", "tell me", "thanks", "thank you",
            "hello", "hi ", "hey ",
        ]
        if any(c in t for c in chat_cues):
            return False
        # Default OFF — only explicit action cues enable tools (prevents code-fantasy answers)
        return False

    def _messages_for_llm(self) -> list:
        """Keep prompt small: system + last 16 non-system messages."""
        if not self.session_messages:
            return self.session_messages
        system = [m for m in self.session_messages if m.get("role") == "system"][:1]
        rest = [m for m in self.session_messages if m.get("role") != "system"]
        return system + rest[-16:]


    def _handle_meta_command(self, user_text: str) -> bool:
        """Built-in Phase 2 commands: undo / schedule. Returns True if handled."""
        t = (user_text or "").strip().lower()
        if t in ("stop", "cancel", "stop activity", "cancel activity", "abort"):
            self.request_cancel("chat")
            self._log("[System] Activity stop signal sent.")
            return True

        if t in ("undo", "undo last", "undo last action", "revert last"):
            result = self.undo.undo_last()
            self._log(f"[Undo] {result.get('message')}")
            self.on_speak(result.get("message") or "Undo done.")
            try:
                self.session_sync.set_undo_available(bool(self.undo.list_stack()))
            except Exception:
                pass
            return True
        if t.startswith("schedule every ") and " minutes:" in t:
            try:
                rest = user_text.strip()[len("schedule every "):]
                mins_part, prompt = rest.split("minutes:", 1)
                mins = int(mins_part.strip().split()[0])
                prompt = prompt.strip()
                job = self.scheduler.add_interval(prompt, mins)
                self._log(f"[Scheduler] Job {job['id']} every {mins} min: {prompt}")
                self.on_speak(f"Scheduled. I will run this every {mins} minutes.")
                try:
                    self.session_sync.set_jobs(self.scheduler.list_jobs())
                except Exception:
                    pass
                return True
            except Exception as e:
                self._log(f"[Scheduler] Could not parse interval schedule: {e}")
                return True
        if t.startswith("schedule in ") and " minutes:" in t:
            try:
                from datetime import timedelta
                rest = user_text.strip()[len("schedule in "):]
                mins_part, prompt = rest.split("minutes:", 1)
                mins = int(mins_part.strip().split()[0])
                prompt = prompt.strip()
                run_at = datetime.now() + timedelta(minutes=mins)
                job = self.scheduler.add_once(prompt, run_at)
                self._log(f"[Scheduler] Job {job['id']} at {job['run_at']}: {prompt}")
                self.on_speak(f"Scheduled. I will run this in {mins} minutes.")
                try:
                    self.session_sync.set_jobs(self.scheduler.list_jobs())
                except Exception:
                    pass
                return True
            except Exception as e:
                self._log(f"[Scheduler] Could not parse one-shot schedule: {e}")
                return True
        # Phase 3 plugin marketplace
        if t in ("verify plugins", "list plugin manifests", "plugin verify"):
            try:
                from agent.plugin_marketplace import scan_plugins_dir
                rows = scan_plugins_dir()
                ok_n = sum(1 for r in rows if r.get("ok"))
                skip_n = sum(1 for r in rows if r.get("skipped"))
                bad = [r for r in rows if not r.get("ok") and not r.get("skipped")]
                self._log(
                    f"[Marketplace] plugins: verified={ok_n} legacy(no manifest)={skip_n} blocked={len(bad)}"
                )
                for r in rows:
                    from pathlib import Path as _P
                    bn = _P(r.get("path") or "?").name
                    if r.get("ok"):
                        man = r.get("manifest") or {}
                        self._log(f"  OK  {bn} v{man.get('version','?')} ({man.get('author','')})")
                    elif r.get("skipped"):
                        self._log(f"  --  {bn} (no manifest)")
                    else:
                        self._log(f"  BAD {bn}: {'; '.join(r.get('errors') or [])}")
            except Exception as e:
                self._log(f"[Marketplace] {e}")
            return True
        if t.startswith("install plugin "):
            path = user_text.strip()[len("install plugin "):].strip().strip('"')
            try:
                from agent.plugin_marketplace import install_from_zip
                res = install_from_zip(path)
                if res.get("ok"):
                    self._log("[Marketplace] " + res.get("message", "Installed"))
                else:
                    self._log("[Marketplace] Install failed: " + "; ".join(res.get("errors") or []))
            except Exception as e:
                self._log(f"[Marketplace] {e}")
            return True
        if t in ("generate plugin manifests", "sign plugins"):
            try:
                from agent.plugin_marketplace import generate_manifests_for_plugins_dir
                written = generate_manifests_for_plugins_dir()
                self._log(f"[Marketplace] Wrote {len(written)} manifest(s). Restart to verify on load.")
            except Exception as e:
                self._log(f"[Marketplace] {e}")
            return True

        # Phase 3 LLM failover
        if t in ("llm status", "failover status", "provider status"):
            try:
                self._log(self.llm.provider_status())
            except Exception as e:
                self._log(f"[LLM] status error: {e}")
            return True
        if t in ("enable failover", "failover on"):
            try:
                self.llm.save_failover_cfg({"enabled": True})
                self._log("[LLM] Cloud failover ENABLED. Configure history/llm_failover.json if needed.")
            except Exception as e:
                self._log(f"[LLM] {e}")
            return True
        if t in ("disable failover", "failover off"):
            try:
                self.llm.save_failover_cfg({"enabled": False})
                self._log("[LLM] Cloud failover DISABLED.")
            except Exception as e:
                self._log(f"[LLM] {e}")
            return True

        # Phase 3 skill packs
        if t in ("list skills", "list skill packs", "show skills"):
            self._log(self.skill_packs.summary_text())
            return True
        if t.startswith("enable skill "):
            pid = t[len("enable skill "):].strip()
            if self.skill_packs.set_enabled(pid, True):
                self._log(f"[Skill] Enabled pack: {pid}")
                self.on_speak(f"Enabled {pid} skill pack.")
            else:
                self._log(f"[Skill] Unknown pack: {pid}. Try browser, office, vscode.")
            return True
        if t.startswith("disable skill "):
            pid = t[len("disable skill "):].strip()
            if self.skill_packs.set_enabled(pid, False):
                self._log(f"[Skill] Disabled pack: {pid}")
                self.on_speak(f"Disabled {pid} skill pack.")
            else:
                self._log(f"[Skill] Unknown pack: {pid}.")
            return True

        # Phase 3 policy learning commands
        if t.startswith("trust app "):
            name = user_text.strip()[len("trust app "):].strip().strip('"')
            if not name:
                self._log("[Policy] Usage: trust app <name.exe or path>")
                return True
            lst = list(self.policy.data.get("auto_approve_scheduled_opens") or [])
            if name not in lst and name.lower() not in {x.lower() for x in lst}:
                lst.append(name)
                self.policy.data["auto_approve_scheduled_opens"] = lst
                self.policy.save()
            self._log(f"[Policy] Trusted scheduled open added: {name}")
            self.on_speak(f"Trusted {name} for scheduled opens.")
            return True
        if t.startswith("untrust app "):
            name = user_text.strip()[len("untrust app "):].strip().strip('"')
            lst = list(self.policy.data.get("auto_approve_scheduled_opens") or [])
            lst = [x for x in lst if x.lower() != name.lower()]
            self.policy.data["auto_approve_scheduled_opens"] = lst
            self.policy.save()
            self._log(f"[Policy] Removed trusted open: {name}")
            return True
        if t.startswith("deny tool "):
            name = user_text.strip()[len("deny tool "):].strip()
            lst = list(self.policy.data.get("denied_tools") or [])
            if name and name not in lst:
                lst.append(name)
                self.policy.data["denied_tools"] = lst
                self.policy.save()
            self._log(f"[Policy] Denied tool: {name}")
            return True
        if t.startswith("allow tool "):
            name = user_text.strip()[len("allow tool "):].strip()
            lst = [x for x in (self.policy.data.get("denied_tools") or []) if x.lower() != name.lower()]
            self.policy.data["denied_tools"] = lst
            self.policy.save()
            self._log(f"[Policy] Allowed tool (removed from deny): {name}")
            return True
        if t.startswith("allow write root "):
            name = user_text.strip()[len("allow write root "):].strip()
            lst = list(self.policy.data.get("allowed_write_roots") or [])
            if name and name not in lst:
                lst.append(name)
                self.policy.data["allowed_write_roots"] = lst
                self.policy.save()
            self._log(f"[Policy] Allowed write root: {name}")
            return True
        if t.startswith("deny write extension "):
            name = user_text.strip()[len("deny write extension "):].strip()
            if name and not name.startswith("."):
                name = "." + name
            lst = list(self.policy.data.get("deny_write_extensions") or [])
            if name and name not in lst:
                lst.append(name)
                self.policy.data["deny_write_extensions"] = lst
                self.policy.save()
            self._log(f"[Policy] Denied write extension: {name}")
            return True

        if t in ("list schedules", "list jobs", "show schedules"):

            jobs = self.scheduler.list_jobs()
            if not jobs:
                self._log("[Scheduler] No jobs.")
            else:
                for j in jobs:
                    self._log(
                        f"[Scheduler] {j.get('id')} enabled={j.get('enabled')} "
                        f"at={j.get('run_at')} :: {j.get('label')}"
                    )
            try:
                self.session_sync.set_jobs(jobs)
            except Exception:
                pass
            return True
        return False

    def _run_daily_language_lesson(self) -> bool:
        """Generate and deliver a short lesson using the configured local Ollama client only."""
        try:
            if self.profile is None:
                raise RuntimeError("Encrypted profile storage is unavailable")
            from agent.language_lesson import build_language_lesson_messages
            messages = build_language_lesson_messages(self.profile.get_profile())
            local_client = getattr(self.llm, "client", None)
            if local_client is None:
                raise RuntimeError("Local LLM client is unavailable")
            # Deliberately call the local OpenAI-compatible client directly. Do not use
            # LLMClient.chat(), which may apply optional cloud failover.
            response = local_client.chat.completions.create(
                model=self.llm.model, messages=messages, timeout=90
            )
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("Local model returned an empty lesson")
            lesson = content.strip()[:8000]
            self.on_transcript("[Daily Language Lesson]\n" + lesson)
            self.on_status("Daily language lesson is ready in the transcript.", "#1ABC9C")
            try:
                self.on_speak("Your daily language lesson is ready. Please see the transcript.")
            except Exception:
                pass
            return True
        except Exception as error:
            self._log(f"[Automations] Daily language lesson failed: {type(error).__name__}")
            try:
                self.on_status("Daily language lesson could not be generated; it will be retried.", "#C0392B")
            except Exception:
                pass
            return False

    def _on_scheduled_job(self, job: dict):        """Run scheduled work without handing tool selection to the LLM.

        Side-effecting fast-path tools use the persisted execution_id ledger.
        Reminders remain at-least-once notifications.
        """
        prompt = (job.get("prompt") or "").strip()
        jid = job.get("id")
        execution_id = job.get("execution_id")
        self._log(f"[Scheduler] Running job {jid}: {prompt}")
        if not prompt:
            return False
        if prompt == "__automation__:daily_language_lesson":
            return self._run_daily_language_lesson()
        if prompt == "__automation__:birthday_reminders":
            try:
                from agent.birthday_reminders import due_birthday_reminders
                profile = self.profile.get_profile() if self.profile is not None else {}
                reminders = due_birthday_reminders(profile)
                if not reminders:
                    self.on_status("Birthday reminders: no reminders due today.", "#7F8C8D")
                    return True
                message = "Birthday reminders: " + " ".join(reminders)
                self.on_transcript(message)
                self.on_status(message[:160], "#1ABC9C")
                try:
                    self.on_speak(message)
                except Exception:
                    pass
                return True
            except Exception as error:
                self._log(f"[Automations] Birthday reminder failed: {type(error).__name__}")
                return False
        try:
            fast_result = self._execute_scheduled_prompt_fast(prompt, execution_id)
            if fast_result is not None:
                return fast_result

            self._log(f"[Scheduler] Reminder (no tools): {prompt}")
            spoken = False
            try:
                self.on_speak(prompt)
                spoken = True
            except Exception:
                try:
                    self.on_speak("Scheduled reminder.")
                    spoken = True
                except Exception:
                    pass
            try:
                self.on_status(f"Reminder: {prompt[:80]}", "#1ABC9C")
            except Exception:
                pass
            return spoken
        except Exception as e:
            self._log(f"[Scheduler] Job failed: {e}")
            try:
                self.on_speak("Scheduled task failed.")
            except Exception:
                pass
            return False

    def _is_trusted_scheduled_open(self, target: str) -> bool:
        """Policy-driven trusted scheduled opens (file writes never auto-approved)."""
        try:
            return self.policy.is_trusted_scheduled_open(target)
        except Exception:
            t = (target or "").strip().lower().replace("\\", "/")
            base = t.split("/")[-1]
            return base in {"notepad", "notepad.exe", "calc", "calc.exe", "calculator"}

    def _execute_scheduled_prompt_fast(self, prompt: str, execution_id: str | None = None):
        """Execute known scheduled actions; None means the prompt was not handled."""
        t = prompt.strip()
        low = t.lower()

        m = re.search(
            r"^open\s+(notepad|calculator|calc|chrome|edge|explorer|cmd|powershell)\s*$",
            low,
        )
        if m:
            app = m.group(1)
            target = {"calculator": "calc.exe", "calc": "calc.exe"}.get(app, app)
            trusted = self._is_trusted_scheduled_open(target)
            self._log(
                f"[Scheduler] Fast-path open_app → {target}"
                + (" (auto-approve)" if trusted else " (needs approval)")
            )
            return self._run_tool_direct(
                "open_app",
                {"target": target},
                display=f"Scheduled: Open {target}",
                auto_approve=trusted,
                execution_id=execution_id,
            )

        # File writes always require the security gate — never auto-approve.
        try:
            fast_plan = self.planner._try_fast_file_plan(prompt)
        except Exception:
            fast_plan = None
        if fast_plan and fast_plan.get("steps"):
            step = fast_plan["steps"][0]
            if step.get("tool") == "write_developer_code":
                args = step.get("arguments") or {}
                path = args.get("filepath", "")
                content = args.get("content", "OK")
                self._log(f"[Scheduler] Fast-path write → {path} (needs approval)")
                return self._run_tool_direct(
                    "write_developer_code",
                    {"filepath": path, "content": content},
                    display=f"Scheduled: Create {path.split('/')[-1]}",
                    auto_approve=False,
                    execution_id=execution_id,
                )
        return None

    def _run_tool_direct(
        self,
        func_name: str,
        args: dict,
        display: str,
        auto_approve: bool = False,
        execution_id: str | None = None,
    ):
        """Run an approved scheduled tool under a durable execution ID guard."""
        done = queue.Queue()
        ledger = self.scheduled_execution_ledger
        if not execution_id:
            return JobExecutionResult(
                success=False, retryable=False,
                message="Scheduled tool blocked: execution_id is missing",
            )
        if ledger is None:
            return JobExecutionResult(
                success=False, retryable=False,
                message="Scheduled tool blocked: durable execution ledger is unavailable",
            )

        try:
            decision = self.policy.evaluate_tool_call(
                func_name, args, permissions=self.tool_registry.get_permissions(func_name)
            )
            if not decision.get("allowed", False):
                self._log(f"[Policy] Scheduled action denied: {decision.get('reason', 'blocked')}")
                return False
            auto_approve = bool(
                auto_approve
                and func_name == "open_app"
                and self.policy.role != "read_only"
                and self.policy.is_trusted_scheduled_open(str(args.get("target") or ""))
            )
        except Exception as error:
            self._log(f"[Policy] Scheduled action blocked because policy evaluation failed: {error}")
            return False

        callback_lock = threading.Lock()
        callback_state = {"started": False, "expired": False}

        def execute_callback():
            # A delayed approval callback must not start after the scheduler
            # has timed out. A duplicate callback in this same request is ignored.
            with callback_lock:
                if callback_state["expired"] or callback_state["started"]:
                    return
                callback_state["started"] = True

            backup = None
            claimed = False
            try:
                if func_name == "write_developer_code":
                    try:
                        backup = self.undo.snapshot_before_write(args.get("filepath", ""))
                        if not backup:
                            raise OSError("Undo snapshot returned no backup or new-file marker")
                    except Exception as error:
                        result = {
                            "content": f"File write blocked because undo protection failed: {error}",
                            "is_error": True,
                        }
                        self._log(f"[Undo] {result['content']}")
                        done.put(result)
                        return

                claim = ledger.claim(execution_id, func_name)
                if claim == "succeeded":
                    done.put({
                        "content": "This scheduled execution was already completed; duplicate invocation skipped.",
                        "is_error": False,
                        "_already_completed": True,
                    })
                    return
                if claim == "in_progress":
                    done.put({
                        "content": "The same scheduled execution is still active; duplicate invocation skipped.",
                        "is_error": True,
                        "_defer": True,
                    })
                    return
                if claim == "uncertain":
                    done.put({
                        "content": "The previous process may have performed this action before stopping. Automatic replay is blocked; inspect the outcome and create a new scheduled occurrence if appropriate.",
                        "is_error": True,
                        "_terminal": True,
                    })
                    return

                claimed = True
                result = self.tool_registry.execute(func_name, args)
                if not isinstance(result, dict):
                    result = {"content": "Tool returned an invalid result", "is_error": True}

                if func_name == "write_developer_code" and not result.get("is_error"):
                    undo_failure = self._record_write_or_rollback(
                        args.get("filepath", ""),
                        backup,
                        str(args.get("content", ""))[:200],
                    )
                    if undo_failure:
                        result = undo_failure

                if result.get("is_error"):
                    detail = str(result.get("content") or "Tool reported an error")
                    ledger.complete(execution_id, success=False, detail=detail)
                    claimed = False
                    result["_terminal"] = True
                else:
                    ledger.complete(execution_id, success=True)
                    claimed = False
                self._log(
                    f"[{'Error' if result.get('is_error') else 'Success'}] "
                    f"{result.get('content')}"
                )
                done.put(result)
            except Exception as error:
                terminal = claimed
                if claimed:
                    try:
                        ledger.complete(execution_id, success=False, detail=str(error))
                    except Exception as ledger_error:
                        self._log(f"[Scheduler] Execution outcome checkpoint failed: {ledger_error}")
                done.put({
                    "content": f"{type(error).__name__}: {error}",
                    "is_error": True,
                    "_terminal": terminal,
                })

        try:
            # Hard safety: never auto-approve file writes even if caller asks.
            if auto_approve and func_name == "write_developer_code":
                auto_approve = False

            if auto_approve:
                self._log(f"[Scheduler] Auto-approved trusted action: {display}")
                try:
                    self.on_status(f"Auto-approved: {display}", "#1ABC9C")
                except Exception:
                    pass
                execute_callback()
            else:
                self.on_security_gate(display, execute_callback, "low")
                self.on_speak("Scheduled action needs your approval.")

            result = done.get(timeout=300)
            if result.get("_defer"):
                return JobExecutionResult(
                    success=False, retryable=True, defer=True,
                    message=str(result.get("content") or "Scheduled execution is still active"),
                )
            if result.get("_terminal"):
                return JobExecutionResult(
                    success=False, retryable=False,
                    message=str(result.get("content") or "Scheduled execution outcome is uncertain"),
                )
            ok = not result.get("is_error")
            if ok:
                try:
                    self.on_speak("Scheduled task completed.")
                except Exception:
                    pass
            return ok
        except queue.Empty:
            with callback_lock:
                if callback_state["started"]:
                    # The callback may still be executing. Keep the same key and
                    # let the scheduler poll the durable ledger rather than replay.
                    return JobExecutionResult(
                        success=False, retryable=True, defer=True,
                        message="Scheduled action is still running; waiting for its durable outcome",
                    )
                callback_state["expired"] = True
            self._log("[Scheduler] Approval timed out before execution; delayed approval callbacks are now disabled.")
            return False

    def clear_history(self):
        system_content = SYSTEM_PROMPT
        try:
            mem_ctx = self.memory.context_snippet() if getattr(self, "memory", None) else ""
            if mem_ctx:
                system_content = SYSTEM_PROMPT + "\n\n[Memory]\n" + mem_ctx
        except Exception:
            pass
        self.session_messages = [{"role": "system", "content": system_content}]
        self.history.clear_conversation()

    def message_to_dict(self, message) -> dict:
        """Convert ChatCompletionMessage or dict to a plain serializable dict."""
        if isinstance(message, dict):
            return message
        data = {
            "role": message.role,
            "content": message.content,
        }
        if hasattr(message, "tool_calls") and message.tool_calls:
            data["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        if hasattr(message, "tool_call_id") and message.tool_call_id:
            data["tool_call_id"] = message.tool_call_id
        if hasattr(message, "name") and message.name:
            data["name"] = message.name
        return data
