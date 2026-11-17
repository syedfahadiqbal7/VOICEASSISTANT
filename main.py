# main.py
from agent.observability import configure_logging

configure_logging()

import logging
import customtkinter as ctk
from ui.app_window import AgenticVoiceAssistant

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

if __name__ == "__main__":
    try:
        app = AgenticVoiceAssistant()
        app.mainloop()
    except Exception:
        logging.getLogger("voiceassistant.startup").exception(
            "Desktop application terminated unexpectedly",
            extra={"event": "application.unhandled_exception"},
        )
        raise
