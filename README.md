# Voice Assistant — Desktop Agent + Flutter Companion

A Windows-oriented local AI assistant with a CustomTkinter desktop UI, Ollama-compatible LLM integration, planning and tool execution, security approvals, local history/memory, scheduled jobs, plugins, audio input/output, a FastAPI/WebSocket mobile bridge, and a Flutter companion app.

> **Project status:** hardened development release. Python regression checks are included in CI. Windows GUI/audio/Ollama runtime and mobile device testing still require validation on the target machine. Review [WORLD_CLASS_AUDIT_REPORT.md](WORLD_CLASS_AUDIT_REPORT.md) and [SECURITY.md](SECURITY.md) before enabling automation or LAN access.

## Repository layout

- `main.py`, `ui/` — Windows desktop application and operational viewers.
- `agent/` — conversation orchestration, planner/executor, policy, verification, undo, memory, history, scheduler, skills and LLM client.
- `tools/`, `plugins/` — built-in tools and manifest-verified plugin tools.
- `audio/` — microphone capture, Faster-Whisper speech-to-text and pyttsx3 text-to-speech.
- `mobile_api/` — local FastAPI/WebSocket bridge.
- `voice_assistant_mobile/` — Flutter Android/iOS/desktop companion source.
- `tests/` — security/reliability regressions and Phase 0 smoke checks.

## Requirements

- Windows 10/11, 64-bit (primary supported desktop target).
- **Python 3.11 (64-bit)** recommended.
- [Ollama for Windows](https://ollama.com/) for local LLM inference.
- A model compatible with Ollama. The default is `qwen2.5-coder:32b` in `config.py`; this model requires substantial RAM/VRAM. Choose a smaller model in `config.py` if required.
- For speech: a working microphone/speaker. Faster-Whisper may download its model on first use.
- For OCR: install the Tesseract OCR desktop application separately; the Python `pytesseract` package alone is not sufficient.
- For the mobile app: Flutter stable (Dart SDK version constrained by `voice_assistant_mobile/pubspec.yaml`) and Android SDK for Android builds.

## Quick start — Windows

Open PowerShell in the repository root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_windows.ps1
.\.venv\Scripts\python.exe main.py
```

The script creates `.venv` and installs the **complete Python runtime dependency set** from `requirements.txt`.

Manual installation:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

For contributors and test tooling, use:

```powershell
.\setup_windows.ps1 -Development
```

This installs `requirements-dev.txt` (runtime dependencies plus pytest).

### Ollama setup

Install and start Ollama, then pull the model selected in `config.py`, for example:

```powershell
ollama pull qwen2.5-coder:32b
ollama list
```

If your machine cannot comfortably run the default model, edit `DEFAULT_MODEL` in `config.py` and select an installed smaller model. This project does not silently switch models.

### Optional cloud failover

Local Ollama is the default. Cloud failover is optional and disabled by default. Copy `history/llm_failover.example.json` to `history/llm_failover.json`, enable failover only if needed, and set `VOICEASSISTANT_CLOUD_API_KEY` in your user environment. **Never commit API keys or real `.env` files.**

## Mobile companion

```powershell
cd voice_assistant_mobile
flutter pub get
flutter analyze
flutter test
flutter build apk
```

By default, the desktop mobile API listens on `127.0.0.1`, so it is local-only. For trusted private-LAN pairing, configure `VOICEASSISTANT_MOBILE_HOST=0.0.0.0` and a random `VOICEASSISTANT_MOBILE_TOKEN` with at least 32 characters before launching the desktop app. Follow [MOBILE_SECURITY_SETUP.md](MOBILE_SECURITY_SETUP.md). Direct LAN mode uses `ws://`; use a VPN or TLS-terminating `wss://` reverse proxy on untrusted networks. Never expose port 8765 directly to the public internet.

## Tests

```powershell
python -m pytest -q tests/test_security_reliability_regressions.py tests/test_scheduler_reliability.py tests/test_plugin_marketplace_security.py
python tests/phase0_smoke_tests.py
```

GitHub Actions runs the Python regression suite and Flutter analysis/tests/build on pushes, pull requests and version tags. Version tags (`v*`, e.g. `v1.0.0`) create a GitHub Release with source and Android APK artifacts.

## Runtime data and privacy

Conversation history, memory, audit logs, policy, schedules, undo stacks and exports are local runtime data. They are intentionally excluded from Git. The application creates its runtime directories/files as needed. Scheduled jobs use persisted execution states and retry failed handlers up to three times. Delivery is at-least-once: a crash after an external side effect but before its completion checkpoint can replay the handler, so side-effecting scheduled operations must be idempotent. Do not commit real user history, credentials, generated reports containing private data, or machine-specific settings.

## Documentation

- [Comprehensive engineering audit](WORLD_CLASS_AUDIT_REPORT.md)
- [Mobile security setup](MOBILE_SECURITY_SETUP.md)
- [Plugin authoring guide](tests/PLUGIN_AUTHORING.md)
- [Plugin security model](docs/PLUGIN_SECURITY_MODEL.md)
- [Help](HELP.md)

## License

No open-source license has been assigned in this repository. Unless the repository owner adds a license, normal copyright restrictions apply; do not assume permission to redistribute or reuse the code.
