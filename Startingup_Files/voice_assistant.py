import os
import sys
import time
import numpy as np
import pyttsx3
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from openai import OpenAI

# 1. Initialize OpenAI client pointing to local Ollama instance
client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

# 2. Initialize Whisper (STT)
print("Loading Speech-to-Text engine...")
try:
    stt_model = WhisperModel("base", device="cpu", compute_type="int8")
except Exception as e:
    print(f"Error loading Whisper: {e}")
    sys.exit(1)

# 3. Initialize Windows Native TTS Engine
print("Loading Windows Text-to-Speech engine...")
tts_engine = pyttsx3.init()
tts_engine.setProperty('rate', 180) 
# --- ADD THESE TWO LINES TO CHANGE THE VOICE ---
voices = tts_engine.getProperty('voices')
tts_engine.setProperty('voice', voices[1].id)  # Change '1' to whichever index you preferred from Step 1

def record_until_silent(filename="input.wav", fs=16000):
    print("\nListening... Speak now.")
    
    audio_buffer = []
    silence_duration = 0.0
    has_spoken = False
    
    # Configuration parameters
    SILENCE_THRESHOLD = 0.01  
    SILENCE_LIMIT = 1.8       
    MAX_DURATION = 15.0       
    MIN_START_DELAY = 1.0     
    
    start_time = time.time()

    def callback(indata, frames, time_info, status):
        nonlocal silence_duration, has_spoken
        volume = np.sqrt(np.mean(indata**2))
        audio_buffer.append(indata.copy())
        
        elapsed = time.time() - start_time
        if elapsed < MIN_START_DELAY:
            return

        if volume > SILENCE_THRESHOLD:
            has_spoken = True
            silence_duration = 0.0
        else:
            if has_spoken:
                silence_duration += frames / fs

    with sd.InputStream(samplerate=fs, channels=1, callback=callback):
        while True:
            sd.sleep(100)
            elapsed_time = time.time() - start_time
            
            if has_spoken and silence_duration >= SILENCE_LIMIT:
                print("Silence detected, stopping recording.")
                break
                
            if not has_spoken and elapsed_time > 7.0:
                print("No speech detected. Timing out.")
                return False
                
            if elapsed_time > MAX_DURATION:
                print("Maximum recording limit reached.")
                break

    if audio_buffer:
        recording = np.concatenate(audio_buffer, axis=0)
        sf.write(filename, recording, fs)
        print("Processing speech...")
        return True
    return False

def voice_assistant():
    audio_file = "input.wav"
    
    if not record_until_silent(audio_file):
        return
    
    try:
        audio_data, samplerate = sf.read(audio_file, dtype='float32')
        segments, info = stt_model.transcribe(audio_data, beam_size=5)
        user_text = "".join([segment.text for segment in segments]).strip()
    except Exception as e:
        print(f"Error processing audio data: {e}")
        return
    
    if not user_text:
        print("Didn't catch that. Try again.")
        return
        
    print(f"You said: {user_text}")
    
    print("Ollama is thinking...")
    try:
        response = client.chat.completions.create(
            model="llama3:latest", # Explicitly matched to your exact pulled model
            messages=[{"role": "user", "content": user_text}]
        )
        # FIX: Added [0] to correctly extract from the choices list
        ai_response = response.choices[0].message.content
        print(f"AI: {ai_response}")
    except Exception as e:
        print(f"Error connecting to Ollama: {e}")
        return
    
    print("Speaking...")
    tts_engine.say(ai_response)
    tts_engine.runAndWait()

if __name__ == "__main__":
    while True:
        try:
            voice_assistant()
            input("\nPress Enter to talk again (or Ctrl+C to exit)...")
        except KeyboardInterrupt:
            print("\nExiting...")
            break
