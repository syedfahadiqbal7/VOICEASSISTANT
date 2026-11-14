import os
import sys
import time
import json
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

        self.title("Agentic AI Developer & IT Admin Assistant")
        self.geometry("700x650")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Connect to Ollama
        self.client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        
        # Audio Settings
        self.SILENCE_THRESHOLD = 0.01  
        self.SILENCE_LIMIT = 1.8       
        self.MAX_DURATION = 25.0       
        self.MIN_START_DELAY = 1.0     
        self.is_running = False
        
        # Intercept variables for security approval gate
        self.pending_tool_call = None
        self.pending_callback = None

        # --- UI Component Definitions ---
        self.status_label = ctk.CTkLabel(self, text="Loading Systems...", font=ctk.CTkFont(size=14, weight="bold"))
        self.status_label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        self.separator = ctk.CTkFrame(self, height=2, fg_color="gray30")
        self.separator.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        self.transcript_area = ctk.CTkTextbox(self, font=ctk.CTkFont(size=13), wrap="word", activate_scrollbars=True)
        self.transcript_area.grid(row=2, column=0, padx=20, pady=10, sticky="nsew")
        self.transcript_area.insert("0.0", "Initializing Agent Core...\n")
        self.transcript_area.configure(state="disabled")

        # --- Security Gate Panel ---
        self.gate_frame = ctk.CTkFrame(self, fg_color="gray20", corner_radius=8)
        self.gate_frame.grid(row=3, column=0, padx=20, pady=5, sticky="ew")
        self.gate_frame.grid_remove() # Hidden by default
        
        self.gate_label = ctk.CTkLabel(self.gate_frame, text="SECURITY APPROVAL REQUIRED:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#FFFFFF")
        self.gate_label.pack(anchor="w", padx=15, pady=(5,0))
        
        self.gate_cmd_display = ctk.CTkLabel(self.gate_frame, text="", font=ctk.CTkFont(family="Consolas", size=12), justify="left", anchor="w")
        self.gate_cmd_display.pack(fill="x", padx=15, pady=5)
        
        self.btn_frame = ctk.CTkFrame(self.gate_frame, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=15, pady=(0,10))
        
        self.approve_btn = ctk.CTkButton(self.btn_frame, text="Approve & Execute", fg_color="#27AE60", hover_color="#219653", command=self.approve_action)
        self.approve_btn.pack(side="left", padx=(0,10))
        
        self.deny_btn = ctk.CTkButton(self.btn_frame, text="Deny / Cancel", fg_color="#C0392B", hover_color="#922B21", command=self.deny_action)
        self.deny_btn.pack(side="left")

        # Main Button
        self.action_button = ctk.CTkButton(self, text="Start Assistant", font=ctk.CTkFont(size=15, weight="bold"), height=45, command=self.toggle_assistant_thread)
        self.action_button.grid(row=4, column=0, padx=20, pady=20, sticky="ew")

        # Define schema for tools available to the AI
        self.tools_schema = [
            {
                "type": "function",
                "function": {
                    "name": "write_developer_code",
                    "description": "Writes code, scripts, or documentation directly to a file on the user's system.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string", "description": "The exact full file path where the script/file should be created (e.g. C:/Users/.../Desktop/script.py)."},
                            "content": {"type": "string", "description": "The raw code string content to write inside the file."}
                        },
                        "required": ["filepath", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_powershell_admin",
                    "description": "Executes an administrative Windows PowerShell command to manage IT infrastructure, networks, or gather diagnostics.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "The explicit PowerShell string command to execute (e.g., Get-NetIPAddress, ping google.com)."}
                        },
                        "required": ["command"]
                    }
                }
            }
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
        self.tts_engine.setProperty('rate', 185)
        voices = self.tts_engine.getProperty('voices')
        if len(voices) > 1:
            self.tts_engine.setProperty('voice', voices[1].id) # Female Voice Profile

        self.update_status("Ready")
        self.update_transcript("[System] Systems Fully Online. Speak freely.")

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
            self.action_button.configure(text="Stop Assistant", fg_color="#C0392B", hover_color="#922B21")
            threading.Thread(target=self.run_voice_loop, daemon=True).start()
        else:
            self.is_running = False
            self.action_button.configure(text="Start Assistant", fg_color=("#3B8ED0", "#1F6AA5"), hover_color=("#3278B2", "#144875"))
            self.update_status("Stopped")

        # --- Core Pipeline Execution Loop ---
    def run_voice_loop(self):
        import queue
        self.master_queue = queue.Queue()
        audio_file = "gui_input.wav"
        
        # Maintain an active session memory array so the LLM remembers previous turns
        self.session_messages = [
            {"role": "system", "content": "You are a hybrid Senior Developer and Infrastructure Architect running on a Windows 11 machine. Always invoke your tools when operations are requested. If a tool output or terminal log returns an error, analyze the output, fix your syntax flags immediately (e.g., use 'ping -n 4' instead of '-Count' for Windows ping layout), and rerun the tool with the corrected arguments."}
        ]
        
        while self.is_running:
            self.update_status("Listening... Speak now", "#2ECC71")
            
            audio_buffer = []
            silence_duration = 0.0
            has_spoken = False
            start_time = time.time()

            def callback(indata, frames, time_info, status):
                nonlocal silence_duration, has_spoken
                volume = np.sqrt(np.mean(indata**2))
                audio_buffer.append(indata.copy())
                if (time.time() - start_time) < self.MIN_START_DELAY: return
                if volume > self.SILENCE_THRESHOLD:
                    has_spoken = True
                    silence_duration = 0.0
                else:
                    if has_spoken: silence_duration += frames / 16000

            try:
                with sd.InputStream(samplerate=16000, channels=1, callback=callback):
                    while self.is_running:
                        sd.sleep(100)
                        if has_spoken and silence_duration >= self.SILENCE_LIMIT: break
                        if not has_spoken and (time.time() - start_time) > 7.0: break
                        if (time.time() - start_time) > self.MAX_DURATION: break
            except Exception as e:
                self.update_transcript(f"[Audio Error] {e}")
                break

            if not self.is_running: break
            if not audio_buffer or not has_spoken: continue

            self.update_status("Processing speech...", "#F39C12")
            recording = np.concatenate(audio_buffer, axis=0)
            sf.write(audio_file, recording, 16000)

            try:
                audio_data, _ = sf.read(audio_file, dtype='float32')
                max_amplitude = np.max(np.abs(audio_data))
                if max_amplitude < 0.02: continue

                segments, _ = self.stt_model.transcribe(audio_data, beam_size=5)
                user_text = "".join([segment.text for segment in segments]).strip()
            except Exception as e:
                self.update_transcript(f"[STT Error] {e}")
                continue

            hallucination_keywords = ["人民銀行", "Thank you", "you", "晶晶", "谢谢", "Spoken"]
            if any(keyword in user_text for keyword in hallucination_keywords) and len(user_text) < 12:
                continue

            if not user_text: continue
            self.update_transcript(f"You: {user_text}")
            
            # Append user voice input to message array
            self.session_messages.append({"role": "user", "content": user_text})

            # Process loop that allows the LLM to think, get tool execution output, and fix itself
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
                    
                    # Update memory with assistant response footprint
                    self.session_messages.append(response_message)
                    
                    tool_triggered = False
                    if response_message.tool_calls:
                        for tool_call in response_message.tool_calls:
                            self.process_agent_tool(tool_call)
                            tool_triggered = True
                    
                    if tool_triggered:
                        # Wait for background tool thread to respond via the synchronized thread queue
                        self.update_status("Awaiting Tool Execution Output...", "#E67E22")
                        result_event = self.master_queue.get()
                        
                        if result_event["type"] == "terminal_result":
                            # Feed the terminal output logs directly back into the LLM context memory array
                            self.session_messages.append({
                                "role": "tool",
                                "tool_call_id": response_message.tool_calls[0].id,
                                "name": response_message.tool_calls[0].function.name,
                                "content": result_event["content"]
                            })
                            
                            # If an error occurred, continue the while loop immediately to let the AI fix itself
                            if result_event["is_error"]:
                                self.update_transcript("[Agentic Auto-Correction Loop Activated 🔄]")
                                continue
                        self.speak("Command execution sequence complete.")
                        break # Tool ran perfectly, break out of processing step
                    
                    if content_text:
                        self.update_transcript(f"AI: {content_text}")
                        self.speak(content_text)
                    break
                    
                except Exception as e:
                    self.update_transcript(f"[Ollama Error] Connection failed: {e}")
                    break

        self.update_status("Ready")

    def process_agent_tool(self, tool_call):
        """Intercepts the tool request and presents it to the User Security Gate."""
        func_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)

        if func_name == "write_developer_code":
            display_text = (
                f"Action: Write File\nPath: {args.get('filepath')}\n"
                f"Content Snippet:\n{args.get('content')[:150]}..."
            )
            callback = lambda: self.execute_write_file(
                args.get("filepath"), args.get("content")
            )
        elif func_name == "execute_powershell_admin":
            display_text = (
                f"Action: Run PowerShell Command\nCommand: {args.get('command')}"
            )
            callback = lambda: self.execute_powershell(args.get("command"))
        else:
            return

        # Inject details into UI security gate panel and prompt user
        self.pending_tool_call = display_text
        self.pending_callback = callback
        self.gate_cmd_display.configure(text=display_text)
        self.gate_frame.grid()  # Reveal security gate pane
        self.update_status("Awaiting Security Clearance ⚠️", "#E74C3C")
        self.speak("System action requested. Please review and approve.")

    def approve_action(self):
        """Fires if user clicks 'Approve & Execute'"""
        if self.pending_callback:
            self.update_status("Executing approved tool...", "#2980B9")
            # Run execution in separate thread so GUI doesn't hang
            threading.Thread(target=self.pending_callback, daemon=True).start()
            self.clear_security_gate()

    def deny_action(self):
        """Fires if user rejects command execution"""
        self.update_transcript("[Security Gate] Execution rejected by user.")
        self.clear_security_gate()
        self.update_status("Ready")

    def clear_security_gate(self):
        self.pending_tool_call = None
        self.pending_callback = None
        self.gate_frame.grid_remove()

        # --- System Action Implementations ---
    def execute_write_file(self, filepath, content):
        try:
            # 1. Strip out illegal characters like <, >, or accidental literal quotes
            filepath = filepath.replace("<", "").replace(">", "").replace('"', '').replace("'", "")
            
            # 2. Automatically resolve true Windows profile paths
            home_dir = os.path.expanduser("~") # Returns %USERPROFILE%
            home_dir = home_dir.replace("\\", "/") # Normalize slash structure
            
            # 3. Intercept and replace placeholder strings hallucinated by the LLM
            placeholders = ["YourUsername", "YourName", "yourusername", "username", "YourUserName"]
            for placeholder in placeholders:
                if placeholder in filepath:
                    filepath = filepath.replace(f"C:/Users/{placeholder}", home_dir)
                    filepath = filepath.replace(placeholder, os.path.basename(home_dir))

            # 4. Handle cases where the model forgets absolute drive structures
            if filepath.startswith("Desktop/"):
                filepath = f"{home_dir}/{filepath}"

            self.update_transcript(f"[System Execution]: Attempting file deployment to: {filepath}")

            # Ensure folder tree targets exist, then write out the plain text arrays
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
                
            msg = f"[Success] Script written successfully to:\n{filepath}"
            self.update_transcript(msg)
            self.speak("File successfully created.")
            
        except Exception as e:
            self.update_transcript(f"[File Error] Operation failed: {e}")

    def execute_powershell(self, command):
        try:
            self.update_transcript(f"[Executing Terminal Command]: {command}")
            process = subprocess.Popen(["powershell", "-Command", command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate()
            
            # Capture both standard logs and explicit red-text terminal errors
            output = stdout if stdout else stderr
            clean_output = output.strip() if output else "Command executed with no output logs."
            self.update_transcript(f"[Terminal Log Response]:\n{clean_output}")
            
            # Check if Windows or PowerShell returned an error symptom
            is_error = False
            error_indicators = ["Bad option", "is not recognized", "TerminatingError", "FullyQualifiedErrorId", "InvalidOperation"]
            if any(indicator in clean_output for indicator in error_indicators) or stderr:
                is_error = True
            
            # Send the execution logs back to the master thread sequence
            self.master_queue.put({
                "type": "terminal_result",
                "content": clean_output,
                "is_error": is_error,
                "failed_command": command
            })
            
        except Exception as e:
            self.update_transcript(f"[OS Execution Error] {e}")
            self.master_queue.put({"type": "error", "content": str(e)})

    # Ensure only one thread tries to make the engine speak at any given moment
    speech_lock = threading.Lock()
    def speak(self, text):
        if self.is_running:
            self.update_status("Speaking...", "#3498DB")
            # Using a background thread with a lock completely fixes the 'run loop already started' error
            def run_speech():
                with self.speech_lock:
                    try:
                        self.tts_engine.say(text)
                        self.tts_engine.runAndWait()
                    except Exception as speech_err:
                        print(f"Speech loop warning handled: {speech_err}")
                    finally:
                        # Bring app status back to ready if still running
                        if self.is_running:
                            self.update_status("Listening... Speak now", "#2ECC71")

            threading.Thread(target=run_speech, daemon=True).start()


if __name__ == "__main__":
    app = AgenticVoiceAssistant()
    app.mainloop()