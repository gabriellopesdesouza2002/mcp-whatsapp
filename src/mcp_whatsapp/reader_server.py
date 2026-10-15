"""Read-only MCP entry point for the WhatsApp Reader skill."""

import re
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

os.environ["MCP_WHATSAPP_READER"] = "1"

from mcp_whatsapp import server as core

reader = FastMCP(
    "WhatsApp Reader",
    instructions=(
        "Read-only access to WhatsApp chat history. The exposed tools do not send, "
        "modify, delete, mark, or administer WhatsApp data or sessions. "
        "History can be incomplete or delayed; report stale warnings and never claim completeness."
    ),
)


def _safe(result: Any) -> Any:
    """Always redact common sensitive identifiers in Reader responses."""
    if isinstance(result, str):
        for label, pattern in core.PrivacyGuard.PATTERNS.items():
            result = re.sub(pattern, f"[REDACTED_{label}]", result)
        return result
    return result


@reader.tool()
async def whatsapp_status() -> str:
    """Read connection and WuzAPI service status; does not connect or alter a session."""
    return _safe(await core.whatsapp_status())


@reader.tool()
async def whatsapp_get_datetime() -> str:
    """Get the current server date and time for interpreting message timestamps."""
    return await core.whatsapp_get_datetime()


@reader.tool()
async def whatsapp_get_chats() -> str:
    """List chats found in locally synchronized history, with recent-message previews."""
    return _safe(await core.whatsapp_get_chats())


@reader.tool()
async def whatsapp_get_messages(phone: str, count: int = 20) -> str:
    """Read recent messages for a phone number or full chat JID (no leading +)."""
    return _safe(await core.whatsapp_get_messages(phone, count))


@reader.tool()
async def whatsapp_search_messages(query: str, limit: int = 50) -> str:
    """Find literal keyword matches in synchronized message history; not semantic search."""
    return _safe(await core.whatsapp_search_messages(query, limit))


@reader.tool()
async def whatsapp_search_contacts(query: str) -> str:
    """Search contact names, with a local-history fallback for recent direct chats."""
    return _safe(await core.whatsapp_search_contacts(query))


@reader.tool()
async def whatsapp_get_messages_by_contact_name(
    name: str, only_today: bool = False, limit: int = 20
) -> str:
    """Read recent messages for a contact found by name; searches all dates by default."""
    return _safe(await core.whatsapp_get_messages_by_contact_name(name, only_today, limit))


@reader.tool()
async def whatsapp_list_groups() -> str:
    """List groups without changing membership or settings."""
    return _safe(await core.whatsapp_list_groups())


@reader.tool()
async def whatsapp_get_group_info(group_jid: str) -> str:
    """Read metadata for a group JID; does not change the group."""
    return _safe(await core.whatsapp_get_group_info(group_jid))


def main() -> None:
    reader.run(transport="stdio")


if __name__ == "__main__":
    main()
