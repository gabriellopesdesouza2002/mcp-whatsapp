"""
MCP Server for WhatsApp via WuzAPI.

Exposes WhatsApp capabilities as MCP tools that any AI (Claude, Gemini, etc.) can use.
Uses the official MCP Python SDK with stdio transport.
"""

import base64
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from mcp_whatsapp.wuzapi_client import WuzAPIClient

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

ENV_FILE = Path(__file__).parent.parent.parent / ".env"
load_dotenv(ENV_FILE, override=True)

WUZAPI_BASE_URL = os.getenv("WUZAPI_BASE_URL", "http://localhost:7143")
WUZAPI_TOKEN = os.getenv("WUZAPI_TOKEN", "")
WUZAPI_ADMIN_TOKEN = os.getenv("WUZAPI_ADMIN_TOKEN", "")
WUZAPI_PRIVACY_MODE = os.getenv("WUZAPI_PRIVACY_MODE", "false").lower() == "true"
WUZAPI_HISTORY_SYNC = os.getenv("WUZAPI_HISTORY_SYNC", "true").lower() == "true"

# Audit Logging setup
AUDIT_LOG_FILE = Path(__file__).parent.parent.parent / "logs" / "audit_privacy.log"
AUDIT_LOG_FILE.parent.mkdir(exist_ok=True)

logging.basicConfig(
    filename=str(AUDIT_LOG_FILE),
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)

class PrivacyGuard:
    """Utility to ensure LGPD/GDPR compliance by masking PII and logging activities."""
    
    # Patterns for common PII (Personal Identifiable Information)
    PATTERNS = {
        "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "CPF": r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
        "CNPJ": r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
        "CREDIT_CARD": r"\b(?:\d[ -]*?){13,16}\b",
        # Generic phone pattern to catch potentially sensitive numbers not in chat format
        "PHONE_PII": r"\b(?:\+?\d{1,3}[- ]?)?\(?\d{2,3}\)?[- ]?\d{4,5}[- ]?\d{4}\b"
    }

    @staticmethod
    def redact(text: str) -> str:
        """Redact sensitive info if privacy mode is enabled."""
        if not WUZAPI_PRIVACY_MODE or not isinstance(text, str):
            return text
            
        redacted = text
        for label, pattern in PrivacyGuard.PATTERNS.items():
            redacted = re.sub(pattern, f"[REDACTED_{label}]", redacted)
        return redacted

    @staticmethod
    def log_action(tool_name: str, params: dict[str, Any]):
        """Audit log for accountability (GDPR/LGPD requirement)."""
        # Remove sensitive content from logs themselves
        safe_params = {k: (v if k not in ["message", "text", "body", "query"] else "[CONTENT]") for k, v in params.items()}
        logging.info(f"Tool: {tool_name} | Params: {json.dumps(safe_params)}")

def _format_result(result: dict[str, Any]) -> str:
    """Format API response as readable JSON string with Privacy Guardrails."""
    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    return PrivacyGuard.redact(json_str)


async def _query_db(sql: str, params: tuple = (), limit: int = 50) -> list[Any]:
    """Helper to query the SQLite database using a temporary shadow copy to avoid locking/corruption issues."""
    import time
    import shutil
    import tempfile
    
    retries = 3
    last_error = None
    
    # Create a temporary path for the database copy
    tmp_dir = tempfile.gettempdir()
    base_name = f"mcp_whatsapp_shadow_{int(time.time())}"
    tmp_path = os.path.join(tmp_dir, f"{base_name}.db")
    tmp_wal = tmp_path + "-wal"
    tmp_shm = tmp_path + "-shm"
    
    db_str = str(DB_PATH)
        
    for i in range(retries):
        try:
            # Copy the active DB and its WAL/SHM files if they exist
            shutil.copy2(db_str, tmp_path)
            if os.path.exists(db_str + "-wal"):
                shutil.copy2(db_str + "-wal", tmp_wal)
            if os.path.exists(db_str + "-shm"):
                shutil.copy2(db_str + "-shm", tmp_shm)
            
            # Query the copy
            conn = sqlite3.connect(tmp_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.close()
            
            # Clean up and return
            for f in [tmp_path, tmp_wal, tmp_shm]:
                if os.path.exists(f):
                    try: os.remove(f)
                    except: pass
            return rows
        except Exception as e:
            last_error = e
            if i < retries - 1:
                time.sleep(0.5 * (i + 1))
                continue
            break
    
    # Final cleanup if failed
    for f in [tmp_path, tmp_wal, tmp_shm]:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
            
    raise last_error if last_error else Exception("Unknown database error")


if not WUZAPI_TOKEN:
    print("⚠️  WUZAPI_TOKEN not set. Configure it in .env file.", file=sys.stderr)

DB_PATH = Path(__file__).parent.parent.parent / "wuzapi_data" / "users.db"

# ──────────────────────────────────────────────
# MCP Server & WuzAPI Client
# ──────────────────────────────────────────────

mcp = FastMCP(
    "WhatsApp MCP",
    instructions=(
        "MCP server that provides WhatsApp messaging capabilities via WuzAPI. "
        "Use whatsapp_connect() first to start a session, then use messaging tools to send/receive messages.\n\n"
        "--- DELETION GUARDRAIL (MANDATORY) ---\n"
        "⚠️  NEVER call whatsapp_delete_message, whatsapp_admin_delete_user, or whatsapp_disconnect "
        "without EXPLICITLY asking the user for confirmation first. Show what will be deleted/affected "
        "and wait for a clear 'yes', 'confirm' or 'pode apagar' response before proceeding. "
        "If the user did not explicitly confirm, return a warning and do NOT call the tool.\n\n"
        "--- COMPLIANCE GUARDRAILS (LGPD/GDPR) ---\n"
        "1. TRANSPARENCY: Always inform the user if you are fetching large amounts of personal history.\n"
        "2. DATA MINIMIZATION: Only request the messages and contacts necessary for the current task.\n"
        "3. SENSITIVE DATA: Do not repeat or store PII (CPF, Credit Cards, etc.) unless strictly required by the user.\n"
        "4. ACCOUNTABILITY: All actions are logged for auditing purposes."
    ),
)

client = WuzAPIClient(
    base_url=WUZAPI_BASE_URL,
    token=WUZAPI_TOKEN,
    admin_token=WUZAPI_ADMIN_TOKEN,
    history_sync=WUZAPI_HISTORY_SYNC,
)


# ══════════════════════════════════════════════
# SESSION / CONNECTION TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_connect() -> list[TextContent | ImageContent]:
    """Connect to WhatsApp. If not yet paired, automatically shows the QR code
    to scan with your phone. Just call this once and scan the QR."""
    # Ensure user exists before trying to connect (Plug & Play)
    await client.ensure_user_exists()
    
    result = await client.connect()

    # Show QR if: connected OK with no jid, OR "already connected" (needs pairing)
    already_connected = result.get("error") == "already connected"
    not_logged = not result.get("data", {}).get("jid")
    if (result.get("success") and not_logged) or already_connected:
        image_bytes = await client.get_qrcode_image()
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            return [
                TextContent(
                    type="text",
                    text="📱 Scan this QR code with WhatsApp on your phone:\n"
                         "WhatsApp → ⋮ → Linked devices → Link a device",
                ),
                ImageContent(type="image", data=b64, mimeType="image/png"),
            ]

    return [TextContent(type="text", text=_format_result(result))]


@mcp.tool()
async def whatsapp_disconnect(confirmed: bool = False) -> str:
    """Disconnect/Logout from WhatsApp session.

    ⚠️ DESTRUCTIVE: This will disconnect your WhatsApp session.
    Args:
        confirmed: Must be True to proceed. Always ask the user explicitly before disconnecting.
    """
    if not confirmed:
        return (
            "⚠️ Você tem certeza que quer DESCONECTAR o WhatsApp?\n"
            "Isso encerrará sua sessão ativa. Para confirmar, chame novamente com confirmed=True."
        )
    result = await client.disconnect()
    return _format_result(result)


@mcp.tool()
async def whatsapp_status() -> str:
    """Get the current WhatsApp connection status. Returns whether
    the session is connected, disconnected, or waiting for QR scan."""
    result = await client.get_status()
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_qrcode() -> list[TextContent | ImageContent]:
    """Get the QR code for WhatsApp Web pairing. The user needs to scan
    this QR code with their phone to connect the session.
    Returns the QR code as a viewable image."""
    # Try to get the QR as image first
    image_bytes = await client.get_qrcode_image()
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return [
            TextContent(type="text", text="Scan this QR code with your WhatsApp phone app:"),
            ImageContent(type="image", data=b64, mimeType="image/png"),
        ]
    # Fallback to JSON response
    result = await client.get_qrcode()
    return [
        TextContent(type="text", text=_format_result(result)),
    ]


@mcp.tool()
async def whatsapp_health() -> str:
    """Check the WuzAPI service health status."""
    result = await client.health()
    return _format_result(result)


# ══════════════════════════════════════════════
# MESSAGING TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_send_text(phone: str, message: str) -> str:
    """Send a text message via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
               Do NOT include '+' prefix.
        message: The text message content to send.
    """
    result = await client.send_text(phone, message)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_image(
    phone: str, image_url: str, caption: str = ""
) -> str:
    """Send an image via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        image_url: URL of the image to send.
        caption: Optional caption for the image.
    """
    result = await client.send_image(phone, image_url, caption)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_document(
    phone: str, document_url: str, filename: str = "", caption: str = ""
) -> str:
    """Send a document/file via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        document_url: URL of the document to send.
        filename: Optional filename to display.
        caption: Optional caption for the document.
    """
    result = await client.send_document(phone, document_url, filename, caption)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_audio(phone: str, audio_url: str) -> str:
    """Send an audio message via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        audio_url: URL of the audio file to send.
    """
    result = await client.send_audio(phone, audio_url)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_video(
    phone: str, video_url: str, caption: str = ""
) -> str:
    """Send a video via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        video_url: URL of the video to send.
        caption: Optional caption for the video.
    """
    result = await client.send_video(phone, video_url, caption)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_sticker(phone: str, sticker_url: str) -> str:
    """Send a sticker via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        sticker_url: URL of the sticker image (WebP format recommended).
    """
    result = await client.send_sticker(phone, sticker_url)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_location(
    phone: str, latitude: float, longitude: float, name: str = ""
) -> str:
    """Send a location via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        latitude: Location latitude.
        longitude: Location longitude.
        name: Optional name for the location.
    """
    result = await client.send_location(phone, latitude, longitude, name)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_contact(
    phone: str, contact_name: str, contact_phone: str
) -> str:
    """Send a contact card (vCard) via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        contact_name: Name of the contact to share.
        contact_phone: Phone number of the contact to share.
    """
    result = await client.send_contact(phone, contact_name, contact_phone)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_link(phone: str, url: str, text: str = "") -> str:
    """Send a link with preview via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        url: The URL to send.
        text: Optional accompanying text.
    """
    result = await client.send_link(phone, url, text)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_buttons(
    phone: str,
    text: str,
    buttons: list[dict[str, str]],
    title: str = "",
    footer: str = "",
) -> str:
    """Send an interactive buttons message via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        text: The body text of the message.
        buttons: List of button objects, each with 'ButtonId' and 'ButtonText'.
        title: Optional message title.
        footer: Optional footer text.
    """
    result = await client.send_buttons(phone, text, buttons, title, footer)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_list(
    phone: str,
    text: str,
    button_text: str,
    sections: list[dict[str, Any]],
    title: str = "",
    footer: str = "",
) -> str:
    """Send an interactive list message via WhatsApp.

    Args:
        phone: Recipient phone number with country code (e.g. '5511999998888').
        text: The body text of the message.
        button_text: Text for the list button.
        sections: List of section objects with 'Title' and 'Rows'.
        title: Optional message title.
        footer: Optional footer text.
    """
    result = await client.send_list(phone, text, button_text, sections, title, footer)
    return _format_result(result)


@mcp.tool()
async def whatsapp_react(phone: str, message_id: str, emoji: str) -> str:
    """React to a WhatsApp message with an emoji.

    Args:
        phone: Phone number of the chat containing the message.
        message_id: ID of the message to react to.
        emoji: Emoji to react with (e.g. '👍', '❤️').
    """
    result = await client.react(phone, message_id, emoji)
    return _format_result(result)


@mcp.tool()
async def whatsapp_mark_read(phone: str, message_ids: list[str]) -> str:
    """Mark WhatsApp messages as read.

    Args:
        phone: Phone number of the chat.
        message_ids: List of message IDs to mark as read.
    """
    result = await client.mark_read(phone, message_ids)
    return _format_result(result)


@mcp.tool()
async def whatsapp_download_media(phone: str, message_id: str) -> str:
    """Download media (image, audio, video, document) from a WhatsApp message.

    Args:
        phone: Phone number of the chat containing the message.
        message_id: ID of the message with the media to download.
    """
    result = await client.download_media(phone, message_id)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_messages(phone: str, count: int = 50) -> str:
    """Get recent messages from a WhatsApp chat.

    Args:
        phone: Phone number to get messages from.
        count: Number of messages to retrieve (default: 50).
    """
    result = await client.get_messages(phone, count)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_chats() -> str:
    """List all active WhatsApp conversations with last message info.
    Use this to see recent chats, find who messaged you, or get a conversation overview."""
    result = await client.get_chats()
    return _format_result(result)


@mcp.tool()
async def whatsapp_reply_message(
    phone: str, message: str, quoted_message_id: str
) -> str:
    """Reply to a specific WhatsApp message, quoting the original.

    Args:
        phone: Phone number of the chat (e.g. '5511999998888').
        message: Your reply text.
        quoted_message_id: The ID of the message you want to reply to.
    """
    result = await client.reply_message(phone, message, quoted_message_id)
    return _format_result(result)


@mcp.tool()
async def whatsapp_delete_message(
    phone: str, message_id: str, everyone: bool = True, confirmed: bool = False
) -> str:
    """Delete a WhatsApp message.

    ⚠️ DESTRUCTIVE: Always ask the user explicitly for confirmation before calling this.
    Args:
        phone: Phone number of the chat containing the message.
        message_id: ID of the message to delete.
        everyone: If True, deletes for everyone (revoke). If False, deletes only for you.
        confirmed: Must be True to proceed. Never call with confirmed=True without explicit user approval.
    """
    if not confirmed:
        scope = "para todos" if everyone else "só para você"
        return (
            f"⚠️ Você tem certeza que quer APAGAR a mensagem `{message_id}` ({scope})?\n"
            "Esta ação não pode ser desfeita. Para confirmar, chame novamente com confirmed=True."
        )
    result = await client.delete_message(phone, message_id, everyone)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_poll(
    phone: str,
    question: str,
    options: list[str],
    max_answers: int = 1,
) -> str:
    """Send a poll message on WhatsApp.

    Args:
        phone: Recipient phone number or group JID.
        question: The poll question.
        options: List of answer options (e.g. ['Yes', 'No', 'Maybe']).
        max_answers: Maximum number of options a person can select (default: 1).
    """
    result = await client.send_poll(phone, question, options, max_answers)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_unread_messages() -> str:
    """Get all unread WhatsApp messages across all chats.
    Use this to check what messages you haven't read yet."""
    result = await client.get_unread_messages()
    return _format_result(result)


@mcp.tool()
async def whatsapp_search_messages(query: str, limit: int = 50) -> str:
    """Search for messages containing a specific keyword across all chats.
    This is a global search that looks through all synchronized history.

    Args:
        query: The keyword or phrase to search for.
        limit: Maximum number of results to return (default: 50).
    """
    if not DB_PATH.exists():
        return (
            "⚠️ Database file not found. Ensure WuzAPI is running and you have "
            "scanned the QR code to sync history. Path: " + str(DB_PATH)
        )

    try:
        # Search in the 'text_content' column of the 'message_history' table
        sql = """
            SELECT chat_jid, timestamp, text_content 
            FROM message_history 
            WHERE text_content LIKE ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """
        rows = await _query_db(sql, (f"%{query}%", limit))

        results = []
        for row in rows:
            results.append({
                "from": row[0],
                "timestamp": row[1],
                "message": row[2]
            })

        if not results:
            return f"No messages found containing '{query}'."

        return _format_result({"success": True, "count": len(results), "data": results})
    except Exception as e:
        return f"❌ Error searching database: {str(e)}"


@mcp.tool()
async def whatsapp_forward_message(message_id: str, to_phone: str) -> str:
    """Forward an existing message to another phone number or group.
    This works by locating the original message in your history and re-sending its content.

    Args:
        message_id: The unique ID of the message to forward.
        to_phone: Recipient phone number or group JID (e.g. '5511999998888' or '120363... @g.us').
    """
    if not DB_PATH.exists():
        return "⚠️ Database not found. Cannot locate original message for forwarding."

    try:
        # We need message_type, text_content (for text), media_link and caption
        sql = "SELECT message_type, text_content, media_link FROM message_history WHERE message_id = ? LIMIT 1"
        rows = await _query_db(sql, (message_id,))

        if not rows:
            return f"❌ Message with ID {message_id} not found in history."

        msg_type, body, media_url = rows[0]
        
        # Dispatch based on type
        if msg_type == "text":
            return await whatsapp_send_text(to_phone, body)
        elif msg_type == "image":
            return await whatsapp_send_image(to_phone, media_url, body) # Body is often used as caption for images
        elif msg_type == "document":
            return await whatsapp_send_document(to_phone, media_url, caption=body)
        elif msg_type == "audio":
            return await whatsapp_send_audio(to_phone, media_url)
        elif msg_type == "video":
            return await whatsapp_send_video(to_phone, media_url, caption=body)
        else:
            return f"❌ Forwarding for message type '{msg_type}' is not yet supported."

    except Exception as e:
        return f"❌ Error during forwarding: {str(e)}"


# ══════════════════════════════════════════════
# USER / PROFILE TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_get_user_info() -> str:
    """Get info about the currently logged-in WhatsApp user."""
    result = await client.get_user_info()
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_contacts() -> str:
    """Get the full WhatsApp contacts list."""
    result = await client.get_contacts()
    return _format_result(result)


@mcp.tool()
async def whatsapp_search_contacts(query: str) -> str:
    """Search contacts by name. Understands nicknames like 'amor', 'namorada',
    'esposa', 'mãe', 'pai', etc. Returns matching contacts with their phone numbers.

    Args:
        query: Name or keyword to search for (e.g. 'amor', 'João', 'namorada').
    """
    result = await client.get_contacts()
    contacts_raw = result.get("data", {})

    if not isinstance(contacts_raw, dict):
        return _format_result(result)

    q = query.lower().strip()
    matches = []
    for jid, contact in contacts_raw.items():
        full_name = contact.get("FullName") or ""
        push_name = contact.get("PushName") or ""
        first_name = contact.get("FirstName") or ""
        business_name = contact.get("BusinessName") or ""
        all_names = f"{full_name} {push_name} {first_name} {business_name}".lower()
        if q in all_names:
            # Extract phone from JID (e.g. "5511999998888@s.whatsapp.net" → "5511999998888")
            phone = jid.split("@")[0] if "@" in jid else jid
            matches.append({
                "name": full_name or push_name or first_name or business_name,
                "phone": phone,
                "jid": jid,
            })

    if not matches:
        return f"❌ No contacts found matching '{query}'."

    return _format_result({"success": True, "count": len(matches), "data": matches})


@mcp.tool()
async def whatsapp_get_messages_by_contact_name(name: str, only_today: bool = True, limit: int = 20) -> str:
    """Fetch messages from a contact using their name or nickname (e.g., 'Amor', 'Mãe').
    
    Args:
        name: The name or nickname of the contact to search for.
        only_today: If True, returns only messages from today (UTC).
        limit: Max number of messages to return.
    """
    # 1. Find the contact first
    search_result = await whatsapp_search_contacts(name)
    if "❌" in search_result:
        return search_result # Contact not found message
    
    try:
        data = json.loads(search_result)
        contacts = data.get("data", [])
        if not contacts:
            return f"❌ Nenhum contato encontrado com o nome '{name}'."
        
        # Use the first match
        contact = contacts[0]
        jid = contact["jid"]
        contact_name = contact["name"]
        
        # 2. Query history for this JID
        sql = "SELECT timestamp, text_content, sender_jid, message_type FROM message_history WHERE chat_jid = ?"
        params = [jid]
        
        if only_today:
            # Simple date match for today in the format stored in SQLite
            today_str = datetime.now().strftime("%Y-%m-%d")
            sql += " AND timestamp LIKE ?"
            params.append(f"{today_str}%")
        
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        rows = await _query_db(sql, tuple(params))
        
        results = []
        for row in rows:
            results.append({
                "time": row[0],
                "content": row[1] or f"[{row[3]}]",
                "from_me": row[2] == client.token # This check might need adjustment depending on schema
            })
            
        if not results:
            period = "hoje" if only_today else "recentemente"
            return f"ℹ️ Nenhuma mensagem encontrada de {contact_name} {period}."
            
        return _format_result({
            "success": True,
            "contact": contact_name,
            "count": len(results),
            "messages": results
        })
        
    except Exception as e:
        return f"❌ Erro ao buscar mensagens por nome: {str(e)}"


@mcp.tool()
async def whatsapp_check_phones(phones: list[str]) -> str:
    """Check if phone numbers are registered on WhatsApp.

    Args:
        phones: List of phone numbers to check (with country code, no '+' prefix).
    """
    result = await client.check_phones(phones)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_avatar(phone: str) -> str:
    """Get the profile picture URL for a WhatsApp user.

    Args:
        phone: Phone number to get the avatar for.
    """
    result = await client.get_avatar(phone)
    return _format_result(result)


# ══════════════════════════════════════════════
# GROUP TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_list_groups() -> str:
    """List all WhatsApp groups the user is part of."""
    result = await client.list_groups()
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_group_info(group_jid: str) -> str:
    """Get detailed information about a WhatsApp group.

    Args:
        group_jid: The JID (identifier) of the group.
    """
    result = await client.get_group_info(group_jid)
    return _format_result(result)


@mcp.tool()
async def whatsapp_create_group(name: str, participants: list[str]) -> str:
    """Create a new WhatsApp group.

    Args:
        name: Name of the group to create.
        participants: List of phone numbers to add as participants.
    """
    result = await client.create_group(name, participants)
    return _format_result(result)


@mcp.tool()
async def whatsapp_update_group_participants(
    group_jid: str, participants: list[str], action: str = "add"
) -> str:
    """Add or remove participants from a WhatsApp group.

    Args:
        group_jid: The JID (identifier) of the group.
        participants: List of phone numbers to add/remove.
        action: Either 'add' or 'remove' (default: 'add').
    """
    result = await client.update_group_participants(group_jid, participants, action)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_group_invite_link(group_jid: str) -> str:
    """Get the invite link for a WhatsApp group.

    Args:
        group_jid: The JID (identifier) of the group.
    """
    result = await client.get_group_invite_link(group_jid)
    return _format_result(result)


# ══════════════════════════════════════════════
# WEBHOOK TOOL
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_set_webhook(webhook_url: str) -> str:
    """Configure the webhook URL to receive incoming WhatsApp messages and events.

    Args:
        webhook_url: The URL where WuzAPI will POST incoming events.
    """
    result = await client.set_webhook(webhook_url)
    return _format_result(result)


# ══════════════════════════════════════════════
# NEWSLETTER / CHANNEL TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_get_newsletter_messages(
    newsletter_jid: str, count: int = 50
) -> str:
    """Get messages from a WhatsApp newsletter/channel.

    Args:
        newsletter_jid: The JID of the newsletter/channel.
        count: Number of messages to retrieve (default: 50).
    """
    result = await client.get_newsletter_messages(newsletter_jid, count)
    return _format_result(result)


@mcp.tool()
async def whatsapp_subscribe_newsletter(newsletter_jid: str) -> str:
    """Subscribe to a WhatsApp newsletter/channel.

    Args:
        newsletter_jid: The JID of the newsletter/channel to subscribe to.
    """
    result = await client.subscribe_newsletter(newsletter_jid)
    return _format_result(result)


# ══════════════════════════════════════════════
# ADMIN TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_admin_list_users() -> str:
    """List all WuzAPI users (requires admin token)."""
    result = await client.admin_list_users()
    return _format_result(result)


@mcp.tool()
async def whatsapp_admin_create_user(name: str, token: str) -> str:
    """Create a new WuzAPI user (requires admin token).

    Args:
        name: Username for the new user.
        token: Authentication token for the new user.
    """
    result = await client.admin_create_user(name, token)
    return _format_result(result)


@mcp.tool()
async def whatsapp_admin_delete_user(name: str, confirmed: bool = False) -> str:
    """Delete a WuzAPI user (requires admin token).

    ⚠️ DESTRUCTIVE: Always ask the user explicitly for confirmation before calling this.
    Args:
        name: Username to delete.
        confirmed: Must be True to proceed. Never call with confirmed=True without explicit user approval.
    """
    if not confirmed:
        return (
            f"⚠️ Você tem certeza que quer DELETAR o usuário '{name}'?\n"
            "Isso removerá a sessão e todos os dados do usuário no WuzAPI. "
            "Para confirmar, chame novamente com confirmed=True."
        )
    result = await client.admin_delete_user(name)
    return _format_result(result)


@mcp.tool()
async def whatsapp_admin_enable_history() -> str:
    """Enable message history for the current WuzAPI user (requires admin token).
    Run this once if whatsapp_get_messages returns 'history is disabled' error."""
    if not client.admin_token:
        return "❌ Admin token not configured. Run whatsapp_configure with admin_token first."
    try:
        users = await client.admin_list_users()
        user_list = users.get("data", [])
        for user in user_list:
            if user.get("token") == client.token:
                await client.admin_update_user(
                    user["id"],
                    events="Message,ReadReceipt,HistorySync",
                    history=True,
                )
                return (
                    f"✅ Histórico ativado para o usuário '{user.get('name')}'!\n"
                    "Agora use whatsapp_get_messages para ler mensagens."
                )
        return "❌ Usuário com esse token não encontrado."
    except Exception as e:
        return f"❌ Erro: {str(e)}"


# ══════════════════════════════════════════════
# CONFIGURATION TOOL
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_configure(
    token: str,
    base_url: str = "http://localhost:7143",
    admin_token: str = "",
) -> str:
    """Configure the WhatsApp MCP credentials. Run this once to set up your WuzAPI connection.

    Args:
        token: Your WuzAPI user token.
        base_url: WuzAPI server URL (default: http://localhost:7143).
        admin_token: WuzAPI admin token (optional, needed for admin tools).
    """
    lines = [
        f"WUZAPI_BASE_URL={base_url}",
        f"WUZAPI_TOKEN={token}",
        f"WUZAPI_ADMIN_TOKEN={admin_token}",
    ]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    client.base_url = base_url.rstrip("/")
    client.token = token
    client.admin_token = admin_token

    # Auto-enable message history if admin token is available
    history_status = ""
    if admin_token:
        try:
            users = await client.admin_list_users()
            user_list = users.get("data", [])
            for user in user_list:
                if user.get("token") == token:
                    events = user.get("events", "")
                    needs_update = "Message" not in events
                    has_history = str(user.get("history", "0")) != "0"
                    if needs_update or not has_history:
                        await client.admin_update_user(
                            user["id"],
                            events="Message,ReadReceipt,HistorySync",
                            history=True,
                        )
                    history_status = "\n  📥 Message history: enabled"
                    break
        except Exception:
            pass

    return (
        "✅ Configuration saved!\n"
        f"  URL: {base_url}\n"
        f"  Token: {token[:4]}{'*' * (len(token) - 4) if len(token) > 4 else ''}"
        f"{history_status}\n"
        "Now use whatsapp_connect() to connect to WhatsApp."
    )


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────


# Wrapper to add audit logging to all tools automatically
def _wrap_tools():
    for name, tool in mcp._tools.items():
        original_func = tool.fn
        async def wrapped(*args, _name=name, _func=original_func, **kwargs):
            PrivacyGuard.log_action(_name, kwargs)
            return await _func(*args, **kwargs)
        tool.fn = wrapped

# Note: FastMCP might handle tool calls differently, but this is the general idea.
# For now, we will add manual logging to key tools.

def main():
    """Run the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
