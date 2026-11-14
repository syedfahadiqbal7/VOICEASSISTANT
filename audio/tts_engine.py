# audio/tts_engine.py
import threading
import pyttsx3


class TTSEngine:
    def __init__(self):
        self.engine = None
        self.lock = threading.Lock()
        self.is_running = True
        self._stop_requested = False

    def initialize(self):
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", 185)
        voices = self.engine.getProperty("voices")
        if len(voices) > 1:
            self.engine.setProperty("voice", voices[1].id)

    def speak(self, text: str, on_finished=None):
        if not self.is_running or not self.engine:
            return

        self._stop_requested = False

        def _run():
            with self.lock:
                try:
                    self.engine.say(text)
                    self.engine.runAndWait()
                except Exception:
                    pass
                finally:
                    if on_finished and not self._stop_requested:
                        on_finished()

        threading.Thread(target=_run, daemon=True).start()

    def stop(self):
        """Interrupt current speech."""
        self._stop_requested = True
        try:
            if self.engine:
                self.engine.stop()
        except Exception:
            pass

    def shutdown(self):
        self.is_running = False
        self.stop()