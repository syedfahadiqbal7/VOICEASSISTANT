# audio/recorder.py
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
from config import (
    SILENCE_THRESHOLD,
    SILENCE_LIMIT,
    MAX_DURATION,
    MIN_START_DELAY,
    SAMPLE_RATE,
    AUDIO_FILE,
)


class AudioRecorder:
    def record(self, is_running_flag) -> np.ndarray | None:
        audio_buffer = []
        silence_duration = 0.0
        has_spoken = False
        start_time = time.time()

        def callback(indata, frames, time_info, status):
            nonlocal silence_duration, has_spoken
            volume = np.sqrt(np.mean(indata**2))
            audio_buffer.append(indata.copy())

            if (time.time() - start_time) < MIN_START_DELAY:
                return

            if volume > SILENCE_THRESHOLD:
                has_spoken = True
                silence_duration = 0.0
            elif has_spoken:
                silence_duration += frames / SAMPLE_RATE

        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, callback=callback):
                while is_running_flag():
                    sd.sleep(100)
                    if has_spoken and silence_duration >= SILENCE_LIMIT:
                        break
                    if not has_spoken and (time.time() - start_time) > 7.0:
                        break
                    if (time.time() - start_time) > MAX_DURATION:
                        break
        except Exception as e:
            raise RuntimeError(f"Audio Error: {e}")

        if not audio_buffer or not has_spoken:
            return None

        recording = np.concatenate(audio_buffer, axis=0)
        sf.write(AUDIO_FILE, recording, SAMPLE_RATE)

        audio_data, _ = sf.read(AUDIO_FILE, dtype="float32")
        if np.max(np.abs(audio_data)) < 0.02:
            return None

        return audio_data