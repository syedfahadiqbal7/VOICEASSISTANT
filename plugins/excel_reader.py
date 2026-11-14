# plugins/excel_reader.py
from tools.base import BaseTool
from typing import Any, Dict


class ExcelReaderTool(BaseTool):
    @property
    def name(self) -> str:
        return "excel_reader"

    @property
    def description(self) -> str:
        return "Read an Excel or CSV file and return a summary / preview."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "sheet_name": {"type": "string", "description": "Optional sheet name"},
            },
            "required": ["filepath"],
        }

    def execute(self, filepath: str, sheet_name: str = None) -> Dict[str, Any]:
        try:
            import pandas as pd
            if filepath.lower().endswith(".csv"):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath, sheet_name=sheet_name)
            preview = df.head(15).to_string()
            summary = f"Shape: {df.shape}\nColumns: {list(df.columns)}\n\nPreview:\n{preview}"
            return {"content": summary, "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}