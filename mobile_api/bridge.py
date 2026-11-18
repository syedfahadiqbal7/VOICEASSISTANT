# mobile_api/bridge.py
"""
Bridge between ConversationManager (desktop) and Mobile clients.
Desktop callbacks ko intercept karke mobile ko bhi events bhejta hai.
Security gate ke liye mobile se approval wait karta hai.
"""

import asyncio
import logging
import threading
import uuid
from typing import Callable, Optional, Dict, Any
from mobile_api.connection_manager import manager

logger = logging.getLogger("voiceassistant.mobile_bridge")


class MobileBridge:
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_approvals: Dict[str, threading.Event] = {}
        self._approval_results: Dict[str, bool] = {}
        self._approval_clients: Dict[str, str] = {}
        self._approval_lock = threading.RLock()
        self._active_approval_id: Optional[str] = None
        self._original_on_status = None
        self._original_on_transcript = None
        self._original_on_speak = None
        self._original_on_security_gate = None
        self._conversation = None
        self._clear_gate_callback = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_clear_gate_callback(self, callback):
        """Desktop UI se gate clear karne ka function set karo."""
        self._clear_gate_callback = callback

    def _run_async(self, coro):
        """Thread-safe tarike se async function call karo."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            try:
                asyncio.run(coro)
            except Exception as e:
                logger.exception("Mobile bridge async dispatch failed", extra={"event": "mobile_bridge.dispatch_failed"})

    def attach(self, conversation):
        """
        ConversationManager ke callbacks ko wrap karo
        taake desktop + mobile dono ko events milein.
        """
        self._conversation = conversation

        self._original_on_status = conversation.on_status
        self._original_on_transcript = conversation.on_transcript
        self._original_on_speak = conversation.on_speak
        self._original_on_security_gate = conversation.on_security_gate

        conversation.on_status = self._wrapped_status
        conversation.on_transcript = self._wrapped_transcript
        conversation.on_speak = self._wrapped_speak
        conversation.on_security_gate = self._wrapped_security_gate

        # Session sync → mobile broadcast
        try:
            if hasattr(conversation, "session_sync"):
                conversation.session_sync.attach_bridge(self)
                try:
                    conversation.session_sync.set_jobs(conversation.scheduler.list_jobs())
                except Exception:
                    pass
        except Exception as e:
            logger.exception("Mobile session synchronization attach failed", extra={"event": "mobile_bridge.session_sync_attach_failed"})

        logger.info("Mobile bridge attached to ConversationManager", extra={"event": "mobile_bridge.attached"})

    def _wrapped_status(self, message: str, color: Optional[str] = None):
        if self._original_on_status:
            try:
                self._original_on_status(message, color)
            except Exception as e:
                logger.exception("Mobile status callback failed", extra={"event": "mobile_bridge.status_callback_failed"})
        self._run_async(manager.broadcast({
            "type": "status",
            "message": message,
            "color": color or "#3498DB"
        }))
        try:
            if self._conversation and hasattr(self._conversation, "session_sync"):
                self._conversation.session_sync.set_status(message)
        except Exception:
            pass

    def _wrapped_transcript(self, text: str):
        if self._original_on_transcript:
            try:
                self._original_on_transcript(text)
            except Exception as e:
                # UI may not be ready yet (scheduler fire during boot)
                logger.exception("Mobile transcript callback failed", extra={"event": "mobile_bridge.transcript_callback_failed"})

        role = "system"
        out = text
        if text.startswith("You:"):
            role = "user"
            out = text[4:].strip()
        elif text.startswith("AI:"):
            role = "ai"
            out = text[3:].strip()

        self._run_async(manager.broadcast({
            "type": "transcript",
            "role": role,
            "text": out
        }))

    def _wrapped_speak(self, text: str):
        if self._original_on_speak:
            try:
                self._original_on_speak(text)
            except Exception as e:
                logger.exception("Mobile speech callback failed", extra={"event": "mobile_bridge.speech_callback_failed"})
        self._run_async(manager.broadcast({
            "type": "speak",
            "text": text
        }))

    def _wrapped_security_gate(self, display_text: str, callback: Callable, risk_level: str = "low"):
        """
        Agar koi mobile connected hai to pehle mobile se approval lo.
        Warna normal desktop security gate dikhao.
        """
        if not manager.has_connections():
            if self._original_on_security_gate:
                self._original_on_security_gate(display_text, callback, risk_level)
            return

        request_id = str(uuid.uuid4())
        event = threading.Event()
        with self._approval_lock:
            self._pending_approvals[request_id] = event
            self._approval_results[request_id] = False
            self._active_approval_id = request_id
        # Only one authenticated mobile client receives this approval challenge.
        target, target_id = manager.first_connection(allowed_roles={"admin", "approver"})
        with self._approval_lock:
            self._approval_clients[request_id] = target_id or ""
        if target is not None:
            self._run_async(manager.send_personal(target, {
                "type": "security_gate",
                "request_id": request_id,
                "display_text": display_text,
                "risk_level": risk_level
            }))
        elif self._original_on_security_gate is None:
            self.resolve_approval(request_id, False)

        if self._original_on_security_gate:
            def desktop_approve():
                self.resolve_approval(request_id, True)

            self._original_on_security_gate(display_text, desktop_approve, risk_level)

        event.wait(timeout=300)

        with self._approval_lock:
            result = self._approval_results.pop(request_id, False)
            self._pending_approvals.pop(request_id, None)
            self._approval_clients.pop(request_id, None)
            if self._active_approval_id == request_id:
                self._active_approval_id = None

        if result:
            threading.Thread(target=callback, daemon=True).start()
        else:
            if self._conversation:
                try:
                    self._conversation.master_queue.put({
                        "content": "Action rejected by user (mobile/desktop).",
                        "is_error": False
                    })
                except Exception:
                    pass

    def resolve_approval(self, request_id: str, approved: bool, client_id: Optional[str] = None):
        """Resolve only from the challenged mobile client or the local desktop UI."""
        if not isinstance(approved, bool):
            return False
        with self._approval_lock:
            event = self._pending_approvals.get(request_id)
            if event is None:
                return False
            expected_client = self._approval_clients.get(request_id, "")
            if client_id is not None and (not expected_client or client_id != expected_client):
                logger.warning("Rejected approval from non-authorized mobile client for request %s", request_id, extra={"event": "mobile_bridge.unauthorized_approval"})
                return False
            self._approval_results[request_id] = bool(approved)
            event.set()
        logger.info("Mobile approval resolved for request %s (approved=%s)", request_id, bool(approved), extra={"event": "mobile_bridge.approval_resolved"})

        # A desktop-side decision must also dismiss the matching mobile prompt.
        # Mobile-originated decisions receive their acknowledgement from the API handler.
        if client_id is None:
            self._run_async(manager.broadcast({
                "type": "approval_ack",
                "request_id": request_id,
                "accepted": True,
            }))

        if self._clear_gate_callback:
            try:
                self._clear_gate_callback()
            except Exception as e:
                logger.exception("Could not clear desktop approval gate", extra={"event": "mobile_bridge.clear_gate_failed"})
        return True


    def client_disconnected(self, client_id: str) -> None:
        """Do not leave approval waits orphaned when a challenged client disconnects.

        The desktop approval dialog remains a valid fallback. If no desktop gate
        exists, the disconnected mobile challenge is denied immediately.
        """
        if self._original_on_security_gate is not None:
            return
        with self._approval_lock:
            for request_id, expected in list(self._approval_clients.items()):
                if expected == client_id:
                    self._approval_results[request_id] = False
                    event = self._pending_approvals.get(request_id)
                    if event is not None:
                        event.set()

    def deny_desktop_approval(self) -> bool:
        """Allow the desktop Deny/Stop controls to resolve a mobile-routed gate."""
        with self._approval_lock:
            request_id = self._active_approval_id
        return self.resolve_approval(request_id, False) if request_id else False

    def process_mobile_message(self, text: str):
        """Mobile se aaya hua user message process karo."""
        if self._conversation:
            threading.Thread(
                target=self._conversation.process_user_input,
                args=(text,),
                daemon=True
            ).start()
        else:
            logger.warning("ConversationManager is not attached yet", extra={"event": "mobile_bridge.not_attached"})

    def get_session_state(self) -> Dict[str, Any]:
        if self._conversation and hasattr(self._conversation, "session_sync"):
            try:
                self._conversation.session_sync.set_jobs(
                    self._conversation.scheduler.list_jobs()
                )
                return self._conversation.session_sync.snapshot()
            except Exception as e:
                return {"error": str(e)}
        return {}

    def list_schedules(self):
        if not self._conversation:
            return []
        try:
            jobs = self._conversation.scheduler.list_jobs()
            self._conversation.session_sync.set_jobs(jobs)
            return jobs
        except Exception:
            return []

    def cancel_schedule(self, job_id: str) -> bool:
        if not self._conversation or not job_id:
            return False
        try:
            ok = self._conversation.scheduler.cancel(job_id)
            self._conversation.session_sync.set_jobs(
                self._conversation.scheduler.list_jobs()
            )
            return ok
        except Exception:
            return False

    def undo_last(self) -> dict:
        """Mobile Undo button → desktop UndoManager."""
        if not self._conversation:
            return {"ok": False, "message": "Desktop agent not attached"}
        try:
            result = self._conversation.undo.undo_last()
            try:
                self._conversation.session_sync.set_undo_available(
                    bool(self._conversation.undo.list_stack())
                )
            except Exception:
                pass
            try:
                msg = result.get("message") or "Undo done."
                self._conversation._log(f"[Undo] {msg}")
                self._conversation.on_speak(msg)
            except Exception:
                pass
            return result if isinstance(result, dict) else {"ok": False, "message": str(result)}
        except Exception as e:
            return {"ok": False, "message": str(e)}


bridge = MobileBridge()
