# agent/history_manager.py
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
import threading
import shutil
from agent.storage import atomic_write_json, load_json
from agent.data_protection import DataProtectionError
from config import HISTORY_FILE, AUDIT_LOG_FILE, EXPORTS_DIR


class HistoryManager:
    def __init__(self):
        self.history_file = HISTORY_FILE
        self.audit_file = AUDIT_LOG_FILE
        self._lock = threading.RLock()

    def load_conversation(self) -> List[Dict[str, Any]]:
        if not self.history_file.exists():
            return []
        try:
            data = load_json(self.history_file, encrypt=True)
            if not isinstance(data, dict) or not isinstance(data.get("messages", []), list):
                raise ValueError("conversation history must be an object with a messages list")
            return data.get("messages", [])
        except DataProtectionError:
            # Never turn an authentication/key failure into an empty history.
            raise
        except Exception as e:
            # Preserve malformed legacy history before a later successful save replaces it.
            try:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                backup = self.history_file.with_name(f"{self.history_file.name}.corrupt.{stamp}.bak")
                shutil.copy2(self.history_file, backup)
                print(f"[History] Corrupt conversation preserved at {backup}: {e}")
            except Exception as backup_error:
                print(f"[History] Load failed and backup failed: {e}; {backup_error}")
            return []

    def save_conversation(self, messages: List[Dict[str, Any]]):
        trimmed = messages[-40:] if len(messages) > 40 else messages
        payload = {
            "updated_at": datetime.now().isoformat(),
            "messages": trimmed,
        }
        with self._lock:
            atomic_write_json(self.history_file, payload, indent=2, ensure_ascii=False, encrypt=True)

    def clear_conversation(self):
        if self.history_file.exists():
            self.history_file.unlink()

    def log_audit(self, action: str, details: str, approved: bool, result: str = ""):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
            "approved": approved,
            "result": result,
        }
        with self._lock:
            logs = []
            if self.audit_file.exists():
                try:
                    loaded = load_json(self.audit_file, encrypt=True)
                    if not isinstance(loaded, list):
                        raise ValueError("audit log must be a JSON array")
                    logs = loaded
                except DataProtectionError:
                    raise
                except Exception as e:
                    # Do not overwrite a malformed/corrupt audit trail.
                    raise ValueError(f"Refusing to append to an unreadable audit log: {e}") from e
            logs.append(entry)
            atomic_write_json(self.audit_file, logs[-200:], indent=2, ensure_ascii=False, encrypt=True)

    def get_audit_log(self) -> List[Dict]:
        if not self.audit_file.exists():
            return []
        data = load_json(self.audit_file, default=[], encrypt=True)
        if not isinstance(data, list):
            raise ValueError("audit log must be a JSON array")
        return data

    def export_conversation(self, messages: List[Dict[str, Any]], format: str = "md") -> str:
        """Export conversation to Markdown or Text file. Returns the file path."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if format == "md":
            filename = EXPORTS_DIR / f"conversation_{timestamp}.md"
            content = self._to_markdown(messages)
        else:
            filename = EXPORTS_DIR / f"conversation_{timestamp}.txt"
            content = self._to_text(messages)

        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        return str(filename)

    def _to_markdown(self, messages: List[Dict]) -> str:
        lines = ["# Conversation Export\n"]
        lines.append(f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        for msg in messages:
            role = msg.get("role", "unknown")
            if role == "system":
                continue
            content = msg.get("content", "")
            if role == "user":
                lines.append(f"### 👤 You\n{content}\n")
            elif role == "assistant":
                lines.append(f"### 🤖 Assistant\n{content}\n")
            elif role == "tool":
                name = msg.get("name", "tool")
                lines.append(f"### 🛠️ Tool Result (`{name}`)\n```\n{content}\n```\n")
        return "\n".join(lines)

    def _to_text(self, messages: List[Dict]) -> str:
        lines = ["Conversation Export", "=" * 40, ""]
        for msg in messages:
            role = msg.get("role", "unknown")
            if role == "system":
                continue
            content = msg.get("content", "")
            lines.append(f"[{role.upper()}]\n{content}\n")
        return "\n".join(lines)