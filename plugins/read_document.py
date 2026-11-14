# plugins/read_document.py
"""Extract text from local documents (.docx, .txt, .md, .pdf best-effort)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict

from tools.base import BaseTool


class ReadDocumentTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_document"

    @property
    def description(self) -> str:
        return (
            "Read/extract text from a local document for analysis. "
            "Supports .docx, .txt, .md, and .pdf (if pypdf/pdfminer available). "
            "Use for CV audit, resume review, reading notes. "
            "Pass full filepath. Do NOT invent a tool named extract_text — use read_document."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Full path to the document, e.g. C:/Users/You/Desktop/CV.docx",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters to return (default 12000, max 50000)",
                },
            },
            "required": ["filepath"],
        }

    def _sanitize(self, filepath: str) -> str:
        fp = (filepath or "").strip().strip('"').strip("'")
        fp = fp.replace("\\", "/")
        home = os.path.expanduser("~").replace("\\", "/")
        if fp.startswith("Desktop/") or fp.startswith("desktop/"):
            fp = f"{home}/Desktop/{fp.split('/', 1)[-1]}"
        return fp

    def execute(self, filepath: str = "", max_chars: int = 12000, **kwargs) -> Dict[str, Any]:
        try:
            max_chars = max(500, min(int(max_chars or 12000), 50000))
        except Exception:
            max_chars = 12000
        path = self._sanitize(filepath)
        if not path:
            return {"content": "filepath is required", "is_error": True}
        p = Path(path)
        if not p.is_file():
            # try Windows path as-is
            p2 = Path(filepath.strip().strip('"'))
            if p2.is_file():
                p = p2
            else:
                return {
                    "content": f"File not found: {path}",
                    "is_error": True,
                }

        ext = p.suffix.lower()
        try:
            if ext == ".docx":
                text = self._read_docx(p)
            elif ext in (".txt", ".md", ".csv", ".log", ".json"):
                text = p.read_text(encoding="utf-8", errors="ignore")
            elif ext == ".pdf":
                text = self._read_pdf(p)
            else:
                # best effort utf-8
                text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return {"content": f"Failed to read {p}: {e}", "is_error": True}

        text = (text or "").strip()
        if not text:
            return {
                "content": f"No extractable text in {p.name} (empty or unsupported format).",
                "is_error": True,
            }
        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n…[truncated]…"
            truncated = True
        header = f"Document: {p}\nCharacters: {len(text)}{' (truncated)' if truncated else ''}\n\n"
        return {"content": header + text, "is_error": False}

    def _read_docx(self, path: Path) -> str:
        try:
            from docx import Document  # type: ignore
        except ImportError:
            raise RuntimeError("python-docx not installed. Run: pip install python-docx")
        doc = Document(str(path))
        parts = []
        for para in doc.paragraphs:
            t = (para.text or "").strip()
            if t:
                parts.append(t)
        # tables
        for table in doc.tables:
            for row in table.rows:
                cells = [((c.text or "").strip()) for c in row.cells]
                line = " | ".join(cells)
                if line.strip(" |"):
                    parts.append(line)
        return "\n".join(parts)

    def _read_pdf(self, path: Path) -> str:
        # try pypdf then pdfminer
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
            return "\n".join(parts)
        except Exception:
            pass
        try:
            from pdfminer.high_level import extract_text  # type: ignore

            return extract_text(str(path)) or ""
        except Exception as e:
            raise RuntimeError(
                f"PDF read failed ({e}). Install: pip install pypdf"
            )


class ExtractTextTool(ReadDocumentTool):
    """Alias so models that call extract_text still work."""

    @property
    def name(self) -> str:
        return "extract_text"

    @property
    def description(self) -> str:
        return (
            "Alias of read_document. Extract text from a local file (.docx, .txt, .md, .pdf). "
            "Prefer tool name read_document when possible."
        )
