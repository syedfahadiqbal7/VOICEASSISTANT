# plugins/document_writer.py
"""Create real .docx and .pdf files from plain text (no manual Word paste)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _expand_path(path: str) -> Path:
    p = (path or "").strip().strip('"').strip("'")
    p = os.path.expandvars(os.path.expanduser(p))
    # Desktop/Documents relative
    if re.match(r"^(Desktop|Documents|Downloads)([/\\]|$)", p, re.I):
        home = Path.home()
        p = str(home / p.replace("/", os.sep))
    return Path(p)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _paragraphs(text: str) -> List[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n", text.strip())
    out: List[str] = []
    for block in parts:
        block = block.strip()
        if not block:
            continue
        out.append(block)
    if not out and text.strip():
        out = [text.strip()]
    return out


class _DocxPdfMixin:
    def _write_docx(self, path: Path, content: str, title: str = "") -> None:
        try:
            from docx import Document  # type: ignore
            from docx.shared import Pt  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "python-docx not installed. Run: pip install python-docx"
            ) from e

        doc = Document()
        if title:
            doc.add_heading(title, level=0)

        for block in _paragraphs(content):
            lines = block.split("\n")
            # Heuristic: ALL CAPS short line or markdown # heading
            first = lines[0].strip()
            if first.startswith("# "):
                doc.add_heading(first.lstrip("# ").strip(), level=1)
                body = "\n".join(lines[1:]).strip()
                if body:
                    for ln in body.split("\n"):
                        self._add_line(doc, ln)
                continue
            if first.startswith("## "):
                doc.add_heading(first.lstrip("# ").strip(), level=2)
                body = "\n".join(lines[1:]).strip()
                if body:
                    for ln in body.split("\n"):
                        self._add_line(doc, ln)
                continue
            if (
                len(first) < 80
                and first.isupper()
                and any(c.isalpha() for c in first)
            ):
                doc.add_heading(first.title() if len(first) > 40 else first, level=1)
                for ln in lines[1:]:
                    self._add_line(doc, ln)
                continue
            for ln in lines:
                self._add_line(doc, ln)

        _ensure_parent(path)
        doc.save(str(path))

    @staticmethod
    def _add_line(doc, ln: str) -> None:
        ln = (ln or "").rstrip()
        if not ln:
            doc.add_paragraph("")
            return
        # bullets
        if re.match(r"^(\-|\*|\u2022)\s+", ln):
            text = re.sub(r"^(\-|\*|\u2022)\s+", "", ln)
            doc.add_paragraph(text, style="List Bullet")
            return
        if re.match(r"^\d+[\.\)]\s+", ln):
            text = re.sub(r"^\d+[\.\)]\s+", "", ln)
            doc.add_paragraph(text, style="List Number")
            return
        p = doc.add_paragraph(ln)
        for run in p.runs:
            run.font.name = "Calibri"
            try:
                from docx.shared import Pt  # type: ignore
                run.font.size = Pt(11)
            except Exception:
                pass

    def _write_pdf_reportlab(self, path: Path, content: str, title: str = "") -> None:
        try:
            from reportlab.lib.pagesizes import A4  # type: ignore
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
            from reportlab.lib.units import inch  # type: ignore
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer  # type: ignore
            from reportlab.lib.enums import TA_LEFT  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "reportlab not installed. Run: pip install reportlab"
            ) from e

        _ensure_parent(path)
        doc = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=0.7 * inch,
            rightMargin=0.7 * inch,
            topMargin=0.7 * inch,
            bottomMargin=0.7 * inch,
        )
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            alignment=TA_LEFT,
            spaceAfter=6,
        )
        head = ParagraphStyle(
            "Head",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            spaceBefore=10,
            spaceAfter=6,
        )
        story = []
        if title:
            story.append(Paragraph(_xml_escape(title), styles["Title"]))
            story.append(Spacer(1, 0.15 * inch))

        for block in _paragraphs(content):
            for ln in block.split("\n"):
                ln = ln.strip()
                if not ln:
                    story.append(Spacer(1, 0.08 * inch))
                    continue
                if ln.startswith("#"):
                    story.append(Paragraph(_xml_escape(ln.lstrip("# ").strip()), head))
                elif len(ln) < 80 and ln.isupper() and any(c.isalpha() for c in ln):
                    story.append(Paragraph(_xml_escape(ln), head))
                else:
                    story.append(Paragraph(_xml_escape(ln), body))
        doc.build(story)

    def _docx_to_pdf_word(self, docx_path: Path, pdf_path: Path) -> bool:
        """Windows + Word installed: ExportAsFixedFormat."""
        if not sys.platform.startswith("win"):
            return False
        try:
            import win32com.client  # type: ignore
        except Exception:
            return False
        word = None
        try:
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(str(docx_path.resolve()))
            # 17 = wdFormatPDF
            doc.SaveAs(str(pdf_path.resolve()), FileFormat=17)
            doc.Close(False)
            return pdf_path.is_file()
        except Exception:
            return False
        finally:
            try:
                if word is not None:
                    word.Quit()
            except Exception:
                pass


def _xml_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


from tools.base import BaseTool


class WriteDocxTool(BaseTool, _DocxPdfMixin):
    @property
    def name(self) -> str:
        return "write_docx"

    @property
    def description(self) -> str:
        return (
            "Create a real Microsoft Word .docx file from plain text content. "
            "Use for CV drafts, letters, reports. Path should end with .docx. "
            "Parents folders are created automatically."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Full path or Desktop/ForJob/.../file.docx",
                },
                "content": {
                    "type": "string",
                    "description": "Plain text body (use blank lines between sections)",
                },
                "title": {
                    "type": "string",
                    "description": "Optional document title/heading",
                },
            },
            "required": ["filepath", "content"],
        }

    def execute(
        self, filepath: str = "", content: str = "", title: str = "", **kwargs
    ) -> Dict[str, Any]:
        try:
            path = _expand_path(filepath)
            if path.suffix.lower() != ".docx":
                path = path.with_suffix(".docx")
            if content == "__FROM_LAST_ASSISTANT__":
                return {
                    "content": "Content placeholder not expanded. Provide full text content.",
                    "is_error": True,
                }
            if not (content or "").strip():
                return {"content": "Content is empty.", "is_error": True}
            self._write_docx(path, content, title=title or "")
            return {
                "content": f"Word document created: {path}",
                "is_error": False,
                "filepath": str(path),
            }
        except Exception as e:
            return {"content": f"write_docx failed: {e}", "is_error": True}


class WritePdfTool(BaseTool, _DocxPdfMixin):
    @property
    def name(self) -> str:
        return "write_pdf"

    @property
    def description(self) -> str:
        return (
            "Create a PDF file from plain text, or convert an existing .docx to PDF. "
            "On Windows with Word installed, docx→PDF uses Word export; otherwise reportlab text PDF."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Output path ending in .pdf",
                },
                "content": {
                    "type": "string",
                    "description": "Plain text body (if not converting from docx)",
                },
                "source_docx": {
                    "type": "string",
                    "description": "Optional existing .docx to convert to PDF",
                },
                "title": {
                    "type": "string",
                    "description": "Optional PDF title",
                },
            },
            "required": ["filepath"],
        }

    def execute(
        self,
        filepath: str = "",
        content: str = "",
        source_docx: str = "",
        title: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            path = _expand_path(filepath)
            if path.suffix.lower() != ".pdf":
                path = path.with_suffix(".pdf")
            src = (source_docx or "").strip()
            if src:
                docx_path = _expand_path(src)
                if not docx_path.is_file():
                    return {
                        "content": f"source_docx not found: {docx_path}",
                        "is_error": True,
                    }
                _ensure_parent(path)
                if self._docx_to_pdf_word(docx_path, path):
                    return {
                        "content": f"PDF created via Word export: {path}",
                        "is_error": False,
                        "filepath": str(path),
                    }
                # Fallback: extract text roughly by reading docx then reportlab
                try:
                    from docx import Document  # type: ignore

                    d = Document(str(docx_path))
                    content = "\n".join(p.text for p in d.paragraphs)
                except Exception as e:
                    return {
                        "content": f"Word export failed and docx text fallback failed: {e}",
                        "is_error": True,
                    }
            if content == "__FROM_LAST_ASSISTANT__":
                return {
                    "content": "Content placeholder not expanded. Provide full text or source_docx.",
                    "is_error": True,
                }
            if not (content or "").strip():
                return {
                    "content": "Provide content or source_docx for PDF generation.",
                    "is_error": True,
                }
            self._write_pdf_reportlab(path, content, title=title or "")
            return {
                "content": f"PDF created: {path}",
                "is_error": False,
                "filepath": str(path),
            }
        except Exception as e:
            return {"content": f"write_pdf failed: {e}", "is_error": True}
