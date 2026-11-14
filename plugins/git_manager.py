# plugins/git_manager.py
from tools.base import BaseTool
from typing import Any, Dict
import subprocess
import os


class GitManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "git_manager"

    @property
    def description(self) -> str:
        return "Perform Git operations: status, add, commit, push, pull, branch, log. Use action parameter."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "One of: status, add, commit, push, pull, branch, log, diff",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (required for commit)",
                },
                "path": {
                    "type": "string",
                    "description": "Optional repo path. Defaults to current directory.",
                },
            },
            "required": ["action"],
        }

    def execute(self, action: str, message: str = "", path: str = ".") -> Dict[str, Any]:
        try:
            cwd = path if os.path.isdir(path) else "."
            action = action.lower().strip()

            if action == "status":
                cmd = ["git", "status"]
            elif action == "add":
                cmd = ["git", "add", "."]
            elif action == "commit":
                if not message:
                    return {"content": "Commit message is required", "is_error": True}
                cmd = ["git", "commit", "-m", message]
            elif action == "push":
                cmd = ["git", "push"]
            elif action == "pull":
                cmd = ["git", "pull"]
            elif action == "branch":
                cmd = ["git", "branch", "-a"]
            elif action == "log":
                cmd = ["git", "log", "--oneline", "-n", "15"]
            elif action == "diff":
                cmd = ["git", "diff"]
            else:
                return {"content": f"Unknown action: {action}", "is_error": True}

            result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
            output = result.stdout.strip() or result.stderr.strip() or "Command completed with no output."
            return {"content": output, "is_error": result.returncode != 0}
        except Exception as e:
            return {"content": str(e), "is_error": True}