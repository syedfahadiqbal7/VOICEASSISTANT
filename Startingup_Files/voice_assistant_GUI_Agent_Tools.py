import os
import sys
import time
import json
import queue
import threading
import subprocess
import numpy as np
import pyttsx3
import sounddevice as sd
import soundfile as sf
import customtkinter as ctk
from faster_whisper import WhisperModel
from openai import OpenAI

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


class AgenticVoiceAssistant(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Agentic AI Architect & IT Admin Assistant")
        self.geometry("750x700")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Ollama client
        self.client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

        # Audio settings
        self.SILENCE_THRESHOLD = 0.01
        self.SILENCE_LIMIT = 1.8
        self.MAX_DURATION = 25.0
        self.MIN_START_DELAY = 1.0
        self.is_running = False

        # Threading / queues
        self.master_queue = queue.Queue()
        self.speech_lock = threading.Lock()
        self.pending_tool_call = None
        self.pending_callback = None

        # --- UI ---
        self.status_label = ctk.CTkLabel(
            self, text="Loading Core Systems...", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.status_label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        self.separator = ctk.CTkFrame(self, height=2, fg_color="gray30")
        self.separator.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        self.transcript_area = ctk.CTkTextbox(
            self, font=ctk.CTkFont(size=13), wrap="word", activate_scrollbars=True
        )
        self.transcript_area.grid(row=2, column=0, padx=20, pady=10, sticky="nsew")
        self.transcript_area.insert("0.0", "Initializing Agent Core...\n")
        self.transcript_area.configure(state="disabled")

        # Security Gate
        self.gate_frame = ctk.CTkFrame(self, fg_color="gray20", corner_radius=8)
        self.gate_frame.grid(row=3, column=0, padx=20, pady=5, sticky="ew")
        self.gate_frame.grid_remove()

        self.gate_label = ctk.CTkLabel(
            self.gate_frame,
            text="SECURITY APPROVAL REQUIRED:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#E74C3C",
        )
        self.gate_label.pack(anchor="w", padx=15, pady=(5, 0))

        self.gate_cmd_display = ctk.CTkLabel(
            self.gate_frame,
            text="",
            font=ctk.CTkFont(family="Consolas", size=12),
            justify="left",
            anchor="w",
        )
        self.gate_cmd_display.pack(fill="x", padx=15, pady=5)

        self.btn_frame = ctk.CTkFrame(self.gate_frame, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=15, pady=(0, 10))

        self.approve_btn = ctk.CTkButton(
            self.btn_frame,
            text="Approve & Execute",
            fg_color="#27AE60",
            hover_color="#219653",
            command=self.approve_action,
        )
        self.approve_btn.pack(side="left", padx=(0, 10))

        self.deny_btn = ctk.CTkButton(
            self.btn_frame,
            text="Deny / Cancel",
            fg_color="#C0392B",
            hover_color="#922B21",
            command=self.deny_action,
        )
        self.deny_btn.pack(side="left")

        # Main button
        self.action_button = ctk.CTkButton(
            self,
            text="Start Assistant",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=45,
            command=self.toggle_assistant_thread,
        )
        self.action_button.grid(row=4, column=0, padx=20, pady=20, sticky="ew")

        # Tools schema
        self.tools_schema = [
            {
                "type": "function",
                "function": {
                    "name": "write_developer_code",
                    "description": "Writes code, scripts, or configuration files directly to a path on the user's system.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filepath": {
                                "type": "string",
                                "description": "The explicit destination path (e.g. C:/Users/.../Desktop/app.py).",
                            },
                            "content": {
                                "type": "string",
                                "description": "The complete code or script content string.",
                            },
                        },
                        "required": ["filepath", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_powershell_admin",
                    "description": "Executes Windows PowerShell commands for infrastructure configuration, deployment, diagnostics or script execution.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The exact PowerShell string command.",
                            }
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "install_python_dependencies",
                    "description": "Automatically executes pip install commands to download and install missing Python packages/modules.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "modules": {
                                "type": "string",
                                "description": "Space-separated list of Python packages (e.g. 'requests beautifulsoup4 pandas').",
                            }
                        },
                        "required": ["modules"],
                    },
                },
            },
        ]

        threading.Thread(target=self.initialize_engines, daemon=True).start()

    def initialize_engines(self):
        self.update_transcript("[System] Booting Speech-to-Text Model...")
        try:
            self.stt_model = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as e:
            self.update_transcript(f"[Error] Whisper boot failed: {e}")
            return

        self.update_transcript("[System] Binding Audio Output Pipeline...")
        self.tts_engine = pyttsx3.init()
        self.tts_engine.setProperty("rate", 185)
        voices = self.tts_engine.getProperty("voices")
        if len(voices) > 1:
            self.tts_engine.setProperty("voice", voices[1].id)  # Prefer female voice

        self.update_status("Ready")
        self.update_transcript("[System] Master Framework Online. Clear feedback loop active.")

    def update_status(self, text, color=None):
        self.status_label.configure(text=text)
        if color:
            self.status_label.configure(text_color=color)

    def update_transcript(self, text):
        self.transcript_area.configure(state="normal")
        self.transcript_area.insert("end", text + "\n\n")
        self.transcript_area.see("end")
        self.transcript_area.configure(state="disabled")

    def toggle_assistant_thread(self):
        if not self.is_running:
            self.is_running = True
            self.action_button.configure(
                text="Stop Assistant", fg_color="#C0392B", hover_color="#922B21"
            )
            threading.Thread(target=self.run_voice_loop, daemon=True).start()
        else:
            self.is_running = False
            self.action_button.configure(
                text="Start Assistant",
                fg_color=("#3B8ED0", "#1F6AA5"),
                hover_color=("#3278B2", "#144875"),
            )
            self.update_status("Stopped")

        # --- Core Pipeline Execution Loop ---
    def run_voice_loop(self):
        self.master_queue = queue.Queue()
        audio_file = "gui_input.wav"
        
        self.session_messages = [
            {"role": "system", "content": "You are a hybrid Senior Software Developer and Infrastructure Solutions Architect running on Windows 11. Use your available tools immediately to complete requests. If a terminal log returns an error about a missing python module, call the install_python_dependencies tool. If a command flags a syntax error, adjust it natively for Windows environment parameters and retry."}
        ]
        
        while self.is_running:
            # Step 1: Smart Silence Capture Recording
            self.update_status("Listening... Speak now", "#2ECC71")
            
            audio_buffer = []
            silence_duration = 0.0
            has_spoken = False
            start_time = time.time()

            def callback(indata, frames, time_info, status):
                nonlocal silence_duration, has_spoken
                volume = np.sqrt(np.mean(indata**2))
                audio_buffer.append(indata.copy())
                if (time.time() - start_time) < self.MIN_START_DELAY: 
                    return
                if volume > self.SILENCE_THRESHOLD:
                    has_spoken = True
                    silence_duration = 0.0
                else:
                    if has_spoken: 
                        silence_duration += frames / 16000

            try:
                with sd.InputStream(samplerate=16000, channels=1, callback=callback):
                    while self.is_running:
                        sd.sleep(100)
                        if has_spoken and silence_duration >= self.SILENCE_LIMIT: 
                            break
                        if not has_spoken and (time.time() - start_time) > 7.0: 
                            break
                        if (time.time() - start_time) > self.MAX_DURATION: 
                            break
            except Exception as e:
                self.update_transcript(f"[Audio Error] {e}")
                break

            if not self.is_running: 
                break
            if not audio_buffer or not has_spoken: 
                continue

            # Step 2: Speech-To-Text processing
            self.update_status("Processing speech...", "#F39C12")
            recording = np.concatenate(audio_buffer, axis=0)
            sf.write(audio_file, recording, 16000)

            try:
                audio_data, _ = sf.read(audio_file, dtype='float32')
                max_amplitude = np.max(np.abs(audio_data))
                if max_amplitude < 0.02: 
                    continue

                segments, _ = self.stt_model.transcribe(audio_data, beam_size=5)
                user_text = "".join([segment.text for segment in segments]).strip()
            except Exception as e:
                self.update_transcript(f"[STT Error] {e}")
                continue

            hallucination_keywords = ["人民銀行", "Thank you", "you", "晶晶", "谢谢", "Spoken"]
            if any(keyword in user_text for keyword in hallucination_keywords) and len(user_text) < 12:
                continue

            if not user_text: 
                continue
            self.update_transcript(f"You: {user_text}")
            self.session_messages.append({"role": "user", "content": user_text})

            # Multi-Turn Recursion Loop
            while True:
                self.update_status("Agent thinking...", "#9B59B6")
                try:
                    response = self.client.chat.completions.create(
                        model="qwen2.5-coder:32b",
                        messages=self.session_messages,
                        tools=self.tools_schema
                    )

                    response_message = response.choices[0].message
                    content_text = response_message.content if response_message.content else ""
                    
                    tool_triggered = False
                    active_tool_call = None
                    
                    # Check 1: Check native tool_calls structure object
                    if response_message.tool_calls:
                        if isinstance(response_message.tool_calls, list):
                            active_tool_call = response_message.tool_calls[0]
                        else:
                            active_tool_call = response_message.tool_calls
                        tool_triggered = True
                    
                    # Check 2: Hardened Fallback parsing for raw text JSON output structures
                    if not tool_triggered and ("execute_powershell_admin" in content_text or "write_developer_code" in content_text or "install_python_dependencies" in content_text):
                        try:
                            # Import the explicit structural class models expected by OpenAI/Pydantic
                            from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall, Function
                            
                            # Clean and split string into potential JSON candidates
                            import re
                            json_strings = re.findall(r'\{[^{}]*\}|\{(?:[^{}]|\{[^{}]*\})*\}', content_text)
                            
                            parsed_tool_calls = []
                            for idx, json_str in enumerate(json_strings):
                                if "name" in json_str:
                                    try:
                                        json_data = json.loads(json_str)
                                        func_name = json_data.get("name") or json_data.get("function")
                                        func_args = json_data.get("arguments") or json_data.get("parameters") or json_data
                                        
                                        if func_name:
                                            # Ensure arguments parameter maps as a valid stringified JSON format
                                            arg_string = json.dumps(func_args) if isinstance(func_args, dict) else str(func_args)
                                            
                                            # Instantiate true object blueprints to fulfill Pydantic requirements
                                            native_tool_call = ChatCompletionMessageToolCall(
                                                id=f"call_{int(time.time())}_{idx}",
                                                type="function",
                                                function=Function(
                                                    name=func_name,
                                                    arguments=arg_string
                                                )
                                            )
                                            parsed_tool_calls.append(native_tool_call)
                                    except Exception as single_parse_err:
                                        print(f"Skipping malformed block segment: {single_parse_err}")

                            if parsed_tool_calls:
                                # Bind the correct object arrays straight to the message footprints
                                response_message.tool_calls = parsed_tool_calls
                                
                                # Execute the first intercepted command payload down the security channel
                                active_tool_call = parsed_tool_calls[0]
                                tool_triggered = True
                                
                        except Exception as fallback_err:
                            print(f"Type-safe fallback extraction failed: {fallback_err}")


                    # Append response footprint to session history context
                    self.session_messages.append(response_message)

                    if tool_triggered and active_tool_call:
                        # Display structural gate panel explicitly
                        self.process_agent_tool(active_tool_call)
                        
                        # --- SAFE AUDIO ANNOUNCEMENT ---
                        # Instead of reading the JSON out loud, speak a clean notification sentence
                        self.speak("System action requested. Please review and approve.")
                        
                        self.update_status("Awaiting Tool Clearance...", "#E67E22")
                        result_event = self.master_queue.get()

                        # Safely map the return log payload back to the AI memory context frame arrays
                        self.session_messages.append({
                            "role": "tool",
                            "tool_call_id": active_tool_call.id,
                            "name": active_tool_call.function.name,
                            "content": result_event["content"]
                        })

                        if result_event.get("is_error"):
                            self.update_transcript("[Agentic Auto-Correction Loop Activated 🔄]")
                            continue
                        break

                    if content_text:
                        # Prevent the engine from reading raw braces out loud if dumped as string
                        if content_text.strip().startswith("{") and "name" in content_text:
                            self.speak("Running infrastructure deployment sequence.")
                        else:
                            self.update_transcript(f"AI: {content_text}")
                            self.speak(content_text)
                        break

                except Exception as e:
                    self.update_transcript(f"[Ollama Error] Thread connection lost: {e}")
                    break
            
            # Post-execution cooldown period to let TTS engine fully finish speaking
            # before the microphone restarts listening
            time.sleep(1.5)

        self.update_status("Ready")

    def process_agent_tool(self, tool_call):
        func_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)

        if func_name == "write_developer_code":
            display_text = (
                f"Action: Deploy Code File\n"
                f"Path: {args.get('filepath')}\n"
                f"Content Snippet:\n{args.get('content', '')[:120]}..."
            )
            callback = lambda: self.execute_write_file(
                args.get("filepath"), args.get("content")
            )
        elif func_name == "execute_powershell_admin":
            display_text = (
                f"Action: Run PowerShell Command\nCommand: {args.get('command')}"
            )
            callback = lambda: self.execute_powershell(args.get("command"))
        elif func_name == "install_python_dependencies":
            display_text = (
                f"Action: Python Package Installer (Pip)\n"
                f"Modules to Install: {args.get('modules')}"
            )
            callback = lambda: self.execute_pip_install(args.get("modules"))
        else:
            return

        self.pending_tool_call = display_text
        self.pending_callback = callback
        self.gate_cmd_display.configure(text=display_text)
        self.gate_frame.grid()
        self.update_status("Awaiting Security Clearance ⚠️", "#E74C3C")
        self.speak("System operation requested. Review and approve.")

    def approve_action(self):
        if self.pending_callback:
            self.update_status("Executing tool...", "#2980B9")
            threading.Thread(target=self.pending_callback, daemon=True).start()
            self.clear_security_gate()

    def deny_action(self):
        self.update_transcript("[Security Gate] Action explicitly rejected by user.")
        self.clear_security_gate()
        self.update_status("Ready")
        self.master_queue.put(
            {
                "type": "tool_result",
                "content": "Action rejected by user.",
                "is_error": False,
            }
        )

    def clear_security_gate(self):
        self.pending_tool_call = None
        self.pending_callback = None
        self.gate_frame.grid_remove()

    # ---------- Tool Implementations ----------

    def execute_write_file(self, filepath, content):
        try:
            # Basic sanitization
            filepath = (
                filepath.replace("<", "")
                .replace(">", "")
                .replace('"', "")
                .replace("'", "")
            )

            home_dir = os.path.expanduser("~").replace("\\", "/")
            placeholders = ["YourUsername", "YourName", "yourusername", "username", "YourUserName"]
            for p in placeholders:
                if p in filepath:
                    filepath = filepath.replace(f"C:/Users/{p}", home_dir).replace(
                        p, os.path.basename(home_dir)
                    )

            if filepath.startswith("Desktop/"):
                filepath = f"{home_dir}/{filepath}"

            # Ensure directory exists
            dir_name = os.path.dirname(filepath)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            self.update_transcript(f"[Success] Code written to: {filepath}")
            self.master_queue.put(
                {
                    "type": "tool_result",
                    "content": f"File deployed successfully to {filepath}",
                    "is_error": False,
                }
            )
        except Exception as e:
            self.update_transcript(f"[File Error] {e}")
            self.master_queue.put(
                {
                    "type": "tool_result",
                    "content": f"File writing failed: {e}",
                    "is_error": True,
                }
            )

    def execute_powershell(self, command):
        try:
            self.update_transcript(f"[Executing Command]: {command}")
            process = subprocess.Popen(
                ["powershell", "-Command", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate()
            output = stdout if stdout else stderr
            clean_output = output.strip() if output else "Command finished with empty log."

            self.update_transcript(f"[Terminal Log]:\n{clean_output}")

            error_indicators = [
                "Bad option",
                "is not recognized",
                "TerminatingError",
                "FullyQualifiedErrorId",
                "ModuleNotFoundError",
                "No module named",
            ]
            is_error = any(ind in clean_output for ind in error_indicators) or bool(stderr)

            self.master_queue.put(
                {
                    "type": "tool_result",
                    "content": clean_output,
                    "is_error": is_error,
                }
            )
        except Exception as e:
            self.update_transcript(f"[Execution Error] {e}")
            self.master_queue.put(
                {
                    "type": "tool_result",
                    "content": str(e),
                    "is_error": True,
                }
            )

    def execute_pip_install(self, modules):
        try:
            cmd = f"python -m pip install {modules}"
            self.update_transcript(f"[Executing Package Installer]: {cmd}")
            process = subprocess.Popen(
                ["cmd.exe", "/c", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate()
            output = stdout if stdout else stderr
            clean_output = output.strip() if output else "Pip finished with no output."

            self.update_transcript(f"[Pip Engine Log]:\n{clean_output}")
            self.master_queue.put(
                {
                    "type": "tool_result",
                    "content": f"Pip installation log:\n{clean_output}",
                    "is_error": False,
                }
            )
        except Exception as e:
            self.update_transcript(f"[Pip Error] {e}")
            self.master_queue.put(
                {
                    "type": "tool_result",
                    "content": f"Pip failed: {e}",
                    "is_error": True,
                }
            )

    def speak(self, text):
        if not self.is_running:
            return

        self.update_status("Speaking...", "#3498DB")

        def run_speech():
            with self.speech_lock:
                try:
                    self.tts_engine.say(text)
                    self.tts_engine.runAndWait()
                except Exception:
                    pass
                finally:
                    if self.is_running:
                        self.update_status("Listening... Speak now", "#2ECC71")

        threading.Thread(target=run_speech, daemon=True).start()


if __name__ == "__main__":
    app = AgenticVoiceAssistant()
    app.mainloop()