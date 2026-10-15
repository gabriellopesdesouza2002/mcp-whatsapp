# AGENTS.md — MCP WhatsApp

Compact guidance for AI coding agents working in this repo.

## Project

MCP server that exposes 40+ WhatsApp tools via stdio transport. Backed by WuzAPI (Docker container running unofficial WhatsApp Web).

```
AI Client → MCP stdio → server.py (FastMCP) → wuzapi_client.py (httpx) → WuzAPI :7143 (Docker) → WhatsApp Web
```

## Two source files (both in `src/mcp_whatsapp/`)

| File | Role |
|------|------|
| `server.py` (~1220 lines) | All MCP tool definitions, FastMCP lifespan, PrivacyGuard, SQLite history reads, HTTP media server (port 7144), background sync loop |
| `wuzapi_client.py` (~444 lines) | Thin async HTTP wrapper — one method per WuzAPI endpoint |

## Commands (from CLAUDE.md, verified)

```bash
pip install -e .                 # install
pip install -e ".[test]"         # + test deps (mcp-use, langchain-groq, langchain-openai)
mcp-whatsapp                     # run dev server (stdio)
ruff check                       # lint (no local config — uses ruff defaults)
ruff check --fix
docker compose up -d             # start WuzAPI on localhost:7143
```

## Critical conventions

- **Phone numbers**: NO `+` prefix, e.g. `5511999998888`. Also used raw as JID component or suffixed `@s.whatsapp.net`.
- **JID formats**: `number@s.whatsapp.net` (DM), `number@g.us` (group), `number@lid`/`number@bot` (lid/bot — use full JID as phone).
- **Auth headers**: user token goes in `token` header; admin token goes in `Authorization` header (`_headers(admin=True)`).
- **Destructive ops**: `whatsapp_reset_session`, `whatsapp_delete_message`, `whatsapp_disconnect`, `whatsapp_admin_delete_user` all require `confirmed=True` — guard is enforced at tool level, not just prompt instructions.
- **DB reads**: SQLite backup API via `_query_db()` — copies WAL to a temp file first (safe on Windows), 3 retries.
- **Staleness guard**: `_check_staleness()` compares latest message timestamp vs 12h threshold. Returns `stale_warning` dict. Agent should offer `whatsapp_force_sync()` then retry.
- **Privacy mode**: when `WUZAPI_PRIVACY_MODE=true`, `PrivacyGuard.redact()` masks CPF, email, credit card, phone before returning. Audit log written to `logs/audit_privacy.log`.
- **`ensure_user_exists()`**: called on connect + configure — auto-creates WuzAPI user and enables `HistorySync` if admin_token available.
- **Background sync**: every 5 min via FastMCP lifespan. Reconnects if session lost, else health-pings.

## Architecture quirks

- **History via SQLite, not API**: `whatsapp_get_messages`, `whatsapp_get_chats`, `whatsapp_get_unread_messages`, `whatsapp_search_messages` all read `wuzapi_data/users.db` directly. Falls back to WuzAPI HTTP API if DB missing.
- **DB schema** (`message_history` table): `chat_jid`, `datajson` (JSON blob with `Info`, `Message` keys), `sender_jid`, `message_id`, `media_link`, `text_content`, `timestamp`, `message_type`.
- **Contact search fallback**: `whatsapp_search_contacts` queries `/user/contacts` first. If empty, falls back to scanning recent history in local SQLite for push names.
- **HTTP media server**: thread serving `temp_media/` on localhost:7144 (daemon, port-sharing tolerant). QR codes saved as PNG and served as URL for AI display.
- **Config loaded from `.env`** at repo root. `whatsapp_configure` rewrites it live and updates client state in memory.
- **`whatsapp_reset_session`**: full teardown (disconnect, wipe DB, clear media, delete+recreate WuzAPI user, generate fresh QR).
- **`whatsapp_health`**: queries WuzAPI `/health` endpoint (separate from session status).

## Testing

- **No pytest unit tests** — the only test file is `test_mcp.py` which is an integration harness.
- `test_mcp.py` runs the MCP server in a subprocess and talks to it via `mcp-use` + Groq/OpenAI.
- Run directly: `python test_mcp.py --test health` (or `--test all`, `--chat`).
- Requires `.env` with `WUZAPI_TOKEN` + `GROQ_API_KEY` or `OPENAI_API_KEY`.
- Slow (~90s timeout per call). Not part of CI.

## OMC (oh-my-claudecode)

This repo uses oh-my-claudecode for multi-agent orchestration. State lives in `.omc/`. Skills loaded on pattern match. See `.claude/` and `CLAUDE.md` for OMC config.
