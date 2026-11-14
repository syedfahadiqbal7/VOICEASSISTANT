# templates/custom_tool_template.py
"""
===============================================
  CUSTOM TOOL TEMPLATE
===============================================
1. Copy this file into the `plugins/` folder
2. Rename it (e.g. my_tool.py)
3. Change the class name and implement the methods
4. Restart the application
5. Your tool will be automatically available
"""

from tools.base import BaseTool
from typing import Any, Dict


class MyCustomTool(BaseTool):
    """Replace this with a clear description of what your tool does."""

    @property
    def name(self) -> str:
        # Unique name (no spaces). Example: "send_email", "read_csv"
        return "my_custom_tool"

    @property
    def description(self) -> str:
        # This description is shown to the AI. Be clear and specific.
        return "Describe what this tool does so the AI knows when to use it."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "Explanation of param1",
                },
                # Add more parameters as needed
            },
            "required": ["param1"],
        }

    def execute(self, param1: str, **kwargs) -> Dict[str, Any]:
        """
        Main logic of your tool.
        Always return a dictionary with:
            - content: str
            - is_error: bool
        """
        try:
            # --------- Your code here ---------
            result = f"Tool executed successfully with param1 = {param1}"
            # ----------------------------------

            return {
                "content": result,
                "is_error": False,
            }
        except Exception as e:
            return {
                "content": f"Tool failed: {str(e)}",
                "is_error": True,
            }