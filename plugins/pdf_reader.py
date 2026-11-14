# plugins/pdf_reader.py
from tools.base import BaseTool
from typing import Any, Dict


class PDFReaderTool(BaseTool):
    @property
    def name(self) -> str:
        return "pdf_reader"

    @property
    def description(self) -> str:
        return "Extract text from a PDF file."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "max_pages": {"type": "integer", "description": "Max pages to read (default 5)"},
            },
            "required": ["filepath"],
        }

    def execute(self, filepath: str, max_pages: int = 5) -> Dict[str, Any]:
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(filepath)
            text = []
            for i, page in enumerate(reader.pages[:max_pages]):
                text.append(f"--- Page {i+1} ---\n{page.extract_text()}")
            return {"content": "\n\n".join(text), "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}