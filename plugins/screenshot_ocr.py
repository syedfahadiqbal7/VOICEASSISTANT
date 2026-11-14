# plugins/screenshot_ocr.py
from tools.base import BaseTool
from typing import Any, Dict
import os
from datetime import datetime


class ScreenshotOCRTool(BaseTool):
    @property
    def name(self) -> str:
        return "screenshot_ocr"

    @property
    def description(self) -> str:
        return "Take a screenshot of the screen and optionally extract text using OCR."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ocr": {
                    "type": "boolean",
                    "description": "Whether to run OCR on the screenshot (default false)",
                }
            },
            "required": [],
        }

    def execute(self, ocr: bool = False) -> Dict[str, Any]:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            path = os.path.join(os.path.expanduser("~"), "Desktop", filename)
            img.save(path)

            result = f"Screenshot saved to: {path}"

            if ocr:
                try:
                    import pytesseract
                    text = pytesseract.image_to_string(img)
                    result += f"\n\nOCR Text:\n{text.strip()}"
                except Exception as e:
                    result += f"\n\nOCR failed: {e}"

            return {"content": result, "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}