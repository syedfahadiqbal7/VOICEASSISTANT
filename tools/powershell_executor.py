# tools/powershell_executor.py
import subprocess
from typing import Any, Dict
from .base import BaseTool


class PowerShellExecutorTool(BaseTool):
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    @property
    def name(self) -> str:
        return "execute_powershell_admin"

    @property
    def description(self) -> str:
        return "Executes Windows PowerShell commands for infrastructure configuration, deployment, diagnostics or script execution."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The exact PowerShell string command.",
                }
            },
            "required": ["command"],
        }

    def execute(self, command: str) -> Dict[str, Any]:
        if self.dry_run:
            return {
                "content": (
                    f"[DRY-RUN MODE] Command was NOT executed.\n\n"
                    f"Command that would have run:\n{command}\n\n"
                    f"This is a simulation only. Disable Dry-Run to execute for real."
                ),
                "is_error": False,
            }

        try:
            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                return {
                    "content": f"PowerShell command timed out after 120 seconds and was terminated.\n{(stdout or '').strip()}\n{(stderr or '').strip()}".strip(),
                    "is_error": True,
                    "exit_code": process.returncode,
                    "timed_out": True,
                }

            stdout_text = (stdout or "").strip()
            stderr_text = (stderr or "").strip()
            output = "\n".join(part for part in (stdout_text, stderr_text) if part)
            clean_output = output or "Command finished with empty output."
            exit_code = process.returncode
            return {
                "content": clean_output,
                "is_error": exit_code != 0,
                "exit_code": exit_code,
                "stderr": stderr_text,
            }
        except Exception as e:
            return {"content": str(e), "is_error": True}