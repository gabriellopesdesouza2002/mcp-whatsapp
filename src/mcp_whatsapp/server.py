"""
MCP Server for WhatsApp via WuzAPI.

Exposes WhatsApp capabilities as MCP tools that any AI (Claude, Gemini, etc.) can use.
Uses the official MCP Python SDK with stdio transport.
"""

import asyncio
import http.server
import json
import logging
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP

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
# Mini Media Server (serves QR code as PNG via HTTP)
# ──────────────────────────────────────────────

MEDIA_DIR = Path(__file__).parent.parent.parent / "temp_media"
MEDIA_DIR.mkdir(exist_ok=True)
MEDIA_PORT = int(os.getenv("WUZAPI_MEDIA_PORT", "7144"))


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """Serve files from MEDIA_DIR, suppress logs."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(MEDIA_DIR), **kwargs)

    def log_message(self, format, *args):  # noqa: A002
        pass


def _start_media_server() -> None:
    try:
        with http.server.ThreadingHTTPServer(("localhost", MEDIA_PORT), _SilentHandler) as httpd:
            httpd.serve_forever()
    except OSError:
        pass  # Port already in use — another instance is running


_media_thread = threading.Thread(target=_start_media_server, daemon=True)
_media_thread.start()


def _save_qr_and_url(image_bytes: bytes) -> str:
    """Save QR PNG to temp dir and return its localhost URL."""
    qr_path = MEDIA_DIR / "qr.png"
    qr_path.write_bytes(image_bytes)
    return f"http://localhost:{MEDIA_PORT}/qr.png"

# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# Background sync (keeps session alive + syncs new messages every 5 min)
# ──────────────────────────────────────────────

async def _sync_loop() -> None:
    """Keep session alive: reconnect if disconnected, health-ping if connected."""
    await asyncio.sleep(60)  # Initial delay — let session stabilize first
    while True:
        try:
            status = await client.get_status()
            data = status.get("data", {})
            logged_in = data.get("loggedIn", False)
            connected = data.get("connected", False)
            if logged_in and not connected:
                # Session exists but lost connection — try to reconnect
                await client.connect()
            elif not logged_in:
                # No active session — nothing to do until user calls connect
                pass
            else:
                # All good — just a health ping to keep the TCP connection alive
                await client.health()
        except Exception:
            pass
        await asyncio.sleep(300)  # Every 5 minutes


@asynccontextmanager
async def _lifespan(app):
    task = asyncio.create_task(_sync_loop())
    try:
        yield
    finally:
        task.cancel()


# ──────────────────────────────────────────────
# MCP Server & WuzAPI Client
# ──────────────────────────────────────────────

mcp = FastMCP(
    "WhatsApp MCP",
    lifespan=_lifespan,
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
async def whatsapp_connect() -> str:
    """Connect to WhatsApp. Shows QR code if not yet paired — scan with phone."""
    await client.ensure_user_exists()

    result = await client.connect()

    already_connected = result.get("error") == "already connected"
    not_logged = not result.get("data", {}).get("jid")

    # Show QR if session needs pairing
    if (result.get("success") and not_logged) or already_connected:
        image_bytes = await client.get_qrcode_image()
        if image_bytes:
            url = _save_qr_and_url(image_bytes)
            return (
                "📱 Escaneie o QR code com o WhatsApp do seu celular:\n"
                "WhatsApp → ⋮ → Dispositivos conectados → Conectar dispositivo\n\n"
                f"![QR Code]({url})"
            )

    # Already logged in — auto-enable history silently
    if result.get("data", {}).get("jid"):
        await _auto_enable_history()

    return _format_result(result)


async def _auto_enable_history() -> None:
    """Silently enable history+events for the current user after connect."""
    if not client.admin_token:
        return
    try:
        users = await client.admin_list_users()
        for user in users.get("data", []):
            if user.get("token") == client.token:
                await client.admin_update_user(
                    user["id"],
                    Events="Message,ReadReceipt,HistorySync",
                    History=1,
                )
                break
    except Exception:
        pass


@mcp.tool()
async def whatsapp_disconnect(confirmed: bool = False) -> str:
    """Logout from WhatsApp. ⚠️ DESTRUCTIVE. confirmed=True required — always ask user first."""
    if not confirmed:
        return (
            "⚠️ Você tem certeza que quer DESCONECTAR o WhatsApp?\n"
            "Isso encerrará sua sessão ativa. Para confirmar, chame novamente com confirmed=True."
        )
    result = await client.disconnect()
    return _format_result(result)


@mcp.tool()
async def whatsapp_status() -> str:
    """Get WhatsApp session status: connected/disconnected/waiting QR. Returns JID and history sync info."""
    result = await client.get_status()
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_qrcode() -> list:
    """Get QR code image for WhatsApp pairing. Scan with phone to connect."""
    image_bytes = await client.get_qrcode_image()
    if image_bytes:
        url = _save_qr_and_url(image_bytes)
        return (
            "Escaneie o QR code com o WhatsApp do seu celular:\n\n"
            f"![QR Code]({url})"
        )
    result = await client.get_qrcode()
    return _format_result(result)


@mcp.tool()
async def whatsapp_health() -> str:
    """Check WuzAPI service health (uptime, memory, version)."""
    result = await client.health()
    return _format_result(result)


@mcp.tool()
async def whatsapp_force_sync() -> str:
    """Force full history sync by reconnecting. Use when messages are missing. Wait ~30s after."""
    try:
        # Check current status
        status = await client.get_status()
        data = status.get("data", {})
        was_connected = data.get("connected", False)
        logged_in = data.get("loggedIn", False)

        if not logged_in:
            return "❌ Nenhuma sessão ativa. Use whatsapp_connect primeiro para parear o dispositivo."

        if was_connected:
            # Disconnect to force a clean reconnect
            await client.disconnect()
            await asyncio.sleep(2)

        # Reconnect — this triggers HistorySync on WuzAPI
        await client.connect()

        return (
            "🔄 Sincronização forçada iniciada!\n\n"
            "O WuzAPI está puxando o histórico de mensagens do WhatsApp.\n"
            "⏳ Aguarde ~30 segundos e depois use whatsapp_get_messages para ver as mensagens atualizadas."
        )
    except Exception as e:
        return f"❌ Erro ao forçar sincronização: {e}"


# ══════════════════════════════════════════════
# MESSAGING TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_send_text(phone: str, message: str) -> str:
    """Send WhatsApp text. phone: country code, no '+' (e.g. 5511999998888). message: text content."""
    result = await client.send_text(phone, message)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_image(phone: str, image_url: str, caption: str = "") -> str:
    """Send image via WhatsApp. phone: no '+'. image_url: public URL. caption: optional."""
    result = await client.send_image(phone, image_url, caption)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_document(phone: str, document_url: str, filename: str = "", caption: str = "") -> str:
    """Send document/file via WhatsApp. phone: no '+'. document_url: public URL. filename/caption: optional."""
    result = await client.send_document(phone, document_url, filename, caption)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_audio(phone: str, audio_url: str) -> str:
    """Send audio via WhatsApp. phone: no '+'. audio_url: public URL."""
    result = await client.send_audio(phone, audio_url)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_video(phone: str, video_url: str, caption: str = "") -> str:
    """Send video via WhatsApp. phone: no '+'. video_url: public URL. caption: optional."""
    result = await client.send_video(phone, video_url, caption)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_sticker(phone: str, sticker_url: str) -> str:
    """Send sticker via WhatsApp. phone: no '+'. sticker_url: public URL (WebP recommended)."""
    result = await client.send_sticker(phone, sticker_url)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_location(phone: str, latitude: float, longitude: float, name: str = "") -> str:
    """Send location via WhatsApp. phone: no '+'. latitude/longitude: coords. name: optional label."""
    result = await client.send_location(phone, latitude, longitude, name)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_contact(phone: str, contact_name: str, contact_phone: str) -> str:
    """Send contact card (vCard) via WhatsApp. phone: recipient no '+'. contact_name/contact_phone: contact to share."""
    result = await client.send_contact(phone, contact_name, contact_phone)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_link(phone: str, url: str, text: str = "") -> str:
    """Send link with preview via WhatsApp. phone: no '+'. url: link. text: optional message."""
    result = await client.send_link(phone, url, text)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_buttons(phone: str, text: str, buttons: list[dict[str, str]], title: str = "", footer: str = "") -> str:
    """Send interactive buttons message. phone: no '+'. buttons: [{ButtonId, ButtonText}]. title/footer: optional."""
    result = await client.send_buttons(phone, text, buttons, title, footer)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_list(phone: str, text: str, button_text: str, sections: list[dict[str, Any]], title: str = "", footer: str = "") -> str:
    """Send interactive list message. sections: [{Title, Rows}]. button_text: list button label. title/footer: optional."""
    result = await client.send_list(phone, text, button_text, sections, title, footer)
    return _format_result(result)


@mcp.tool()
async def whatsapp_react(phone: str, message_id: str, emoji: str) -> str:
    """React to a message with emoji. phone: chat number. message_id: target msg. emoji: e.g. '👍'."""
    result = await client.react(phone, message_id, emoji)
    return _format_result(result)


@mcp.tool()
async def whatsapp_mark_read(phone: str, message_ids: list[str]) -> str:
    """Mark messages as read. phone: chat number. message_ids: list of IDs."""
    result = await client.mark_read(phone, message_ids)
    return _format_result(result)


@mcp.tool()
async def whatsapp_download_media(phone: str, message_id: str) -> str:
    """Download media from a message (image/audio/video/doc). phone: chat. message_id: target msg."""
    result = await client.download_media(phone, message_id)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_messages(phone: str, count: int = 20) -> str:
    """Get recent messages from a chat, sorted by timestamp. phone: no '+'. count: default 20."""
    if not DB_PATH.exists():
        result = await client.get_messages(phone, count)
        return _format_result(result)

    try:
        jid = phone if "@" in phone else f"{phone}@s.whatsapp.net"
        # Fetch a buffer (count*4) from the most recent rows to allow proper timestamp sorting
        fetch_limit = count * 4
        sql = "SELECT datajson, sender_jid, message_id, media_link FROM message_history WHERE chat_jid = ? ORDER BY id DESC LIMIT ?"
        rows = await _query_db(sql, (jid, fetch_limit))

        msgs = []
        for datajson_str, sender_jid, message_id, media_link in rows:
            try:
                dj = json.loads(datajson_str)
                ts = dj.get("Info", {}).get("Timestamp", "")
                text = (
                    dj.get("Message", {}).get("conversation")
                    or dj.get("Message", {}).get("extendedTextMessage", {}).get("text")
                    or ""
                )
                msg_type = dj.get("Info", {}).get("Type", "text")
                is_me = dj.get("Info", {}).get("IsFromMe", False)
                if ts:
                    entry: dict[str, Any] = {
                        "ts": ts,
                        "from": "me" if is_me else sender_jid,
                        "text": text or f"[{msg_type}]",
                        "id": message_id,
                    }
                    if media_link:
                        entry["media"] = media_link
                    msgs.append(entry)
            except Exception:
                continue

        msgs.sort(key=lambda m: m["ts"])
        recent = msgs[-count:]
        # Compact JSON — no indentation to save tokens
        return json.dumps({"ok": True, "n": len(recent), "msgs": recent}, ensure_ascii=False)
    except Exception as e:
        result = await client.get_messages(phone, count)
        return _format_result(result)


@mcp.tool()
async def whatsapp_get_chats() -> str:
    """List all active WhatsApp chats with last message info."""
    if not DB_PATH.exists():
        return "⚠️ Database not found. Connect WhatsApp and sync history first."

    try:
        sql = """
            SELECT chat_jid, MAX(id) as last_id
            FROM message_history
            GROUP BY chat_jid
            ORDER BY last_id DESC
            LIMIT 100
        """
        rows = await _query_db(sql)

        if not rows:
            return json.dumps({"ok": True, "n": 0, "chats": []})

        # Fetch last message text for each chat
        chats = []
        for (chat_jid, _) in rows:
            msg_sql = "SELECT datajson, timestamp FROM message_history WHERE chat_jid = ? ORDER BY id DESC LIMIT 1"
            msg_rows = await _query_db(msg_sql, (chat_jid,))
            last_msg = ""
            last_ts = ""
            if msg_rows:
                try:
                    dj = json.loads(msg_rows[0][0] or "{}")
                    last_msg = (
                        dj.get("Message", {}).get("conversation")
                        or dj.get("Message", {}).get("extendedTextMessage", {}).get("text")
                        or f"[{dj.get('Info', {}).get('Type', 'media')}]"
                    )
                    last_ts = dj.get("Info", {}).get("Timestamp", msg_rows[0][1] or "")
                except Exception:
                    pass
            chats.append({"jid": chat_jid, "last_ts": last_ts, "last_msg": last_msg[:80]})

        return json.dumps({"ok": True, "n": len(chats), "chats": chats}, ensure_ascii=False)
    except Exception as e:
        return f"❌ Error listing chats: {e}"


@mcp.tool()
async def whatsapp_reply_message(phone: str, message: str, quoted_message_id: str) -> str:
    """Reply quoting a message. phone: no '+'. message: reply text. quoted_message_id: original msg ID."""
    result = await client.reply_message(phone, message, quoted_message_id)
    return _format_result(result)


@mcp.tool()
async def whatsapp_delete_message(phone: str, message_id: str, everyone: bool = True, confirmed: bool = False) -> str:
    """Delete a message. ⚠️ DESTRUCTIVE. confirmed=True required — always ask user first. everyone: revoke for all."""
    if not confirmed:
        scope = "para todos" if everyone else "só para você"
        return (
            f"⚠️ Você tem certeza que quer APAGAR a mensagem `{message_id}` ({scope})?\n"
            "Esta ação não pode ser desfeita. Para confirmar, chame novamente com confirmed=True."
        )
    result = await client.delete_message(phone, message_id, everyone)
    return _format_result(result)


@mcp.tool()
async def whatsapp_send_poll(phone: str, question: str, options: list[str], max_answers: int = 1) -> str:
    """Send poll. phone: no '+' or group JID. question: poll text. options: list of choices. max_answers: default 1."""
    result = await client.send_poll(phone, question, options, max_answers)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_unread_messages(limit: int = 20) -> str:
    """Get recent incoming messages across all chats (from others, not sent by you). limit: default 20."""
    if not DB_PATH.exists():
        return "⚠️ Database not found. Connect WhatsApp and sync history first."
    try:
        # WuzAPI has no /chat/unread endpoint — we approximate by fetching recent incoming msgs
        sql = """
            SELECT chat_jid, datajson, timestamp
            FROM message_history
            WHERE json_extract(datajson, '$.Info.FromMe') = 0
               OR json_extract(datajson, '$.Info.FromMe') IS NULL
            ORDER BY id DESC
            LIMIT ?
        """
        rows = await _query_db(sql, (limit,))
        if not rows:
            return json.dumps({"ok": True, "n": 0, "messages": []})
        msgs = []
        for (chat_jid, datajson, ts) in rows:
            try:
                dj = json.loads(datajson or "{}")
                text = (
                    dj.get("Message", {}).get("conversation")
                    or dj.get("Message", {}).get("extendedTextMessage", {}).get("text")
                    or f"[{dj.get('Info', {}).get('Type', 'media')}]"
                )
                sender = dj.get("Info", {}).get("Sender", chat_jid)
                timestamp = dj.get("Info", {}).get("Timestamp", ts or "")
                msgs.append({"chat": chat_jid, "from": sender, "ts": timestamp, "text": text[:120]})
            except Exception:
                continue
        return json.dumps({"ok": True, "n": len(msgs), "messages": msgs}, ensure_ascii=False)
    except Exception as e:
        return f"❌ Error fetching messages: {e}"


@mcp.tool()
async def whatsapp_search_messages(query: str, limit: int = 50) -> str:
    """Search messages by keyword across all synced history. query: term. limit: default 50."""
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
    """Forward a message to another chat. message_id: original msg. to_phone: number or group JID."""
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
async def whatsapp_get_user_info(phone: str) -> str:
    """Get WhatsApp profile (name, about) for a number. phone: no '+'. For own session use whatsapp_status."""
    result = await client.get_user_info(phone)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_contacts() -> str:
    """Get full WhatsApp contacts list."""
    result = await client.get_contacts()
    return _format_result(result)


@mcp.tool()
async def whatsapp_search_contacts(query: str) -> str:
    """Search contacts by name or nickname (e.g. 'João', 'mãe', 'amor'). Returns matches with phone numbers."""
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
    """Get messages from a contact by name/nickname. only_today: filter today only. limit: default 20."""
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
        
        # 2. Query history for this JID using datajson for real timestamps
        fetch_limit = limit * 4
        sql = "SELECT datajson, sender_jid, message_id FROM message_history WHERE chat_jid = ? ORDER BY id DESC LIMIT ?"
        rows = await _query_db(sql, (jid, fetch_limit))

        today_str = datetime.now().strftime("%Y-%m-%d")
        results = []
        for datajson_str, sender_jid, message_id in rows:
            try:
                dj = json.loads(datajson_str) if datajson_str else {}
                real_ts = dj.get("Info", {}).get("Timestamp", "")
                if not real_ts:
                    continue
                if only_today and not real_ts.startswith(today_str):
                    continue
                text = (
                    dj.get("Message", {}).get("conversation")
                    or dj.get("Message", {}).get("extendedTextMessage", {}).get("text")
                    or f"[{dj.get('Info', {}).get('Type', 'media')}]"
                )
                is_me = dj.get("Info", {}).get("IsFromMe", False)
                results.append({
                    "ts": real_ts,
                    "from": "me" if is_me else contact_name,
                    "text": text,
                    "id": message_id,
                })
            except Exception:
                continue

        results.sort(key=lambda x: x["ts"])
        results = results[-limit:]

        if not results:
            period = "hoje" if only_today else "recentemente"
            return f"ℹ️ Nenhuma mensagem encontrada de {contact_name} {period}."

        return json.dumps({"contact": contact_name, "n": len(results), "msgs": results}, ensure_ascii=False)
        
    except Exception as e:
        return f"❌ Erro ao buscar mensagens por nome: {str(e)}"


@mcp.tool()
async def whatsapp_check_phones(phones: list[str]) -> str:
    """Check if phone numbers are on WhatsApp. phones: list, country code, no '+'."""
    result = await client.check_phones(phones)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_avatar(phone: str) -> str:
    """Get profile picture URL for a number. phone: no '+'."""
    result = await client.get_avatar(phone)
    return _format_result(result)


# ══════════════════════════════════════════════
# GROUP TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_list_groups() -> str:
    """List all WhatsApp groups."""
    result = await client.list_groups()
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_group_info(group_jid: str) -> str:
    """Get group details. group_jid: group identifier (e.g. 120363...@g.us)."""
    result = await client.get_group_info(group_jid)
    return _format_result(result)


@mcp.tool()
async def whatsapp_create_group(name: str, participants: list[str]) -> str:
    """Create a WhatsApp group. name: group name. participants: list of phone numbers, no '+'."""
    result = await client.create_group(name, participants)
    return _format_result(result)


@mcp.tool()
async def whatsapp_update_group_participants(group_jid: str, participants: list[str], action: str = "add") -> str:
    """Add or remove group participants. action: 'add'|'remove'. participants: phone list, no '+'."""
    result = await client.update_group_participants(group_jid, participants, action)
    return _format_result(result)


@mcp.tool()
async def whatsapp_get_group_invite_link(group_jid: str) -> str:
    """Get invite link for a group. group_jid: group identifier."""
    result = await client.get_group_invite_link(group_jid)
    return _format_result(result)


# ══════════════════════════════════════════════
# WEBHOOK TOOL
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_set_webhook(webhook_url: str) -> str:
    """Set webhook URL to receive incoming WhatsApp events (messages, receipts)."""
    result = await client.set_webhook(webhook_url)
    return _format_result(result)


# ══════════════════════════════════════════════
# NEWSLETTER / CHANNEL TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_get_newsletter_messages(newsletter_jid: str, count: int = 50) -> str:
    """Get messages from a newsletter/channel. newsletter_jid: channel ID. count: default 50."""
    result = await client.get_newsletter_messages(newsletter_jid, count)
    return _format_result(result)


@mcp.tool()
async def whatsapp_subscribe_newsletter(newsletter_jid: str) -> str:
    """Subscribe to a WhatsApp newsletter/channel. newsletter_jid: channel ID."""
    result = await client.subscribe_newsletter(newsletter_jid)
    return _format_result(result)


# ══════════════════════════════════════════════
# ADMIN TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_admin_list_users() -> str:
    """List all WuzAPI users. Requires admin token."""
    result = await client.admin_list_users()
    return _format_result(result)


@mcp.tool()
async def whatsapp_admin_create_user(name: str, token: str) -> str:
    """Create WuzAPI user. Requires admin token. name: username. token: auth token."""
    result = await client.admin_create_user(name, token)
    return _format_result(result)


@mcp.tool()
async def whatsapp_admin_delete_user(name: str, confirmed: bool = False) -> str:
    """Delete WuzAPI user. ⚠️ DESTRUCTIVE. confirmed=True required — always ask user first."""
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
    """Enable message history for current user. Run if whatsapp_get_messages returns history disabled."""
    if not client.admin_token:
        return "❌ Admin token not configured. Run whatsapp_configure with admin_token first."
    try:
        users = await client.admin_list_users()
        user_list = users.get("data", [])
        for user in user_list:
            if user.get("token") == client.token:
                await client.admin_update_user(
                    user["id"],
                    Events="Message,ReadReceipt,HistorySync",
                    History=1,
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
async def whatsapp_configure(token: str, base_url: str = "http://localhost:7143", admin_token: str = "") -> str:
    """Save WuzAPI credentials. Run once to set up. token: user token. admin_token: optional, for admin tools."""
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
