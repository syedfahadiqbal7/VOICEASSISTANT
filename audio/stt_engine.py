# audio/stt_engine.py
from faster_whisper import WhisperModel
from config import HALLUCINATION_KEYWORDS


class STTEngine:
    def __init__(self):
        self.model = None

    def initialize(self):
        self.model = WhisperModel("base", device="cpu", compute_type="int8")

    def transcribe(self, audio_data) -> str | None:
        if self.model is None:
            raise RuntimeError("STT model not initialized")

        segments, _ = self.model.transcribe(audio_data, beam_size=5)
        text = "".join(segment.text for segment in segments).strip()

        if not text:
            return None

        if any(k in text for k in HALLUCINATION_KEYWORDS) and len(text) < 12:
            return None

        return text