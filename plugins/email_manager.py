# plugins/email_manager.py
from tools.base import BaseTool
from typing import Any, Dict


class EmailManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "email_manager"

    @property
    def description(self) -> str:
        return (
            "Read, search, draft emails and create meetings for Gmail and Outlook. "
            "Actions: read_inbox, search, draft, create_meeting"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "gmail or outlook",
                },
                "action": {
                    "type": "string",
                    "description": "read_inbox, search, draft, create_meeting",
                },
                "query": {
                    "type": "string",
                    "description": "Search query or meeting subject",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient email (for draft/meeting)",
                },
                "subject": {
                    "type": "string",
                },
                "body": {
                    "type": "string",
                },
                "start_time": {
                    "type": "string",
                    "description": "Meeting start time (ISO format)",
                },
                "end_time": {
                    "type": "string",
                    "description": "Meeting end time (ISO format)",
                },
            },
            "required": ["provider", "action"],
        }

    @property
    def help(self) -> Dict[str, str]:
        return {
            "summary": "Gmail aur Outlook ke liye email padhna, search karna, draft banan a aur meeting create karna.",
            "usage": (
                "provider: gmail ya outlook\n"
                "action: read_inbox | search | draft | create_meeting\n"
                "Baaki parameters action ke hisaab se."
            ),
            "examples": (
                "• 'Gmail ki latest emails dikhao'\n"
                "• 'Outlook mein Ali ke emails dhoondo'\n"
                "• 'Gmail pe draft banao to boss@company.com subject Meeting'\n"
                "• 'Outlook mein kal 3 baje meeting schedule karo'"
            ),
        }

    def execute(self, provider: str, action: str, query: str = "", to: str = "",
                subject: str = "", body: str = "", start_time: str = "", end_time: str = "") -> Dict[str, Any]:
        provider = provider.lower()
        action = action.lower()

        # -------------------------------------------------
        # YAHAN aap actual API calls implement karoge
        # Abhi placeholder responses hain
        # -------------------------------------------------

        if provider not in ("gmail", "outlook"):
            return {"content": "provider must be gmail or outlook", "is_error": True}

        if action == "read_inbox":
            return {
                "content": f"[{provider.upper()}] Latest emails:\n1. Subject: Project Update\n2. Subject: Meeting Reminder\n3. Subject: Invoice",
                "is_error": False,
            }

        elif action == "search":
            return {
                "content": f"[{provider.upper()}] Search results for '{query}':\n- Email 1\n- Email 2",
                "is_error": False,
            }

        elif action == "draft":
            return {
                "content": f"[{provider.upper()}] Draft created\nTo: {to}\nSubject: {subject}\nBody: {body[:100]}...",
                "is_error": False,
            }

        elif action == "create_meeting":
            return {
                "content": f"[{provider.upper()}] Meeting created\nSubject: {subject}\nStart: {start_time}\nEnd: {end_time}",
                "is_error": False,
            }

        return {"content": f"Unknown action: {action}", "is_error": True}