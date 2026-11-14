# tools/pip_installer.py
import subprocess
from typing import Any, Dict
from .base import BaseTool


class PipInstallerTool(BaseTool):
    @property
    def name(self) -> str:
        return "install_python_dependencies"

    @property
    def description(self) -> str:
        return "Automatically executes pip install commands to download and install missing Python packages/modules."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "modules": {
                    "type": "string",
                    "description": "Space-separated list of Python packages (e.g. 'requests beautifulsoup4 pandas').",
                }
            },
            "required": ["modules"],
        }

    def execute(self, modules: str) -> Dict[str, Any]:
        try:
            cmd = f"python -m pip install {modules}"
            process = subprocess.Popen(
                ["cmd.exe", "/c", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate()
            output = stdout if stdout else stderr
            clean_output = output.strip() if output else "Pip finished with no output."

            return {
                "content": f"Pip installation log:\n{clean_output}",
                "is_error": False,
            }
        except Exception as e:
            return {
                "content": f"Pip failed: {e}",
                "is_error": True,
            }