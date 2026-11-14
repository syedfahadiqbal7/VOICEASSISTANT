# tools/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]: ...

    @property
    def help(self) -> Dict[str, str]:
        """
        Optional help metadata.
        Return dict with keys: summary, usage, examples
        """
        return {
            "summary": self.description,
            "usage": "No detailed usage provided.",
            "examples": "No examples provided.",
        }

    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        ...

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }