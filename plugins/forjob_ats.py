# plugins/forjob_ats.py
"""ForJob — offline CV / ATS keyword check against target roles."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from tools.base import BaseTool

# Role keyword packs (not invented experience — matching only)
ROLE_KEYWORDS: Dict[str, List[str]] = {
    "solution_architect_net": [
        "solution architect", "software architect", "technical architect",
        ".net", "c#", "asp.net core", "microservices", "distributed systems",
        "api design", "system integration", "architecture documentation",
        "stakeholder", "technical leadership", "azure", "sql server",
        "rest", "soap", "ci/cd", "docker",
    ],
    "banking_payments": [
        "banking", "payments", "card issuance", "card personalization",
        "emv", "hsm", "pci", "iso 8583", "clearing", "settlement",
        "chargeback", "tokenisation", "tokenization", "3ds", "pos",
        "acquiring", "issuing", "pin", "payshield", "pkcs#11",
        "digital wallet", "payment gateway",
    ],
    "ats_general": [
        "led", "designed", "implemented", "delivered", "improved",
        "reduced", "increased", "managed", "integrated", "architected",
        "years", "experience",
    ],
}


def _read_text_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        from docx import Document  # type: ignore

        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if (p.text or "").strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join((c.text or "").strip() for c in row.cells))
        return "\n".join(parts)
    return path.read_text(encoding="utf-8", errors="ignore")


class ForJobAtsCheckTool(BaseTool):
    @property
    def name(self) -> str:
        return "forjob_ats_check"

    @property
    def description(self) -> str:
        return (
            "Offline ATS / keyword coverage check for a CV against Solution Architect "
            ".NET and banking/payments roles. Reads a local .docx/.txt CV path and "
            "returns present vs missing keywords, section tips, and next actions. "
            "Does not invent experience. Use instead of web_search for ATS keyword checks."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to CV (.docx or .txt)",
                },
                "target_role": {
                    "type": "string",
                    "description": "Optional focus: solution_architect_net | banking_payments | both",
                },
            },
            "required": ["filepath"],
        }

    def execute(
        self, filepath: str = "", target_role: str = "both", **kwargs
    ) -> Dict[str, Any]:
        fp = (filepath or "").strip().strip('"').replace("\\", "/")
        home = os.path.expanduser("~").replace("\\", "/")
        if fp.startswith("Desktop/"):
            fp = f"{home}/Desktop/{fp.split('/', 1)[-1]}"
        path = Path(fp)
        if not path.is_file():
            path = Path(filepath.strip().strip('"'))
        if not path.is_file():
            return {"content": f"CV not found: {filepath}", "is_error": True}

        try:
            text = _read_text_file(path)
        except Exception as e:
            return {"content": f"Could not read CV: {e}", "is_error": True}

        low = text.lower()
        role = (target_role or "both").lower().strip()
        packs = []
        if role in ("both", "all", ""):
            packs = ["solution_architect_net", "banking_payments", "ats_general"]
        elif "bank" in role or "payment" in role:
            packs = ["banking_payments", "ats_general"]
        else:
            packs = ["solution_architect_net", "ats_general"]

        lines: List[str] = [
            f"ForJob ATS check: {path.name}",
            f"Target packs: {', '.join(packs)}",
            "",
        ]
        total_present = 0
        total_missing = 0
        all_missing: List[str] = []

        for pack in packs:
            kws = ROLE_KEYWORDS[pack]
            present = [k for k in kws if k.lower() in low]
            missing = [k for k in kws if k.lower() not in low]
            total_present += len(present)
            total_missing += len(missing)
            all_missing.extend(missing)
            cov = (100 * len(present) // max(1, len(kws)))
            lines.append(f"## {pack} — coverage ~{cov}% ({len(present)}/{len(kws)})")
            lines.append("Present: " + (", ".join(present[:20]) or "(none)"))
            if len(present) > 20:
                lines.append(f"  … +{len(present)-20} more")
            lines.append("Missing / weak signal: " + (", ".join(missing[:15]) or "(none)"))
            lines.append("")

        # Structure heuristics
        lines.append("## Structure heuristics")
        checks = [
            ("Contact line / email", bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text))),
            ("Professional summary", "summary" in low or "profile" in low),
            ("Experience section", "experience" in low or "employment" in low),
            ("Quantified metrics (%, numbers+)", bool(re.search(r"\d+%|\d+\+", text))),
            ("Skills / stack section", "technology" in low or "skills" in low or "stack" in low),
            ("Education", "education" in low or "bachelor" in low or "university" in low),
        ]
        for label, ok in checks:
            lines.append(f"- {'OK' if ok else 'GAP'}: {label}")

        lines.append("")
        lines.append("## Notes (honest)")
        lines.append(
            "- This is keyword/structure coverage only — not a guarantee of ATS pass."
        )
        lines.append(
            "- Do not add missing keywords unless they match real experience."
        )
        lines.append(
            "- Strong domain signals already typical for this profile: HSM, card personalization, .NET, banking."
        )
        if any(k in all_missing for k in ("iso 8583", "chargeback", "3ds", "tokenization", "tokenisation")):
            lines.append(
                "- Payments scheme keywords (ISO 8583, 3DS, chargeisation, chargebacks) are weak/missing — "
                "only add if you truly worked those areas; otherwise keep as 'familiar with adjacent flows'."
            )

        lines.append("")
        lines.append("## Suggested next actions")
        lines.append("1. Mirror exact job-title keywords from each target JD in a tailored CV version.")
        lines.append("2. Add 2–3 quantified bullets per recent role where evidence exists.")
        lines.append("3. Keep a master CV; export ATS-plain .docx without tables if a portal fails parse.")
        lines.append("4. Save tailored versions under ForJob/CV_Versions/.")

        return {"content": "\n".join(lines), "is_error": False}
