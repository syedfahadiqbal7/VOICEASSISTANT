# ui/app_window.py
import threading
import time
import queue
import logging
import keyboard  # pip install keyboard
try:
    from ui.notify import notify_background
except Exception:
    try:
        from ui_notify import notify_background
    except Exception:
        notify_background = None

import customtkinter as ctk
from tkinter import messagebox
from config import WINDOW_TITLE, WINDOW_SIZE, AVAILABLE_MODELS, DEFAULT_MODEL
from audio.stt_engine import STTEngine
from audio.tts_engine import TTSEngine
from audio.recorder import AudioRecorder
from agent.llm_client import LLMClient
from agent.tool_registry import ToolRegistry
from agent.conversation_manager import ConversationManager
from agent.history_manager import HistoryManager
from ui.diff_viewer import DiffViewer
from ui.audit_viewer import AuditLogViewer
from ui.help_window import HelpWindow
from mobile_api.bridge import bridge
from mobile_api.server import (
    start_server, stop_server, pairing_coordinator, mobile_devices,
    _create_remote_session_qr, remote_session_store,
)
from ui.scheduler_viewer import open_scheduler_viewer
from ui.undo_viewer import open_undo_viewer
from ui.policy_viewer import open_policy_viewer
from ui.skill_author import open_skill_author
from ui.profile_viewer import open_profile_viewer
from ui.automation_catalog_viewer import open_automation_catalog_viewer
from agent.app_restart import restart_app
from agent.remote_identity import RemoteIdentityStore

logger = logging.getLogger("voiceassistant.ui")


class AgenticVoiceAssistant(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")
        self.title(WINDOW_TITLE)
        self.geometry(WINDOW_SIZE)
        self.minsize(960, 640)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._closing = False
        self._ui_queue = queue.Queue()
        self._ui_poll_id = None
        self._undo_in_progress = False
        self._undo_completion_callbacks = []
        self._models_refreshing = False
        self._stt_ready = False
        self._tts_ready = False
        self._max_transcript_lines = 8000
        self.configure(fg_color=("#E8EEF5", "#0F1419"))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.is_running = False
        self.pending_callback = None
        self.dry_run_enabled = False

        self.stt = STTEngine()
        self.tts = TTSEngine()
        self.recorder = AudioRecorder()
        self.llm = LLMClient()
        self.tool_registry = ToolRegistry(dry_run=False)
        self.history_manager = HistoryManager()
        self.conversation = ConversationManager(
            llm=self.llm,
            tool_registry=self.tool_registry,
            history_manager=self.history_manager,
            on_status=self.update_status,
            on_transcript=self.update_transcript,
            on_speak=self.speak,
            on_security_gate=self.show_security_gate,
            on_diff_preview=self.show_diff_preview,
        )

        bridge.attach(self.conversation)
        try:
            self.conversation.session_sync.attach_bridge(bridge)
        except Exception as e:
            logger.exception("Session synchronization bridge attach failed", extra={"event": "ui.session_sync_attach_failed"})
        bridge.set_clear_gate_callback(self._clear_security_gate_threadsafe)
        import os
        self.mobile_host = os.environ.get("VOICEASSISTANT_MOBILE_HOST", "127.0.0.1").strip() or "127.0.0.1"

        self._build_ui()
        self._ui_poll_id = self.after(25, self._drain_ui_queue)
        self._setup_hotkeys()
        self._mobile_server_thread = threading.Thread(
            target=self._run_mobile_server, name="VoiceAssistantMobileAPI", daemon=True
        )
        self._mobile_server_thread.start()
        threading.Thread(target=self._initialize_engines, name="VoiceAssistantEngineInit", daemon=True).start()

    def _build_ui(self):
        # Header
        self.header = ctk.CTkFrame(self, fg_color=("white", "#15202B"), corner_radius=0)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            self.header,
            text="Loading Core Systems...",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#1A5276", "#5DADE2"),
        )
        self.status_label.grid(row=0, column=0, padx=20, pady=(14, 8), sticky="w")

        # Horizontal toolbar
        self.top_controls = ctk.CTkScrollableFrame(
            self, orientation="horizontal", height=50,
            fg_color=("white", "#15202B"), corner_radius=0,
        )
        self.top_controls.grid(row=1, column=0, sticky="ew")

        ctk.CTkLabel(self.top_controls, text="Model", text_color=("gray40", "gray70")).pack(
            side="left", padx=(16, 6)
        )
        self.model_var = ctk.StringVar(value=DEFAULT_MODEL)
        self.model_menu = ctk.CTkOptionMenu(
            self.top_controls, values=AVAILABLE_MODELS, variable=self.model_var,
            command=self._on_model_change, width=180, height=32,
        )
        self.model_menu.pack(side="left", padx=(0, 8))
        self.refresh_models_btn = ctk.CTkButton(
            self.top_controls, text="↻", width=36, height=32, command=self._refresh_models
        )
        self.refresh_models_btn.pack(side="left", padx=(0, 10))

        self.dry_run_var = ctk.BooleanVar(value=False)
        self.dry_run_switch = ctk.CTkSwitch(
            self.top_controls, text="Dry-Run", variable=self.dry_run_var,
            command=self._toggle_dry_run,
        )
        self.dry_run_switch.pack(side="left", padx=8)

        def _tb(text, color, hover, cmd, width=90):
            b = ctk.CTkButton(
                self.top_controls, text=text, width=width, height=32, corner_radius=8,
                fg_color=color, hover_color=hover, command=cmd,
            )
            b.pack(side="left", padx=3)
            return b

        self.audit_btn = _tb("Audit Log", "#6C3483", "#5B2C6F", self._show_audit_log, 90)
        self.export_btn = _tb("Export", "#2471A3", "#1A5276", self._export_conversation, 80)
        self.help_btn = _tb("Help", "#117A65", "#0E6655", self._show_help, 70)
        self.clear_history_btn = _tb("Clear", "#5D6D7E", "#34495E", self._clear_history, 70)
        self.schedules_btn = _tb("Schedules", "#148F77", "#0E6655", self._show_scheduler, 90)
        self.profile_btn = _tb("Profile", "#2874A6", "#1F618D", self._show_profile, 80)
        self.automations_btn = _tb("Automations", "#7D3C98", "#633974", self._show_automation_catalog, 105)
        self.undo_hist_btn = _tb("Undo History", "#1E8449", "#196F3D", self._show_undo_history, 110)
        self.policy_btn = _tb("Policy", "#6C3483", "#5B2C6F", self._show_policy, 80)
        self.skill_author_btn = _tb("Skill Author", "#CA6F1E", "#A04000", self._show_skill_author, 100)
        self.restart_btn = _tb("Restart", "#922B21", "#7B241C", self._restart_app, 80)
        self.stop_speak_btn = _tb("Stop Speaking", "#B9770E", "#9A7D0A", self._stop_speaking, 110)
        self.stop_activity_btn = _tb("Stop Activity", "#C0392B", "#922B21", self._stop_activity, 110)
        self.mobile_pair_btn = _tb("Pair Mobile", "#117A65", "#0E6655", self._show_mobile_pairing, 100)
        self.mobile_remote_btn = _tb("Remote Mobile", "#1F618D", "#154360", self._show_remote_session_qr, 112)

        # Transcript card
        self.main_card = ctk.CTkFrame(self, fg_color=("white", "#1B2838"), corner_radius=12)
        self.main_card.grid(row=3, column=0, padx=16, pady=(10, 6), sticky="nsew")
        self.main_card.grid_columnconfigure(0, weight=1)
        self.main_card.grid_rowconfigure(0, weight=1)
        self.transcript_area = ctk.CTkTextbox(
            self.main_card, font=ctk.CTkFont(size=13), wrap="word",
            activate_scrollbars=True, fg_color=("gray98", "#121A24"),
            text_color=("#1C2833", "#E8EEF5"), corner_radius=10,
        )
        self.transcript_area.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.transcript_area.insert("0.0", "Initializing Agent Core...\n")
        self.transcript_area.configure(state="disabled")
        try:
            tb = self.transcript_area._textbox
            tb.tag_config("user", foreground="#5DADE2")
            tb.tag_config("assistant", foreground="#ABEBC6")
            tb.tag_config("system", foreground="#95A5A6")
            tb.tag_config("success", foreground="#58D68D")
            tb.tag_config("error", foreground="#F1948A")
            tb.tag_config("meta", foreground="#7F8C8D")
            tb.tag_config("divider", foreground="#34495E")
            tb.tag_config("bold", font=("Segoe UI", 13, "bold"))
            tb.tag_config("italic", font=("Segoe UI", 13, "italic"))
            tb.tag_config("heading", font=("Segoe UI", 14, "bold"), foreground="#F5B041")
            tb.tag_config("bullet", foreground="#AED6F1")
        except Exception:
            pass

        # Action bar
        self.action_bar = ctk.CTkFrame(
            self, fg_color=("#D5F5E3", "#1E3A2F"), corner_radius=10,
            border_width=1, border_color="#27AE60",
        )
        self.action_bar.grid(row=4, column=0, padx=16, pady=(0, 6), sticky="ew")
        self.action_bar.grid_remove()
        self.action_bar.grid_columnconfigure(0, weight=1)
        self.action_bar_label = ctk.CTkLabel(
            self.action_bar, text="", anchor="w", justify="left",
            font=ctk.CTkFont(size=12), text_color=("#145A32", "#ABEBC6"),
        )
        self.action_bar_label.grid(row=0, column=0, padx=(14, 8), pady=10, sticky="ew")
        self.inline_undo_btn = ctk.CTkButton(
            self.action_bar, text="↶ Undo", width=90, height=30, corner_radius=8,
            fg_color="#27AE60", hover_color="#1E8449", command=self._undo_last_action,
        )
        self.inline_undo_btn.grid(row=0, column=1, padx=(0, 12), pady=8)

        # Security gate — high contrast
        self.gate_frame = ctk.CTkFrame(
            self, fg_color=("#FDEDEC", "#2C1810"), corner_radius=12,
            border_width=2, border_color="#E74C3C",
        )
        self.gate_frame.grid(row=5, column=0, padx=16, pady=6, sticky="ew")
        self.gate_frame.grid_remove()
        self.gate_frame.grid_columnconfigure(0, weight=1)
        self.gate_label = ctk.CTkLabel(
            self.gate_frame, text="SECURITY APPROVAL REQUIRED",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#922B21", "#F5B7B1"),
        )
        self.gate_label.grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")
        self.gate_cmd_display = ctk.CTkTextbox(
            self.gate_frame, height=110, wrap="word",
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=("#FFFFFF", "#1A1A1A"),
            text_color=("#1C2833", "#F4F6F7"),
            border_width=1, border_color=("#E74C3C", "#922B21"), corner_radius=8,
        )
        self.gate_cmd_display.grid(row=1, column=0, padx=16, pady=6, sticky="ew")
        self.gate_cmd_display.configure(state="disabled")
        self.btn_frame = ctk.CTkFrame(self.gate_frame, fg_color="transparent")
        self.btn_frame.grid(row=2, column=0, padx=16, pady=(4, 14), sticky="ew")
        self.approve_btn = ctk.CTkButton(
            self.btn_frame, text="✓  Approve & Execute", height=36, corner_radius=8,
            fg_color="#27AE60", hover_color="#1E8449",
            font=ctk.CTkFont(size=13, weight="bold"), command=self.approve_action,
        )
        self.approve_btn.pack(side="left", padx=(0, 10))
        self.deny_btn = ctk.CTkButton(
            self.btn_frame, text="✕  Deny / Cancel", height=36, corner_radius=8,
            fg_color="#C0392B", hover_color="#922B21",
            font=ctk.CTkFont(size=13, weight="bold"), command=self.deny_action,
        )
        self.deny_btn.pack(side="left")

        # Input
        self.input_frame = ctk.CTkFrame(self, fg_color=("white", "#15202B"), corner_radius=12)
        self.input_frame.grid(row=6, column=0, padx=16, pady=(4, 0), sticky="ew")
        self.input_frame.grid_columnconfigure(0, weight=1)
        self.text_input = ctk.CTkTextbox(
            self.input_frame, height=76, wrap="word", font=ctk.CTkFont(size=13),
            fg_color=("gray98", "#0F1419"), text_color=("#1C2833", "#E8EEF5"), corner_radius=10,
        )
        self.text_input.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=10)
        self.text_input.insert("1.0", "")
        self.text_input.bind("<Control-Return>", lambda e: self._send_text())
        self.send_btn = ctk.CTkButton(
            self.input_frame, text="Send", width=88, height=76, corner_radius=10,
            fg_color="#2471A3", hover_color="#1A5276",
            font=ctk.CTkFont(size=14, weight="bold"), command=self._send_text,
        )
        self.send_btn.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=10)

        self.action_button = ctk.CTkButton(
            self, text="▶  Start Assistant", font=ctk.CTkFont(size=16, weight="bold"),
            height=48, corner_radius=12, fg_color="#1ABC9C", hover_color="#16A085",
            command=self.toggle_assistant,
        )
        self.action_button.grid(row=7, column=0, padx=16, pady=12, sticky="ew")
        self.action_button.configure(state="disabled")

    def _post_ui(self, callback, *args, **kwargs) -> bool:
        """Queue UI work; only the Tk main thread touches widgets."""
        if self._closing:
            return False
        try:
            self._ui_queue.put_nowait((callback, args, kwargs))
            return True
        except Exception as error:
            logger.exception("Could not queue UI callback", extra={"event": "ui.callback_queue_failed"})
            return False

    def _drain_ui_queue(self):
        """Run queued callbacks from the Tk event loop."""
        self._ui_poll_id = None
        if self._closing:
            return
        for _ in range(100):
            if self._closing:
                break
            try:
                callback, args, kwargs = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args, **kwargs)
            except Exception as error:
                logger.exception("Queued UI callback failed", extra={"event": "ui.callback_failed"})
        if not self._closing:
            try:
                self._ui_poll_id = self.after(25, self._drain_ui_queue)
            except Exception:
                self._ui_poll_id = None

    def _show_mobile_pairing(self):
        """Display a one-time QR code and require explicit desktop approval."""
        import json
        import os
        from datetime import datetime, timezone

        endpoint = os.environ.get("VOICEASSISTANT_MOBILE_PUBLIC_URL", "").strip()
        if not endpoint:
            messagebox.showerror(
                "Mobile pairing is not configured",
                "Set VOICEASSISTANT_MOBILE_PUBLIC_URL to the HTTPS address your phone can reach. Use a trusted TLS certificate and a VPN or secure reverse proxy for remote networks.",
                parent=self,
            )
            return

        try:
            identity_store = RemoteIdentityStore()
            desktop_identity = identity_store.status()
            if not desktop_identity.get("initialized"):
                confirmed = messagebox.askyesno(
                    "Create desktop security identity",
                    "This PC does not have a remote identity yet. Create its unique Ed25519 identity now? "
                    "You will compare its fingerprint with the one shown on your phone. "
                    "The identity is encrypted locally and will not be silently replaced.",
                    parent=self,
                )
                if not confirmed:
                    return
                desktop_identity = identity_store.bootstrap(confirmed=True)
            import qrcode
            payload = pairing_coordinator.create_session(
                endpoint, desktop_identity=desktop_identity
            )
            qr_bitmap = qrcode.make(json.dumps(payload, separators=(",", ":"))).get_image()
        except Exception as error:
            logger.exception("Could not create mobile pairing QR", extra={"event": "mobile.pairing_qr_failed"})
            messagebox.showerror("Could not create pairing QR", str(error), parent=self)
            return

        window = ctk.CTkToplevel(self)
        window.title("Connect a mobile device")
        window.geometry("540x600")
        window.resizable(False, False)
        window.transient(self)
        window.grab_set()

        heading = ctk.CTkLabel(window, text="Scan with VoiceAssistant Mobile", font=ctk.CTkFont(size=19, weight="bold"))
        heading.pack(padx=20, pady=(18, 6))
        qr_image = ctk.CTkImage(light_image=qr_bitmap, dark_image=qr_bitmap, size=(300, 300))
        image_label = ctk.CTkLabel(window, text="", image=qr_image)
        image_label.image = qr_image
        image_label.pack(padx=20, pady=8)
        desktop_fingerprint = desktop_identity["fingerprint_sha256"]
        grouped_desktop_fingerprint = " ".join(
            desktop_fingerprint[index:index + 4]
            for index in range(0, len(desktop_fingerprint), 4)
        )
        identity_label = ctk.CTkLabel(
            window,
            text="Verify this PC on your phone. Compare this fingerprint exactly:\\n"
                 + grouped_desktop_fingerprint,
            wraplength=480,
            justify="center",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        identity_label.pack(padx=20, pady=(4, 8))
        status = ctk.CTkLabel(window, text="Waiting for a phone to scan this code...", wraplength=470, justify="center")
        status.pack(padx=20, pady=8)

        request_frame = ctk.CTkFrame(window, fg_color="transparent")
        request_frame.pack(fill="x", padx=20, pady=8)
        approve_button = ctk.CTkButton(request_frame, text="Approve device", state="disabled", fg_color="#1E8449", hover_color="#196F3D")
        approve_button.pack(side="left", expand=True, fill="x", padx=(0, 6))
        deny_button = ctk.CTkButton(request_frame, text="Deny", state="disabled", fg_color="#922B21", hover_color="#7B241C")
        deny_button.pack(side="left", expand=True, fill="x", padx=(6, 0))

        expiry = datetime.fromisoformat(payload["expires_at"]).astimezone(timezone.utc).timestamp()
        session_id = payload["session_id"]
        active_request = {"id": None, "decided": False, "closed": False}

        def approve():
            request_id = active_request["id"]
            if not request_id or active_request["decided"]:
                return
            try:
                if pairing_coordinator.approve(request_id, role="operator"):
                    active_request["decided"] = True
                    approve_button.configure(state="disabled")
                    deny_button.configure(state="disabled")
                    status.configure(text="Approved. Waiting for the phone to receive its one-time credentials...")
                else:
                    status.configure(text="This request expired. Close this window and create a new QR.")
            except Exception as error:
                logger.exception("Mobile pairing approval failed", extra={"event": "mobile.pairing_approval_failed"})
                status.configure(text="Approval failed: " + str(error))

        def deny():
            request_id = active_request["id"]
            if not request_id or active_request["decided"]:
                return
            pairing_coordinator.deny(request_id)
            active_request["decided"] = True
            approve_button.configure(state="disabled")
            deny_button.configure(state="disabled")
            status.configure(text="Pairing request denied.")

        approve_button.configure(command=approve)
        deny_button.configure(command=deny)

        def close():
            if active_request["closed"]:
                return
            active_request["closed"] = True
            try:
                pairing_coordinator.cancel(session_id)
            except Exception:
                logger.exception("Mobile pairing cancellation failed", extra={"event": "mobile.pairing_cancel_failed"})
            try:
                window.grab_release()
                window.destroy()
            except Exception:
                pass

        window.protocol("WM_DELETE_WINDOW", close)

        def poll():
            if active_request["closed"] or not window.winfo_exists():
                return
            if datetime.now(timezone.utc).timestamp() >= expiry:
                pairing_coordinator.cancel(session_id)
                status.configure(text="QR code expired. Close this window and generate a new one.")
                approve_button.configure(state="disabled")
                deny_button.configure(state="disabled")
                return
            if active_request["decided"]:
                state = pairing_coordinator.session_state(session_id)
                if state == "completed":
                    status.configure(text="Device enrolled successfully. You can close this window.")
                    return
                if state == "denied":
                    return
            else:
                requests = pairing_coordinator.pending_requests()
                match = next((item for item in requests if item["session_id"] == session_id), None)
                if match and active_request["id"] is None:
                    active_request["id"] = match["request_id"]
                    fingerprint = match.get("identity_fingerprint", "")
                    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                        status.configure(text="Pairing request has no valid mobile identity fingerprint. It cannot be approved.")
                        pairing_coordinator.deny(match["request_id"])
                        active_request["decided"] = True
                        return
                    grouped_fingerprint = " ".join(
                        fingerprint[index:index + 4] for index in range(0, len(fingerprint), 4)
                    )
                    status.configure(
                        text=(
                            match["device_name"]
                            + " wants to connect. Verify this device before approval.\n"
                            + "Ed25519 SHA-256 fingerprint:\n"
                            + grouped_fingerprint
                        )
                    )
                    approve_button.configure(state="normal")
                    deny_button.configure(state="normal")
            window.after(1000, poll)

        self.update_transcript("[Mobile] A short-lived QR pairing code was created. The phone will not be enrolled until you approve it here.")
        poll()
    def _show_remote_session_qr(self):
        """Create a short-lived, signed remote-access QR for an already paired phone."""
        import json
        import os
        import qrcode

        endpoint = os.environ.get("VOICEASSISTANT_MOBILE_PUBLIC_URL", "").strip()
        if not endpoint:
            messagebox.showerror(
                "Remote mobile access is not configured",
                "Set VOICEASSISTANT_MOBILE_PUBLIC_URL to the HTTPS address the phone can reach. "
                "The address must route to this desktop API with a trusted TLS certificate.",
                parent=self,
            )
            return

        devices = [
            item for item in mobile_devices.list_devices()
            if item.get("status") == "active" and item.get("identity_fingerprint")
        ]
        if not devices:
            messagebox.showinfo(
                "No eligible paired phones",
                "Pair a phone first. Remote QR access requires an active paired device with an Ed25519 identity.",
                parent=self,
            )
            return

        chooser = ctk.CTkToplevel(self)
        chooser.title("Create remote mobile session")
        chooser.geometry("560x300")
        chooser.resizable(False, False)
        chooser.transient(self)
        chooser.grab_set()

        ctk.CTkLabel(
            chooser,
            text="Choose an already paired phone",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(padx=20, pady=(20, 8))

        device_labels = {
            f"{item.get('name', 'Mobile device')} • {item['device_id'][:8]}": item
            for item in devices
        }
        selected_label = next(iter(device_labels))
        device_var = ctk.StringVar(value=selected_label)
        device_menu = ctk.CTkOptionMenu(
            chooser, values=list(device_labels), variable=device_var, width=480
        )
        device_menu.pack(padx=20, pady=8)

        endpoint_var = ctk.StringVar(value=endpoint)
        endpoint_entry = ctk.CTkEntry(
            chooser, textvariable=endpoint_var, width=480,
            placeholder_text="https://your-public-hostname",
        )
        endpoint_entry.pack(padx=20, pady=8)

        ctk.CTkLabel(
            chooser,
            text="QR expires in 90 seconds. The authenticated mobile session lasts 10 minutes. "
                 "Both devices must be able to reach this HTTPS/WSS endpoint.",
            wraplength=500,
            justify="center",
        ).pack(padx=20, pady=8)

        def create_qr():
            selected = device_labels.get(device_var.get())
            if selected is None:
                messagebox.showerror("Select a phone", "Choose an active paired phone.", parent=chooser)
                return
            if not messagebox.askyesno(
                "Authorize remote access",
                f"Create a 10-minute remote session for {selected.get('name', 'this phone')}? "
                "Anyone who scans the QR before it expires can attempt the signed device challenge.",
                parent=chooser,
            ):
                return
            try:
                payload = _create_remote_session_qr({
                    "device_id": selected["device_id"],
                    "endpoint_url": endpoint_var.get().strip(),
                    "challenge_ttl_seconds": 90,
                    "session_ttl_seconds": 600,
                })
                bitmap = qrcode.make(json.dumps(payload, separators=(",", ":"))).get_image()
            except Exception as error:
                logger.exception(
                    "Could not create remote mobile session QR",
                    extra={"event": "mobile.remote_session_qr_failed"},
                )
                messagebox.showerror("Could not create remote session QR", str(error), parent=chooser)
                return

            chooser.grab_release()
            chooser.destroy()
            window = ctk.CTkToplevel(self)
            window.title("Remote mobile session")
            window.geometry("520x610")
            window.resizable(False, False)
            window.transient(self)
            window.grab_set()

            ctk.CTkLabel(
                window, text="Scan with the already paired phone",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(padx=20, pady=(18, 6))
            qr_image = ctk.CTkImage(light_image=bitmap, dark_image=bitmap, size=(300, 300))
            image_label = ctk.CTkLabel(window, text="", image=qr_image)
            image_label.image = qr_image
            image_label.pack(padx=20, pady=8)

            fingerprint = payload["desktop_identity"]["fingerprint_sha256"]
            grouped = " ".join(fingerprint[i:i + 4] for i in range(0, len(fingerprint), 4))
            ctk.CTkLabel(
                window,
                text="Verify the desktop fingerprint on the phone:\n" + grouped,
                wraplength=480,
                justify="center",
                font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(padx=20, pady=6)

            status = ctk.CTkLabel(
                window, text="Waiting for the phone to redeem the signed challenge...",
                wraplength=470, justify="center",
            )
            status.pack(padx=20, pady=8)
            buttons = ctk.CTkFrame(window, fg_color="transparent")
            buttons.pack(fill="x", padx=20, pady=10)

            def revoke():
                remote_session_store.revoke_session(payload["session_id"])
                status.configure(text="Remote session revoked.")
                revoke_button.configure(state="disabled")

            revoke_button = ctk.CTkButton(
                buttons, text="Revoke session", fg_color="#922B21",
                hover_color="#7B241C", command=revoke,
            )
            revoke_button.pack(side="left", expand=True, fill="x", padx=6)
            ctk.CTkButton(
                buttons, text="Close", command=window.destroy,
            ).pack(side="left", expand=True, fill="x", padx=6)

            def poll_state():
                if not window.winfo_exists():
                    return
                state = remote_session_store.state(payload["session_id"])
                if state == "pending":
                    status.configure(text="QR scanned? Complete the connection on the phone. Challenge is still pending.")
                    window.after(1000, poll_state)
                elif state == "active":
                    status.configure(text="Phone authenticated. The short-lived remote session is active.")
                    revoke_button.configure(state="normal")
                    window.after(5000, poll_state)
                else:
                    status.configure(text="QR expired, was redeemed and closed, or the session was revoked.")
                    revoke_button.configure(state="disabled")

            poll_state()
            self.update_transcript(
                f"[Mobile] Signed 10-minute remote session QR created for {selected.get('name', 'paired phone')}."
            )

        ctk.CTkButton(
            chooser, text="Generate remote QR", command=create_qr,
            fg_color="#1F618D", hover_color="#154360", width=220,
        ).pack(padx=20, pady=(10, 18))

    def _run_mobile_server(self):
        try:
            start_server(host=self.mobile_host, port=8765)
        except Exception as e:
            message = f"Mobile API failed to start: {e}"
            logger.exception("Mobile API failed to start", extra={"event": "mobile.server_start_failed"})
            self.update_status("Mobile API unavailable", "#E67E22")
            self.update_transcript(f"[Warning] {message}")

    def _on_close(self):
        """Stop background activity before destroying the Tk application."""
        if self._closing:
            return
        self._closing = True
        self.is_running = False
        try:
            stop_server()
        except Exception:
            logger.exception("Mobile API shutdown signal failed", extra={"event": "shutdown.mobile_api_signal_failed"})
        if self._ui_poll_id is not None:
            try:
                self.after_cancel(self._ui_poll_id)
            except Exception:
                pass
            self._ui_poll_id = None
        try:
            self.conversation.request_cancel("shutdown")
        except Exception as e:
            logger.exception("Conversation shutdown failed", extra={"event": "shutdown.conversation_failed"})
        try:
            scheduler_stopped = self.conversation.scheduler.stop(timeout=2.0)
            if not scheduler_stopped:
                logger.warning(
                    "Scheduler handler is still running after the shutdown grace period; "
                    "its external side effects cannot be assumed cancelled",
                    extra={"event": "shutdown.scheduler_timeout"},
                )
        except Exception as e:
            logger.exception("Scheduler shutdown failed", extra={"event": "shutdown.scheduler_failed"})
        try:
            self.tts.shutdown()
        except Exception as e:
            logger.exception("Speech output shutdown failed", extra={"event": "shutdown.tts_failed"})
        mobile_thread = getattr(self, "_mobile_server_thread", None)
        if mobile_thread and mobile_thread.is_alive():
            mobile_thread.join(timeout=2.5)
            if mobile_thread.is_alive():
                logger.warning("Mobile API did not stop within the shutdown grace period", extra={"event": "shutdown.mobile_api_timeout"})
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception as e:
            logger.exception("Desktop window destruction failed", extra={"event": "shutdown.window_destroy_failed"})

    def _show_action_bar(self, message: str):
        self.action_bar_label.configure(text=message)
        self.action_bar.grid()

    def _hide_action_bar(self):
        self.action_bar.grid_remove()

    def _setup_hotkeys(self):
        try:
            keyboard.add_hotkey("ctrl+shift+space", lambda: self._post_ui(self.toggle_assistant))
            keyboard.add_hotkey("esc", lambda: self._post_ui(self._stop_activity))
        except Exception as error:
            logger.exception("Global hotkey registration unavailable", extra={"event": "ui.hotkey_registration_failed"})

    def _hotkey_toggle(self):
        self._post_ui(self.toggle_assistant)

    def _initialize_engines(self):
        stt_ready = False
        tts_ready = False
        self.update_transcript("[System] Initializing speech services...")
        try:
            self.stt.initialize()
            stt_ready = True
        except Exception as e:
            self.update_transcript(f"[Warning] Speech recognition unavailable: {e}")
        try:
            self.tts.initialize()
            tts_ready = True
        except Exception as e:
            self.update_transcript(f"[Warning] Text-to-speech unavailable: {e}")
        self._stt_ready = stt_ready
        self._tts_ready = tts_ready
        if stt_ready and tts_ready:
            status = "Ready"
        elif stt_ready or tts_ready:
            status = "Ready (degraded voice services)"
        else:
            status = "Ready (text mode; voice services unavailable)"
        self.update_status(status)
        self.update_transcript("[System] Agent core initialized. Text chat remains available.")
        self._post_ui(self._set_voice_button_state, stt_ready)
        self._post_ui(self._schedule_model_refresh)

    def _set_voice_button_state(self, available: bool):
        if self._closing:
            return
        try:
            self.action_button.configure(state="normal" if available else "disabled")
        except Exception:
            pass

    def _schedule_model_refresh(self):
        if self._closing:
            return
        try:
            self.after(1500, self._refresh_models)
        except Exception:
            pass

    def update_status(self, text: str, color: str | None = None):
        """Queue status updates for the Tk main thread; never touch widgets from workers."""
        def _apply():
            if self._closing or not hasattr(self, "status_label"):
                return
            self.status_label.configure(text=text)
            self.status_label.configure(
                text_color=color or ("#1A5276", "#5DADE2")
            )

        self._post_ui(_apply)



    def _window_is_background(self) -> bool:
        """True when minimized or not focused."""
        try:
            if str(self.state()) == "iconic":
                return True
        except Exception:
            pass
        try:
            if self.focus_displayof() is None:
                return True
        except Exception:
            pass
        return False

    def _notify_if_background(self, title: str, message: str) -> None:
        def _apply():
            if self._closing or notify_background is None:
                return
            try:
                if not self._window_is_background():
                    return
                hwnd = None
                try:
                    hwnd = int(self.winfo_id())
                except Exception:
                    hwnd = None
                preview = (message or "Your request is complete.").replace(chr(10), " ").strip()
                if len(preview) > 160:
                    preview = preview[:157] + "..."
                notify_background(title or "VoiceAssistant", preview, hwnd=hwnd)
            except Exception as error:
                logger.exception("Background notification failed", extra={"event": "ui.notification_failed"})

        self._post_ui(_apply)

    def update_transcript(self, text: str):
        """Append to transcript as chat-style blocks (User / Assistant / System)."""
        def _apply():
            try:
                if not hasattr(self, "transcript_area") or self.transcript_area is None:
                    return
                self.transcript_area.configure(state="normal")
                self._insert_chat_block(text)
                self._trim_transcript()
                self.transcript_area.see("end")
                self.transcript_area.configure(state="disabled")
            except Exception as e:
                logger.exception("Transcript update failed", extra={"event": "ui.transcript_update_failed"})
                return

            if "File deployed successfully" in text or "[Success] File deployed" in text:
                short = text
                if "to " in text:
                    short = "File deployed: " + text.split("to ", 1)[-1].strip()
                self._show_action_bar(short)

            if "[Undo]" in text:
                try:
                    stack = self.conversation.undo.list_stack()
                    if not stack:
                        self._hide_action_bar()
                except Exception:
                    pass

        self._post_ui(_apply)


    def _trim_transcript(self) -> None:
        """Keep the live UI transcript bounded; the encrypted history remains authoritative."""
        try:
            textbox = getattr(self.transcript_area, "_textbox", None)
            if textbox is None:
                return
            line_count = int(textbox.index("end-1c").split(".", 1)[0])
            excess = line_count - self._max_transcript_lines
            if excess > 0:
                textbox.delete("1.0", f"{excess + 1}.0")
        except Exception as error:
            logger.exception("Transcript trimming failed", extra={"event": "ui.transcript_trim_failed"})

    def _insert_chat_block(self, text: str) -> None:
        """Format transcript like a chat: clear You vs Assistant vs system."""
        import re as _re
        raw = "" if text is None else str(text)
        if not raw.strip():
            return

        ts = ""
        body = raw
        m = _re.match(r"^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$", raw, _re.S)
        if m:
            ts = m.group(1)
            body = m.group(2)

        body_st = body.lstrip()
        # Drop model echo prefix "assistant"
        body_st = _re.sub(r"(?is)^assistant\s*", "", body_st, count=1).lstrip()

        tag = "system"
        header = None
        render_md = False
        content = body_st

        if body_st.startswith("You:") or body_st.startswith("You :"):
            tag = "user"
            content = _re.sub(r"^You\s*:\s*", "", body_st, count=1)
            header = "You" + (f"  ·  {ts}" if ts else "")
        elif body_st.startswith("AI:") or body_st.startswith("AI :"):
            tag = "assistant"
            content = _re.sub(r"^AI\s*:\s*", "", body_st, count=1)
            content = _re.sub(r"(?is)^assistant\s*", "", content, count=1).lstrip()
            header = "Assistant" + (f"  ·  {ts}" if ts else "")
            render_md = True
        elif body_st.startswith("[Success]") or body_st.startswith("Success"):
            tag = "success"
            content = body_st
            header = "Result" + (f"  ·  {ts}" if ts else "")
            render_md = True
        elif body_st.startswith("[Error]") or body_st.startswith("Error"):
            tag = "error"
            content = body_st
            header = "Error" + (f"  ·  {ts}" if ts else "")
        elif body_st.startswith((
            "[Plan]", "[Planner]", "[Verify]", "[Gate]", "[Timing]",
            "[Policy]", "[System]", "[Undo]", "[Skill]", "[Scheduler]",
            "[Outcome]", "[Memory]", "[SessionSync]",
        )):
            tag = "meta"
            content = body_st
            header = None
        else:
            tag = "system"
            content = body_st
            header = None

        tb = getattr(self.transcript_area, "_textbox", None)

        def _ins(s, *tags):
            if not s:
                return
            if tb is not None and tags:
                try:
                    tb.insert("end", s, tags)
                    return
                except Exception:
                    pass
            self.transcript_area.insert("end", s)

        if header:
            _ins("\n")
            _ins("─" * 42 + "\n", "divider")
            _ins(header + "\n", tag)
            if render_md:
                self._insert_markdown(content.rstrip() + "\n\n", base_tag=tag)
            else:
                _ins(content.rstrip() + "\n\n", tag)
        else:
            line = content.rstrip()
            if ts:
                line = f"[{ts}] {line}"
            _ins(line + "\n", tag)

    def _insert_markdown(self, text: str, base_tag: str = "assistant") -> None:
        """Render light markdown: **bold**, *italic*, # headings, lists."""
        import re as _re
        tb = getattr(self.transcript_area, "_textbox", None)

        def _ins(s, *tags):
            if not s:
                return
            if tb is not None:
                try:
                    use = tuple(t for t in tags if t)
                    if use:
                        tb.insert("end", s, use)
                    else:
                        tb.insert("end", s, (base_tag,))
                    return
                except Exception:
                    pass
            self.transcript_area.insert("end", s)

        for raw_line in (text or "").splitlines():
            line = raw_line
            hm = _re.match(r"^(#{1,3})\s+(.*)$", line)
            if hm:
                _ins(hm.group(2).rstrip() + "\n", base_tag, "heading")
                continue

            bm = _re.match(r"^(\s*(?:[-*]\s+|\d+\.\s+))(.*)$", line)
            if bm:
                _ins(bm.group(1), base_tag, "bullet")
                line = bm.group(2)

            pos = 0
            pattern = _re.compile(r"(\*\*(.+?)\*\*|\*(.+?)\*|__(.+?)__|_(.+?)_)")
            for m in pattern.finditer(line):
                if m.start() > pos:
                    _ins(line[pos:m.start()], base_tag)
                if m.group(2) is not None:
                    _ins(m.group(2), base_tag, "bold")
                elif m.group(3) is not None:
                    _ins(m.group(3), base_tag, "italic")
                elif m.group(4) is not None:
                    _ins(m.group(4), base_tag, "bold")
                elif m.group(5) is not None:
                    _ins(m.group(5), base_tag, "italic")
                pos = m.end()
            if pos < len(line):
                _ins(line[pos:], base_tag)
            _ins("\n", base_tag)



    def speak(self, text: str):
        try:
            self._notify_if_background("Response ready", text or "Assistant finished.")
        except Exception:
            pass
        if not self._tts_ready:
            self.update_status("Text response ready (speech output unavailable)", "#E67E22")
            return
        self.update_status("Speaking...", "#3498DB")
        self.tts.speak(
            text,
            on_finished=lambda: self.update_status("Listening... Speak now", "#2ECC71")
            if self.is_running
            else None,
        )

    def _stop_speaking(self):
        self.tts.stop()
        self.update_status("Speech interrupted", "#E67E22")

    def _stop_activity(self):
        """Stop speech + cancel in-flight plan/LLM/tool wait (debounced)."""
        import time
        now = time.time()
        if getattr(self, "_last_stop_ts", 0) and (now - self._last_stop_ts) < 1.5:
            return
        self._last_stop_ts = now
        try:
            self.tts.stop()
        except Exception:
            pass
        try:
            if self.pending_callback:
                self.deny_action()
        except Exception:
            try:
                self.clear_security_gate()
            except Exception:
                pass
        try:
            if self.conversation:
                self.conversation.request_cancel("ui")
        except Exception as e:
            self.update_transcript(f"[System] Stop failed: {e}")
            return
        self.update_status("Stop requested", "#E67E22")
        self.update_transcript("[System] Stop Activity — cancelling current work…")


    def _on_model_change(self, model_name: str):
        self.llm.set_model(model_name)
        self.update_transcript(f"[System] Switched model to: {model_name}")

    def _refresh_models(self):
        if self._closing or self._models_refreshing:
            return
        self._models_refreshing = True
        try:
            self.refresh_models_btn.configure(state="disabled")
        except Exception:
            pass

        def _load():
            models = []
            error = None
            try:
                models = self.llm.get_available_models() or []
            except Exception as exc:
                error = str(exc)
            self._post_ui(self._apply_model_refresh, models, error)

        threading.Thread(target=_load, name="OllamaModelRefresh", daemon=True).start()

    def _apply_model_refresh(self, models, error):
        self._models_refreshing = False
        if self._closing:
            return
        try:
            self.refresh_models_btn.configure(state="normal")
        except Exception:
            pass
        if error:
            self.update_status("Ollama model refresh failed", "#E67E22")
            self.update_transcript(f"[Warning] Could not refresh Ollama models: {error}")
            return
        if models:
            self.model_menu.configure(values=models)
            current = self.model_var.get()
            if current not in models:
                self.model_var.set(models[0])
                self.llm.set_model(models[0])
            self.update_transcript(f"[System] Models refreshed: {len(models)} models found")
        else:
            self.update_transcript("[System] No models found from Ollama")

    def _toggle_dry_run(self):
        self.dry_run_enabled = self.dry_run_var.get()
        self.tool_registry.set_dry_run(self.dry_run_enabled)
        status = "ENABLED" if self.dry_run_enabled else "DISABLED"
        self.update_transcript(f"[System] Dry-Run Mode {status}")

    def _clear_history(self):
        if not messagebox.askyesno(
            "Clear conversation history",
            "This permanently clears the current conversation history. Continue?",
            parent=self,
        ):
            return
        try:
            self.conversation.clear_history()
            self.transcript_area.configure(state="normal")
            self.transcript_area.delete("0.0", "end")
            self.transcript_area.insert("0.0", "[System] Conversation history cleared.\n")
            self.transcript_area.configure(state="disabled")
            self._hide_action_bar()
            self.update_status("History cleared", "#27AE60")
        except Exception as error:
            self.update_transcript(f"[Error] Could not clear conversation history: {error}")
            self.update_status("Clear history failed", "#E74C3C")

    def _undo_last_action(self, on_complete=None):
        if self._closing:
            return
        if self._undo_in_progress:
            if on_complete is not None:
                self._undo_completion_callbacks.append(on_complete)
            return
        if not messagebox.askyesno(
            "Undo last change",
            "Restore the previous file content for the latest undo entry?",
            parent=self,
        ):
            if on_complete is not None:
                try:
                    on_complete()
                except Exception:
                    pass
            return
        if on_complete is not None:
            self._undo_completion_callbacks.append(on_complete)
        self._undo_in_progress = True
        try:
            self.inline_undo_btn.configure(state="disabled")
        except Exception:
            pass

        def _run_undo():
            result = None
            stack = []
            error = None
            try:
                result = self.conversation.undo.undo_last()
                if isinstance(result, dict) and result.get("ok"):
                    try:
                        stack = self.conversation.undo.list_stack()
                    except Exception as stack_error:
                        logger.exception("Could not refresh remaining undo stack", extra={"event": "undo.stack_refresh_failed"})
            except Exception as exc:
                error = str(exc)
            self._post_ui(self._finish_undo, result, stack, error)

        threading.Thread(target=_run_undo, name="UndoOperation", daemon=True).start()

    def _finish_undo(self, result, stack, error):
        self._undo_in_progress = False
        if self._closing:
            self._undo_completion_callbacks.clear()
            return
        try:
            self.inline_undo_btn.configure(state="normal")
        except Exception:
            pass

        if error:
            self.update_transcript(f"[Undo] Error: {error}")
            self.update_status("Undo failed", "#E74C3C")
        elif not isinstance(result, dict):
            self.update_transcript("[Undo] Undo returned an invalid result.")
            self.update_status("Undo failed", "#E74C3C")
        else:
            message = result.get("message") or ("Undo completed." if result.get("ok") else "Nothing to undo.")
            self.update_transcript(f"[Undo] {message}")
            if not result.get("ok"):
                self.update_status("Nothing to undo", "#E67E22")
                self._hide_action_bar()
            else:
                self.update_status("Undone", "#27AE60")
                self._hide_action_bar()
                if stack:
                    filepath = stack[0].get("filepath") or ""
                    if filepath:
                        self._show_action_bar(f"Previous file change: {filepath}")

        callbacks = self._undo_completion_callbacks
        self._undo_completion_callbacks = []
        for callback in callbacks:
            try:
                callback()
            except Exception as callback_error:
                logger.exception("Undo completion callback failed", extra={"event": "undo.completion_callback_failed"})


    def _show_profile(self):
        if self._closing:
            return
        profile = getattr(self.conversation, "profile", None)
        if profile is None:
            self.update_transcript("[Profile] Profile storage is unavailable; personalization is disabled.")
            return
        def _saved():
            try:
                self.conversation.refresh_profile_context()
                self.update_transcript("[Profile] Preferences saved. Assistant context follows the opt-in setting.")
            except Exception as error:
                logger.exception("Profile context refresh failed", extra={"event": "profile.context_refresh_failed"})
                self.update_transcript(f"[Profile] Saved, but context refresh failed: {type(error).__name__}")
        try:
            open_profile_viewer(self, profile, on_saved=_saved)
        except Exception as error:
            logger.exception("Profile editor failed to open", extra={"event": "profile.editor_open_failed"})
            self.update_transcript(f"[Profile] Could not open editor: {error}")

    def _show_automation_catalog(self):
        if self._closing:
            return
        profile = getattr(self.conversation, "profile", None)
        if profile is None:
            self.update_transcript("[Automations] Profile storage is unavailable; catalog cannot load.")
            return
        def _toggle_automation(automation_id, enabled):
            handlers = {
                "birthday_reminders": self.conversation.set_birthday_reminders_enabled,
                "daily_language_lesson": self.conversation.set_daily_language_lesson_enabled,
            }
            handler = handlers.get(automation_id)
            if handler is None:
                raise ValueError("This automation cannot be enabled yet")
            result = handler(enabled)
            label = "Birthday reminders" if automation_id == "birthday_reminders" else "Daily language lesson"
            if result.get("active"):
                self.update_transcript(f"[Automations] {label} scheduled. The desktop application must remain open.")
            else:
                self.update_transcript(f"[Automations] {label}: " + str(result.get("reason", "disabled")))
            return result
        try:
            open_automation_catalog_viewer(
                self,
                profile,
                scheduler=self.conversation.scheduler,
                on_automation_toggle=_toggle_automation,
            )
        except Exception as error:
            logger.exception("Automation catalog failed to open", extra={"event": "automation.catalog_open_failed"})
            self.update_transcript(f"[Automations] Could not open catalog: {type(error).__name__}")

    def _show_scheduler(self):
        try:
            open_scheduler_viewer(
                self,
                self.conversation.scheduler,
                on_fill_message=self._fill_scheduler_message,
                on_schedule_direct=self._run_scheduler_prompt,
            )
        except Exception as e:
            self.update_transcript(f"[Scheduler] UI error: {e}")

    def _show_undo_history(self):
        try:
            open_undo_viewer(
                self,
                self.conversation.undo,
                on_undo=self._undo_last_action,
                on_transcript=self.update_transcript,
            )
        except Exception as e:
            self.update_transcript(f"[Undo] History UI error: {e}")

    def _restart_app(self):
        if not messagebox.askyesno(
            "Restart VoiceAssistant",
            "The application will close and start again. Continue?",
            parent=self,
        ):
            return
        self.update_transcript("[System] Restarting application...")
        try:
            restart_app(self)
        except Exception as error:
            self.update_transcript(f"[Restart] Could not restart: {error}")
            self.update_status("Restart failed", "#E74C3C")

    def _show_skill_author(self):

        try:
            open_skill_author(
                self,
                on_transcript=self.update_transcript,
                skill_packs=getattr(self.conversation, "skill_packs", None),
            )
        except Exception as e:
            self.update_transcript(f"[Skill Author] UI error: {e}")

    def _show_policy(self):

        try:
            open_policy_viewer(
                self,
                self.conversation.policy,
                on_transcript=self.update_transcript,
            )
        except Exception as e:
            self.update_transcript(f"[Policy] UI error: {e}")

    def _fill_scheduler_message(self, prompt: str):
        """Put generated schedule command into the message box for the user."""
        try:
            self.text_input.delete("1.0", "end")
            self.text_input.insert("1.0", prompt)
            self.text_input.focus_set()
            self.update_status("Schedule command ready in message box — press Send", "#1ABC9C")
        except Exception as e:
            self.update_transcript(f"[Scheduler] Fill error: {e}")

    def _run_scheduler_prompt(self, prompt: str):
        """Directly process the schedule command (form Schedule now)."""
        import threading
        threading.Thread(
            target=self.conversation.process_user_input,
            args=(prompt,),
            daemon=True,
        ).start()

    def _export_conversation(self):
        if self._closing:
            return
        try:
            messages = list(self.conversation.session_messages)
            self.export_btn.configure(state="disabled")
        except Exception as error:
            self.update_transcript(f"[Export] Could not prepare conversation: {error}")
            return

        def _export():
            path = None
            error = None
            try:
                path = self.history_manager.export_conversation(messages, format="md")
            except Exception as exc:
                error = str(exc)
            self._post_ui(self._finish_export, path, error)

        threading.Thread(target=_export, name="ConversationExport", daemon=True).start()

    def _finish_export(self, path, error):
        try:
            self.export_btn.configure(state="normal")
        except Exception:
            pass
        if error:
            self.update_transcript(f"[Export] Failed: {error}")
            self.update_status("Export failed", "#E74C3C")
            return
        self.update_transcript(f"[System] Conversation exported to:\n{path}")
        self.update_status("Conversation exported", "#27AE60")

    def _show_audit_log(self):
        if self._closing:
            return
        try:
            self.audit_btn.configure(state="disabled")
        except Exception:
            pass

        def _load():
            logs = None
            error = None
            try:
                logs = self.history_manager.get_audit_log()
            except Exception as exc:
                error = str(exc)
            self._post_ui(self._finish_audit_log, logs, error)

        threading.Thread(target=_load, name="AuditLogLoad", daemon=True).start()

    def _finish_audit_log(self, logs, error):
        try:
            self.audit_btn.configure(state="normal")
        except Exception:
            pass
        if error:
            self.update_transcript(f"[Audit] Could not load audit log: {error}")
            return
        AuditLogViewer(self, logs or [])

    def _show_help(self):
        try:
            help_list = self.tool_registry.get_all_help()
        except Exception:
            help_list = []
        HelpWindow(self, tool_help_list=help_list)

    def _send_text(self):
        text = self.text_input.get("1.0", "end").strip()
        if not text:
            return
        self.text_input.delete("1.0", "end")
        threading.Thread(
            target=self.conversation.process_user_input, args=(text,), daemon=True
        ).start()

    def show_security_gate(self, display_text: str, callback, risk_level: str = "low"):
        self._post_ui(self._show_security_gate_ui, display_text, callback, risk_level)

    def _show_security_gate_ui(self, display_text: str, callback, risk_level: str = "low"):
        self.pending_callback = callback
        try:
            self.gate_cmd_display.configure(state="normal")
            self.gate_cmd_display.delete("1.0", "end")
            self.gate_cmd_display.insert("1.0", display_text or "")
            self.gate_cmd_display.configure(state="disabled")
        except Exception:
            pass
        if risk_level == "high":
            self.gate_label.configure(
                text="⚠️ HIGH RISK – SECURITY APPROVAL REQUIRED:", text_color="#E74C3C"
            )
            self.approve_btn.configure(fg_color="#C0392B", hover_color="#922B21")
        elif risk_level == "medium":
            self.gate_label.configure(
                text="⚠ MEDIUM RISK – APPROVAL REQUIRED:", text_color="#E67E22"
            )
            self.approve_btn.configure(fg_color="#E67E22", hover_color="#D35400")
        else:
            self.gate_label.configure(
                text="SECURITY APPROVAL REQUIRED:", text_color="#E74C3C"
            )
            self.approve_btn.configure(fg_color="#27AE60", hover_color="#219653")
        self.gate_frame.grid()
        self.update_status("Awaiting Security Clearance ⚠️", "#E74C3C")

    def show_diff_preview(self, filepath: str, content: str, on_confirm, on_cancel):
        self._post_ui(lambda: DiffViewer(self, filepath, content, on_confirm, on_cancel))

    def approve_action(self):
        if self.pending_callback:
            self.update_status("Executing tool...", "#2980B9")
            threading.Thread(target=self.pending_callback, daemon=True).start()
            self.clear_security_gate()

    def deny_action(self):
        self.update_transcript("[Security Gate] Action explicitly rejected by user.")
        self.history_manager.log_audit(
            action="denied", details=(self.gate_cmd_display.get("1.0", "end").strip() if hasattr(self.gate_cmd_display, "get") else ""), approved=False
        )
        handled_by_mobile_bridge = bridge.deny_desktop_approval()
        self.clear_security_gate()
        self.update_status("Ready")
        if not handled_by_mobile_bridge:
            self.conversation.master_queue.put(
                {"content": "Action rejected by user.", "is_error": False}
            )

    def _clear_security_gate_threadsafe(self):
        self._post_ui(self.clear_security_gate)

    def clear_security_gate(self):
        self.pending_callback = None
        self.gate_frame.grid_remove()

    def toggle_assistant(self):
        if not self.is_running:
            if not self._stt_ready:
                self.update_status("Voice input unavailable; use text chat", "#E67E22")
                self.update_transcript("[Warning] Speech recognition is not ready. Text chat remains available.")
                return
            self.is_running = True
            self.action_button.configure(
                text="Stop Assistant", fg_color="#C0392B", hover_color="#922B21"
            )
            self._voice_thread = threading.Thread(
                target=self._voice_loop, name="VoiceCaptureLoop", daemon=True
            )
            self._voice_thread.start()
        else:
            self.is_running = False
            self.tts.stop()
            self.action_button.configure(
                text="Start Assistant",
                fg_color=("#3B8ED0", "#1F6AA5"),
                hover_color=("#3278B2", "#144875"),
                state="normal" if self._stt_ready else "disabled",
            )
            self.update_status("Stopped")

    def _voice_loop(self):
        failure = None
        try:
            while self.is_running and not self._closing:
                self.update_status("Listening... Speak now", "#2ECC71")
                audio_data = self.recorder.record(lambda: self.is_running and not self._closing)
                if audio_data is None:
                    continue
                self.update_status("Processing speech...", "#F39C12")
                user_text = self.stt.transcribe(audio_data)
                if not user_text:
                    continue
                self.conversation.process_user_input(user_text)
                time.sleep(1.2)
        except Exception as error:
            failure = str(error)
            self.is_running = False
        finally:
            self._post_ui(self._voice_loop_finished, failure)

    def _voice_loop_finished(self, failure=None):
        if self._closing:
            return
        self.is_running = False
        try:
            self.action_button.configure(
                text="Start Assistant",
                fg_color=("#3B8ED0", "#1F6AA5"),
                hover_color=("#3278B2", "#144875"),
                state="normal" if self._stt_ready else "disabled",
            )
        except Exception:
            pass
        if failure:
            self.update_transcript(f"[Error] Voice input stopped: {failure}")
            self.update_status("Voice input stopped", "#E74C3C")
        else:
            self.update_status("Ready")
