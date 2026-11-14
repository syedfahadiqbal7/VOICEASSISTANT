# plugins/example_hello_tool.py
from tools.base import BaseTool
from typing import Any, Dict


class HelloWorldTool(BaseTool):
    @property
    def name(self) -> str:
        return "say_hello"

    @property
    def description(self) -> str:
        return "A simple example tool that greets a person by name."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the person to greet",
                }
            },
            "required": ["name"],
        }

    def execute(self, name: str) -> Dict[str, Any]:
        return {
            "content": f"Hello {name}! This message came from a custom plugin tool.",
            "is_error": False,
        }