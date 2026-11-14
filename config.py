# config.py
from pathlib import Path

BASE_DIR = Path(__file__).parent
HISTORY_DIR = BASE_DIR / "history"
HISTORY_DIR.mkdir(exist_ok=True)
HISTORY_FILE = HISTORY_DIR / "conversation_history.json"
AUDIT_LOG_FILE = HISTORY_DIR / "audit_log.json"
PLUGINS_DIR = BASE_DIR / "plugins"
PLUGINS_DIR.mkdir(exist_ok=True)
EXPORTS_DIR = BASE_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)

# Audio
SILENCE_THRESHOLD = 0.01
SILENCE_LIMIT = 1.8
MAX_DURATION = 25.0
MIN_START_DELAY = 1.0
SAMPLE_RATE = 16000
AUDIO_FILE = "gui_input.wav"

# LLM
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_API_KEY = "ollama"
DEFAULT_MODEL = "qwen2.5-coder:32b"
AVAILABLE_MODELS = [
    "qwen2.5-coder:32b",
    "qwen2.5-coder:14b",
    "llama3.1:8b",
    "deepseek-coder-v2:16b",
    "mistral:7b",
]

SYSTEM_PROMPT = (
    "You are a hybrid Senior Software Developer and Infrastructure Solutions Architect "
    "running on Windows 11. Use your available tools immediately to complete requests. "
    "If a terminal log returns an error about a missing python module, call the "
    "install_python_dependencies tool. If a command flags a syntax error, adjust it "
    "natively for Windows environment parameters and retry. "
    "When Dry-Run mode is enabled, prefer explaining what a command would do instead of executing it.\n\n"
    "CRITICAL RULES FOR FILE CREATION:\n"
    "- When user asks to create a file, use the exact filename and extension they requested.\n"
    "- If user says 'create abc.txt', the filepath MUST end with abc.txt\n"
    "- Never create .bat, .cmd or .exe files unless the user explicitly asks for a batch/script file.\n"
    "- To create a folder + file: first ensure the folder path is in the filepath, "
    "the tool will automatically create parent folders.\n"
    "- Example: User says 'create folder test on desktop and file hello.txt with content Hi' "
    "→ filepath should be like 'C:/Users/.../Desktop/test/hello.txt' and content 'Hi'"
    "\n\nCRITICAL RULES FOR FILE CONTENT:\n"
    "- The 'content' field must contain ONLY the actual text that should be inside the file.\n"
    "- NEVER put shell commands like 'echo ... > file.txt' inside the content.\n"
    "- Example: User says write Fahad inside abc.txt → content must be exactly: Fahad\n"
    "- Do not wrap content in quotes unless the user wants quotes inside the file."
)

HALLUCINATION_KEYWORDS = ["人民銀行", "Thank you", "you", "晶晶", "谢谢", "Spoken"]

WINDOW_TITLE = "Agentic AI Architect & IT Admin Assistant"
WINDOW_SIZE = "900x820"