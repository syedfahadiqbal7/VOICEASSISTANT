import os
import sys
import time
import threading
import numpy as np
import pyttsx3
import sounddevice as sd
import soundfile as sf
import customtkinter as ctk
from faster_whisper import WhisperModel
from openai import OpenAI

# Set modern dark theme aesthetics
ctk.set_appearance_mode("System")  # Inherits Windows Dark/Light mode
ctk.set_default_color_theme("blue")  # Modern tech blue accent

class VoiceAssistantGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- Window Layout Configuration ---
        self.title("Local AI Voice Assistant")
        self.geometry("600x550")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- Backend Core Initialization ---
        self.client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        
        # Audio configuration properties
        self.SILENCE_THRESHOLD = 0.01  
        self.SILENCE_LIMIT = 1.8       
        self.MAX_DURATION = 20.0       
        self.MIN_START_DELAY = 1.0     
        
        self.is_running = False

        # Status Label
        self.status_label = ctk.CTkLabel(
            self, text="Loading Engine Engines...", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.status_label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        # Clean Visual Divider
        self.separator = ctk.CTkFrame(self, height=2, fg_color="gray30")
        self.separator.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        # Chat Transcript Area
        self.transcript_area = ctk.CTkTextbox(
            self, font=ctk.CTkFont(size=13), wrap="word", activate_scrollbars=True
        )
        self.transcript_area.grid(row=2, column=0, padx=20, pady=10, sticky="nsew")
        self.transcript_area.insert("0.0", "System Initializing...\n")
        self.transcript_area.configure(state="disabled")

        # Interactive Control Button
        self.action_button = ctk.CTkButton(
            self, text="Start Assistant", font=ctk.CTkFont(size=15, weight="bold"),
            height=45, command=self.toggle_assistant_thread
        )
        self.action_button.grid(row=3, column=0, padx=20, pady=20, sticky="ew")

        # Async Initialization to prevent app startup freeze
        threading.Thread(target=self.initialize_engines, daemon=True).start()

    def initialize_engines(self):
        self.update_transcript("[System] Loading Speech-to-Text Model (Whisper base)...")
        try:
            self.stt_model = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as e:
            self.update_transcript(f"[Error] Failed to load Whisper: {e}")
            return

        self.update_transcript("[System] Binding Windows Text-to-Speech Engine...")
        self.tts_engine = pyttsx3.init()
        self.tts_engine.setProperty('rate', 185)
        
        # Apply female voice profile (Index 1) as verified by your script output
        voices = self.tts_engine.getProperty('voices')
        if len(voices) > 1:
            self.tts_engine.setProperty('voice', voices[1].id)

        self.update_status("Ready")
        self.update_transcript("[System] All models ready. Click 'Start Assistant' to begin speaking.")

    # --- Thread Safe Helper Utilities ---
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
        audio_file = "gui_input.wav"
        
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
                
                elapsed = time.time() - start_time
                if elapsed < self.MIN_START_DELAY:
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
                        elapsed_time = time.time() - start_time
                        
                        if has_spoken and silence_duration >= self.SILENCE_LIMIT:
                            break
                        if not has_spoken and elapsed_time > 7.0:
                            break
                        if elapsed_time > self.MAX_DURATION:
                            break
            except Exception as e:
                self.update_transcript(f"[Audio Error] {e}")
                break

            if not self.is_running:
                break

            if not audio_buffer or not has_spoken:
                self.update_status("No speech detected. Retrying...")
                time.sleep(1)
                continue

            # Step 2: Speech-To-Text processing
            self.update_status("Processing speech...", "#F39C12")
            recording = np.concatenate(audio_buffer, axis=0)
            sf.write(audio_file, recording, 16000)

            try:
                audio_data, _ = sf.read(audio_file, dtype='float32')
                segments, _ = self.stt_model.transcribe(audio_data, beam_size=5)
                user_text = "".join([segment.text for segment in segments]).strip()
            except Exception as e:
                self.update_transcript(f"[STT Error] {e}")
                continue

            if not user_text:
                continue

            self.update_transcript(f"You: {user_text}")

            # Step 3: Local Ollama Generation
            self.update_status("Ollama is thinking...", "#9B59B6")
            try:
                response = self.client.chat.completions.create(
                    model="llama3:latest",
                    messages=[{"role": "user", "content": user_text}]
                )
                ai_response = response.choices[0].message.content
            except Exception as e:
                self.update_transcript(f"[Ollama Error] Could not connect: {e}")
                continue

            self.update_transcript(f"AI: {ai_response}")

            # Step 4: Text-To-Speech Playback
            if self.is_running:
                self.update_status("Speaking...", "#3498DB")
                self.tts_engine.say(ai_response)
                self.tts_engine.runAndWait()

        self.update_status("Ready")

if __name__ == "__main__":
    app = VoiceAssistantGUI()
    app.mainloop()
