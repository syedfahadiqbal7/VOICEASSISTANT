# plugins/teams_manager.py
from tools.base import BaseTool
from typing import Any, Dict


class TeamsManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "teams_manager"

    @property
    def description(self) -> str:
        return (
            "Microsoft Teams: read chats, search messages, draft and send messages "
            "(sending requires approval)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "read_chats, search, draft, send",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Teams chat/conversation ID",
                },
                "query": {
                    "type": "string",
                    "description": "Search text",
                },
                "message": {
                    "type": "string",
                    "description": "Message content for draft/send",
                },
            },
            "required": ["action"],
        }

    @property
    def help(self) -> Dict[str, str]:
        return {
            "summary": "Microsoft Teams ke chats padhna, search karna, draft banana aur approval ke baad send karna.",
            "usage": (
                "action: read_chats | search | draft | send\n"
                "send action Security Gate se approve karna padega."
            ),
            "examples": (
                "• 'Teams ki recent chats dikhao'\n"
                "• 'Teams mein project update dhoondo'\n"
                "• 'Teams pe draft message banao'\n"
                "• 'Yeh message Teams pe bhej do' (approval ke baad)"
            ),
        }

    def execute(self, action: str, chat_id: str = "", query: str = "", message: str = "") -> Dict[str, Any]:
        action = action.lower()

        if action == "read_chats":
            return {
                "content": "Recent Teams Chats:\n1. Dev Team\n2. Project Alpha\n3. HR Announcements",
                "is_error": False,
            }

        elif action == "search":
            return {
                "content": f"Search results for '{query}' in Teams:\n- Message 1\n- Message 2",
                "is_error": False,
            }

        elif action == "draft":
            return {
                "content": f"Draft ready for Teams:\n{message}",
                "is_error": False,
            }

        elif action == "send":
            # Real implementation mein yahan Graph API call hogi
            return {
                "content": f"Message sent to Teams chat {chat_id}:\n{message}",
                "is_error": False,
            }

        return {"content": f"Unknown action: {action}", "is_error": True}