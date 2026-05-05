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

def _check_staleness(most_recent_ts: str | None) -> dict[str, Any] | None:
    """Return a stale_warning dict if most_recent_ts is older than 12 hours, else None."""
    if not most_recent_ts:
        return None
    try:
        # Handle ISO format: "2025-03-18T14:30:00" or "2025-03-18 14:30:00" or date-only
        ts_clean = most_recent_ts[:19].replace("T", " ")
        msg_dt = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S")
        age_hours = (datetime.now() - msg_dt).total_seconds() / 3600
        if age_hours > 12:
            age_str = f"{int(age_hours // 24)} dias" if age_hours >= 24 else f"{int(age_hours)} horas"
            return {
                "stale": True,
                "most_recent_message": most_recent_ts[:10],
                "age": age_str,
                "suggestion": (
                    f"Os dados do banco local têm {age_str} de atraso "
                    f"(última mensagem: {most_recent_ts[:10]}). "
                    "Posso buscar diretamente do WhatsApp agora sem depender da sincronização — deseja?"
                ),
            }
    except Exception:
        pass
    return None


def _format_result(result: dict[str, Any]) -> str:
    """Format API response as readable JSON string with Privacy Guardrails."""
    json_str = json.dumps(result, indent=2, ensure_ascii=False)
    return PrivacyGuard.redact(json_str)


async def _query_db(sql: str, params: tuple = (), limit: int = 50) -> list[Any]:
    """Query the SQLite DB via SQLite's built-in backup API (safe on Windows WAL locks)."""
    import time
    import tempfile

    retries = 3
    last_error = None
    tmp_dir = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_dir, f"mcp_whatsapp_shadow_{int(time.time() * 1000)}.db")

    for i in range(retries):
        backup_conn = None
        src_conn = None
        try:
            # SQLite's .backup() handles WAL mode correctly without copying files manually
            src_conn = sqlite3.connect(str(DB_PATH), timeout=5)
            backup_conn = sqlite3.connect(tmp_path)
            src_conn.backup(backup_conn)
            src_conn.close()
            src_conn = None

            cursor = backup_conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            backup_conn.close()
            backup_conn = None
            return rows
        except Exception as e:
            last_error = e
            if src_conn:
                try: src_conn.close()
                except: pass
            if backup_conn:
                try: backup_conn.close()
                except: pass
            if i < retries - 1:
                time.sleep(0.5 * (i + 1))
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

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

_SERVER_START_TIME = datetime.now()

mcp = FastMCP(
    "WhatsApp MCP",
    lifespan=_lifespan,
        instructions=(
        f"Current date and time (server startup): {_SERVER_START_TIME.strftime('%Y-%m-%d %H:%M')} "
        f"(timezone: America/Sao_Paulo, Brazil).\n"
        "Always use this as today's reference date when calculating ages, deadlines or comparing timestamps. "
        "Use whatsapp_get_datetime() to get a refreshed timestamp if the session has been running for a while.\n\n"
        "MCP server that provides WhatsApp messaging capabilities via WuzAPI. "
        "Use whatsapp_connect() first to start a session, then use messaging tools to send/receive messages.\n\n"
        "--- CONTACTS & NAMES (MANDATORY) ---\n"
        "If the user asks to send a message to someone by name, nickname, or association (e.g., 'Silvandes', 'minha namorada', 'amor'), "
        "DO NOT ask them for the phone number. You MUST use whatsapp_search_contacts first to find the number automatically, "
        "then use the corresponding messaging tool (whatsapp_send_text, etc.) with the found number. "
        "Only ask for the phone number if the search fails to find a match.\n\n"
        "--- STALE DATA GUARDRAIL (MANDATORY) ---\n"
        "When ANY tool response contains a 'stale_warning' field, you MUST:\n"
        "1. Show the user the most recent message date from stale_warning['most_recent_message']\n"
        "2. Use the exact text from stale_warning['suggestion'] to offer a live fetch\n"
        "3. If the user says yes/sim/pode/quero, call whatsapp_get_messages with the phone/JID "
        "   BUT pass fetch_live=True concept: actually call client.get_messages() by NOT using "
        "   the DB path — to do this, use whatsapp_force_sync first, then retry whatsapp_get_messages.\n"
        "Example response when stale: 'Olha, os dados que tenho são do banco local e a última "
        "mensagem é de [data]. Posso buscar diretamente do WhatsApp agora sem depender da "
        "sincronização — deseja?'\n\n"
        "--- DELETION GUARDRAIL (MANDATORY) ---\n"
        "⚠️  NEVER call whatsapp_reset_session, whatsapp_delete_message, whatsapp_admin_delete_user, or whatsapp_disconnect "
        "without EXPLICITLY asking the user for confirmation first. Show what will be affected "
        "and wait for a clear 'yes', 'confirm' or 'pode apagar' response before proceeding. "
        "If the user did not explicitly confirm, return a warning and do NOT call the tool.\n\n"
        "--- SESSION RESET ---\n"
        "When user wants to 'apagar sessão', 'resetar', 'conectar do zero', 'limpar tudo', "
        "use whatsapp_reset_session (NOT whatsapp_disconnect). "
        "whatsapp_disconnect only disconnects but keeps session files. "
        "whatsapp_reset_session actually deletes session files and generates a fresh QR code.\n\n"
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
    """Get WhatsApp session status (connected/disconnected/QR pending) and WuzAPI service health."""
    status = await client.get_status()
    try:
        health = await client.health()
        status_data = status.get("data", {})
        health_data = health.get("data", {}) if isinstance(health.get("data"), dict) else {}
        return json.dumps({
            "connected": status_data.get("connected"),
            "loggedIn": status_data.get("loggedIn"),
            "jid": status_data.get("jid"),
            "health": health_data,
        }, ensure_ascii=False)
    except Exception:
        return _format_result(status)


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
async def whatsapp_get_datetime() -> str:
    """Get current date and time (Brazil/São Paulo). Use to know today's date before comparing message timestamps."""
    now = datetime.now()
    return json.dumps({
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "weekday": now.strftime("%A"),
        "timezone": "America/Sao_Paulo",
    }, ensure_ascii=False)



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

@mcp.tool()
async def whatsapp_reset_session(confirmed: bool = False) -> str:
    """Reset WhatsApp session completely — logout + delete session files + fresh QR. ⚠️ DESTRUCTIVE. confirmed=True required."""
    if not confirmed:
        return (
            "⚠️ Isso vai DESCONECTAR e APAGAR sua sessão do WhatsApp completamente.\n"
            "Você precisará escanear o QR code novamente para reconectar.\n"
            "Para confirmar, chame novamente com confirmed=True."
        )

    # 1. Disconnect — ends active session (clears WuzAPI state)
    logout_result = await client.disconnect()

    # 2. If admin, delete and recreate user for clean state
    cleanup_note = ""
    if client.admin_token:
        try:
            await client.admin_delete_user("mcp_whatsapp_user")
            await asyncio.sleep(1)
            await client.ensure_user_exists()
            cleanup_note = "\n  🗑️ Usuário WuzAPI recriado."
        except Exception as e:
            cleanup_note = f"\n  ⚠️ Limpeza do usuário: {e}"

    # 3. Wait for WuzAPI to be ready
    await asyncio.sleep(2)

    # 4. Connect — triggers new QR code generation
    connect_result = await client.connect()

    # 5. Show QR code
    image_bytes = await client.get_qrcode_image()
    if image_bytes:
        url = _save_qr_and_url(image_bytes)
        return (
            "🔄 Sessão apagada completamente! Escaneie o novo QR code:\n"
            "WhatsApp → ⋮ → Dispositivos conectados → Conectar dispositivo\n\n"
            f"![QR Code]({url})"
            f"{cleanup_note}"
        )

    return _format_result({"logout": logout_result, "connect": connect_result, "note": cleanup_note})

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

        result: dict[str, Any] = {"ok": True, "n": len(recent), "msgs": recent}

        # Detectar se os dados estão velhos
        most_recent_ts = recent[-1]["ts"] if recent else None
        stale = _check_staleness(most_recent_ts)
        if stale:
            result["stale_warning"] = stale

        return json.dumps(result, ensure_ascii=False)
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

        # Build contact name lookup from WuzAPI contacts
        jid_to_name: dict[str, str] = {}
        try:
            contacts_result = await client.get_contacts()
            contacts_raw = contacts_result.get("data", {})
            if isinstance(contacts_raw, dict):
                for c_jid, c in contacts_raw.items():
                    name = (
                        c.get("FullName") or c.get("PushName")
                        or c.get("FirstName") or c.get("BusinessName") or ""
                    )
                    if name:
                        jid_to_name[c_jid] = name
        except Exception:
            pass  # names are best-effort — chats still returned without them

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
            phone = chat_jid.split("@")[0] if "@" in chat_jid else chat_jid
            name = jid_to_name.get(chat_jid, "")
            chats.append({
                "jid": chat_jid,
                "phone": phone,
                "name": name,
                "last_ts": last_ts,
                "last_msg": last_msg[:80],
            })

        result: dict[str, Any] = {"ok": True, "n": len(chats), "chats": chats}

        # Detectar se os dados estão velhos
        most_recent = max((c["last_ts"] for c in chats if c["last_ts"]), default=None)
        stale = _check_staleness(most_recent)
        if stale:
            result["stale_warning"] = stale

        return json.dumps(result, ensure_ascii=False)
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
        # WuzAPI has no /chat/unread endpoint — fetch recent rows and filter in Python
        # (avoids json_extract which may not be available on older SQLite builds on Windows)
        sql = """
            SELECT chat_jid, datajson, timestamp
            FROM message_history
            ORDER BY id DESC
            LIMIT ?
        """
        # Over-fetch so we have enough after filtering out our own messages
        rows = await _query_db(sql, (limit * 6,))
        if not rows:
            return json.dumps({"ok": True, "n": 0, "messages": []})
        msgs = []
        for (chat_jid, datajson, ts) in rows:
            if len(msgs) >= limit:
                break
            try:
                dj = json.loads(datajson or "{}")
                # Use IsFromMe (correct WuzAPI key) — skip our own messages
                if dj.get("Info", {}).get("IsFromMe", False):
                    continue
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
        result: dict[str, Any] = {"ok": True, "n": len(msgs), "messages": msgs}

        # Detectar se os dados estão velhos
        most_recent_ts = msgs[0]["ts"] if msgs else None  # já ordenado DESC
        stale = _check_staleness(most_recent_ts)
        if stale:
            result["stale_warning"] = stale

        return json.dumps(result, ensure_ascii=False)
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
async def whatsapp_search_contacts(query: str) -> str:
    """Search contacts by name or nickname. Falls back to recent chat history when not found in phone book."""
    result = await client.get_contacts()
    contacts_raw = result.get("data", {})

    # Diagnose what the contacts endpoint actually returned
    contacts_count = len(contacts_raw) if isinstance(contacts_raw, dict) else 0
    contacts_status = (
        f"phone_book_contacts={contacts_count}"
        if contacts_count > 0
        else f"phone_book_empty (raw={str(result)[:120]})"
    )

    q = query.lower().strip()
    matches = []

    if isinstance(contacts_raw, dict):
        for jid, contact in contacts_raw.items():
            full_name = contact.get("FullName") or ""
            push_name = contact.get("PushName") or ""
            first_name = contact.get("FirstName") or ""
            business_name = contact.get("BusinessName") or ""
            all_names = f"{full_name} {push_name} {first_name} {business_name}".lower()
            if q in all_names:
                phone = jid.split("@")[0] if "@" in jid else jid
                matches.append({
                    "name": full_name or push_name or first_name or business_name,
                    "phone": phone,
                    "jid": jid,
                })

    if matches:
        return _format_result({"success": True, "count": len(matches), "data": matches, "source": "phone_book"})

    # Fallback: extract push names from datajson in local SQLite (no extra API calls)
    if not DB_PATH.exists():
        return f"❌ '{query}' not found in contacts and message history is unavailable."

    try:
        sql = """
            SELECT chat_jid, datajson, text_content, timestamp
            FROM message_history
            WHERE chat_jid NOT LIKE '%@g.us'
            ORDER BY id DESC
            LIMIT 1000
        """
        rows = await _query_db(sql)

        # Build one entry per unique DM JID, extracting push name from incoming messages
        seen: dict[str, dict] = {}
        for (chat_jid, datajson_raw, text, ts) in rows:
            phone = chat_jid.split("@")[0] if "@" in chat_jid else chat_jid
            if chat_jid not in seen:
                seen[chat_jid] = {
                    "phone": phone,
                    "name": "",
                    "last_message_preview": (text or "")[:60],
                    "last_ts": (ts or "")[:10],
                }
            # Fill name from the first incoming message we find for this JID
            if not seen[chat_jid]["name"] and datajson_raw:
                try:
                    dj = json.loads(datajson_raw)
                    info = dj.get("Info", {})
                    # Only use push name from incoming messages
                    if not info.get("IsFromMe", False):
                        push = info.get("PushName") or dj.get("SenderName") or ""
                        if push:
                            seen[chat_jid]["name"] = push
                except Exception:
                    pass

        name_matches = [
            e for e in seen.values()
            if q in e["name"].lower() or q in e["phone"]
        ]

        if name_matches:
            return json.dumps({
                "found": True,
                "count": len(name_matches),
                "data": name_matches,
            }, ensure_ascii=False)

        # Still not found — return list with names so user can identify
        displayable = [e for e in seen.values() if e["name"] or e["phone"].isdigit()][:30]
        return json.dumps({
            "found": False,
            "contacts_endpoint": contacts_status,
            "message": (
                f"'{query}' não encontrado nos contatos nem no histórico de conversas. "
                "Exibindo conversas recentes com nome e telefone para identificação. "
                "Se nenhum corresponder, pergunte ao usuário: "
                f"'Não encontrei '{query}' em nenhuma conversa ou contato. Você sabe o número de telefone dele(a)?'"
            ),
            "recent_chats": displayable,
        }, ensure_ascii=False)
    except Exception as e:
        return f"❌ '{query}' not found in contacts. History fallback failed: {e}"


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
# ADMIN TOOLS
# ══════════════════════════════════════════════


@mcp.tool()
async def whatsapp_admin(action: str, name: str = "", token: str = "", confirmed: bool = False) -> str:
    """WuzAPI admin operations. action: 'list_users'|'create_user'|'delete_user'|'enable_history'. Requires admin token."""
    if not client.admin_token:
        return "❌ Admin token not configured. Run whatsapp_configure with admin_token first."
    try:
        if action == "list_users":
            result = await client.admin_list_users()
            return _format_result(result)

        elif action == "create_user":
            if not name or not token:
                return "❌ create_user requires name and token params."
            result = await client.admin_create_user(name, token)
            return _format_result(result)

        elif action == "delete_user":
            if not name:
                return "❌ delete_user requires name param."
            if not confirmed:
                return (
                    f"⚠️ Confirma que quer DELETAR o usuário '{name}'? "
                    "Chame novamente com confirmed=True."
                )
            result = await client.admin_delete_user(name)
            return _format_result(result)

        elif action == "enable_history":
            users = await client.admin_list_users()
            user_list = users.get("data", [])
            for user in user_list:
                if user.get("token") == client.token:
                    await client.admin_update_user(
                        user["id"],
                        Events="Message,ReadReceipt,HistorySync",
                        History=1,
                    )
                    return f"✅ Histórico ativado para '{user.get('name')}'! Use whatsapp_get_messages para ler."
            return "❌ Usuário com esse token não encontrado."

        else:
            return f"❌ Unknown action '{action}'. Valid: list_users, create_user, delete_user, enable_history."
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


def main() -> None:
    """Entry point for the mcp-whatsapp CLI and Claude Desktop."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
