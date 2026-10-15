# Install the open-source WhatsApp Reader MCP

This guide installs the read-only MCP server from the public repository. It does not include the commercial Agensi skill instructions.

## Requirements

Windows 10/11, macOS, or Linux; Python 3.11+; Docker with Compose; Git; Claude Code; and a WhatsApp account to pair.

## Start WuzAPI

```sh
git clone https://github.com/gabriellopesdesouza2002/mcp-whatsapp.git
cd mcp-whatsapp
```

Create `.env` from `.env.example` (`Copy-Item .env.example .env` in PowerShell; `cp .env.example .env` on macOS/Linux). Set separate random values for both API tokens, a **32-character** `WUZAPI_GLOBAL_ENCRYPTION_KEY`, and a `WUZAPI_GLOBAL_HMAC_KEY` of at least 32 characters. Generate tokens/HMAC locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and the exact-length encryption key with `python -c "import secrets; print(secrets.token_hex(16))"`. The encryption/HMAC keys must persist across restarts or stored WuzAPI data may become unreadable. Keep `.env` private and do not paste tokens into chat. Start WuzAPI:

```sh
docker compose up -d
```

The WuzAPI HTTP port is bound to loopback only. Compose pins WuzAPI 1.0.9 and disables container log capture because this release includes user tokens in INFO records. Use the container health status and local `/health` endpoint for diagnostics; do not expose the API to the internet.

## Install and pair

```sh
python -m venv .venv
```

Activate the environment (`.venv\Scripts\Activate.ps1` on PowerShell, `source .venv/bin/activate` on macOS/Linux) and install:

```sh
python -m pip install --upgrade pip
python -m pip install -e .
```

Pair and configure the WuzAPI user through the original full MCP or WuzAPI's local setup procedure **before** connecting the Reader. Do not attach the full MCP to a Reader session. The Reader has no QR, connect, configure, session-management, or force-sync tools. Wait for message history synchronization to complete; initial history may be delayed.

## Configure Claude Code

Register only the Reader entry. Example command on Windows (use the absolute clone path):

```powershell
claude mcp add whatsapp-reader -- "C:\path\to\mcp-whatsapp\.venv\Scripts\mcp-whatsapp-reader.exe"
```

Set the environment variables `WUZAPI_BASE_URL=http://127.0.0.1:7143`, `WUZAPI_TOKEN` to the local WuzAPI user token, and `WUZAPI_PRIVACY_MODE=true` in Claude Code's local MCP configuration. The Reader does not need the admin token. Keep local config containing tokens out of Git and logs. On macOS/Linux, use `.venv/bin/mcp-whatsapp-reader` as the command.

Restart Claude Code, inspect the connected server's tool list, and confirm it is `whatsapp-reader`. A basic setup check is to request WhatsApp status; a history check is to list recent chats. Without a paired account and synced history, history queries may return empty results. Report stale warnings rather than claiming the data is current or complete.

## Troubleshooting and data handling

- **Server fails to launch:** confirm Python 3.11+, activate the environment, and rerun `python -m pip install -e .`.
- **Connection refused:** check `docker compose ps`, the local `http://127.0.0.1:7143/health` endpoint, and local port 7143. Container log capture is intentionally disabled to prevent credential leakage.
- **Unauthorized:** check the configured WuzAPI user token locally; do not expose the admin token.
- **Empty/stale history:** check pairing and sync status, allow time for backfill, and retry later. This entry cannot force synchronization.

WuzAPI uses an unofficial WhatsApp Web integration and may break or result in service/account restrictions. The WuzAPI token is not inherently read-only; read-only isolation comes from the tools registered in this MCP entry. Message contents returned to the model are processed by the AI provider configured in Claude Code, according to that provider's data handling. No automatic LGPD/GDPR compliance is asserted.
