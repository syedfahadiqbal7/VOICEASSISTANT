# security/command_analyzer.py
import re
from typing import Tuple


class CommandAnalyzer:
    """Detects potentially dangerous commands."""

    HIGH_RISK_PATTERNS = [
        r"\b(rm|del|remove-item|rmdir|rd)\b.*(-r|-rf|-recurse|force)",
        r"\bformat\b",
        r"\bdiskpart\b",
        r"\breg\s+(delete|add|import)",
        r"\bnet\s+user\b",
        r"\bnet\s+localgroup\b",
        r"\bshutdown\b",
        r"\brestart-computer\b",
        r"\bstop-computer\b",
        r"\bclear-eventlog\b",
        r"\bdisable-windowsoptionalfeature\b",
        r"\bset-executionpolicy\b.*unrestricted",
        r"\binvoke-expression\b|\biex\b",
        r"\bdownloadstring\b|\bdownloadfile\b",
        r"\bnew-service\b|\bsc\s+create\b",
        r"\bschedule.*task",
        r"\bfirewall",
        r"\bipconfig\s+/release",
    ]

    MEDIUM_RISK_PATTERNS = [
        r"\binstall-package\b|\binstall-module\b",
        r"\bpip\s+install\b",
        r"\bnpm\s+install\b",
        r"\bset-itemproperty\b",
        r"\bnew-item\b.*-force",
    ]

    def analyze(self, command: str) -> Tuple[str, str]:
        """
        Returns:
            risk_level: "high" | "medium" | "low"
            reason: explanation
        """
        cmd_lower = command.lower()

        for pattern in self.HIGH_RISK_PATTERNS:
            if re.search(pattern, cmd_lower, re.IGNORECASE):
                return "high", f"Matched high-risk pattern: {pattern}"

        for pattern in self.MEDIUM_RISK_PATTERNS:
            if re.search(pattern, cmd_lower, re.IGNORECASE):
                return "medium", f"Matched medium-risk pattern: {pattern}"

        return "low", "No known dangerous patterns detected"