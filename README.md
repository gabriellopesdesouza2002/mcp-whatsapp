<div align="center">

# 📱 MCP WhatsApp

**An open-source MCP server for connecting compatible AI assistants to WhatsApp through WuzAPI.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-Compatible-blueviolet)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/gabriellopesdesouza2002/mcp-whatsapp?style=social)](https://github.com/gabriellopesdesouza2002/mcp-whatsapp)

Designed for **MCP-compatible clients**, including Claude Code and compatible editor integrations. Client support depends on the MCP capabilities and configuration available in each application.

**Public MCP server source available under the MIT License.**

</div>

---

## ✨ What can you do?

Just talk to your AI naturally:

> *"Send a message to +5511999998888 saying the meeting is postponed"*
> 
> *"Send the invoice.pdf to the Clients group"*
> 
> *"Get the last 20 messages from my support chat"*
> 
> *"Create a group called 'Team Sprint' with these numbers"*

Once configured, supported actions can be requested through an MCP-compatible assistant. Some actions modify WhatsApp data or send messages, so review permissions and requests carefully.

**Important:** This project integrates with **WuzAPI, an unofficial WhatsApp Web interface**. It is not the official WhatsApp Business Platform or a Meta-approved integration. Account restrictions and compatibility changes are possible. See [Security & Responsible Use](#️-security--responsible-use).

---

## WhatsApp Reader (read-only)

This repository includes the open-source `mcp-whatsapp-reader` entry, which registers query-only tools separately from the full MCP. It does not include or expose the commercial WhatsApp Reader skill workflows; those are distributed in the Agensi ZIP.

```bash
python -m pip install -e .
mcp-whatsapp-reader
```

The Reader does not expose pairing, sending, deletion, session changes, or administration. Pair and synchronize using a separately controlled setup process before connecting the Reader. The WuzAPI user token itself is not inherently read-only. See [`docs/reader-install.md`](docs/reader-install.md) for local setup.

The creator's local package builder (`python scripts/package_whatsapp_reader.py`) reads the private, Git-ignored skill source and writes `dist/whatsapp-reader.zip`; neither source nor ZIP is part of the public Git history.

---

## 🚀 Quick Start (3 steps)

### Step 1 — Clone and start WuzAPI

```bash
git clone https://github.com/gabriellopesdesouza2002/mcp-whatsapp.git
cd mcp-whatsapp
# PowerShell: Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
# Edit .env and configure separate random tokens plus persistent encryption/HMAC keys
docker compose up -d
```

WuzAPI is bound to `127.0.0.1:7143` by the included Compose configuration. Before starting it, set `WUZAPI_ADMIN_TOKEN`, `WUZAPI_TOKEN`, `WUZAPI_GLOBAL_ENCRYPTION_KEY` and `WUZAPI_GLOBAL_HMAC_KEY` in your local `.env` file, as documented in [the Reader installation guide](docs/reader-install.md). Keep the encryption/HMAC keys persistent across restarts.

> Keep `.env` private. Never expose the WuzAPI port publicly or commit tokens.

### Step 2 — Install the MCP server

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -e .
```

### Step 3 — Register with Claude Code (global, one-time)

```bash
# Windows (replace with your absolute path)
claude mcp add whatsapp -s user -- C:\path\to\mcp-whatsapp\.venv\Scripts\python.exe -m mcp_whatsapp.server

# Linux / macOS (replace with your absolute path)
claude mcp add whatsapp -s user -- /path/to/mcp-whatsapp/.venv/bin/python -m mcp_whatsapp.server
```

Restart Claude, then just ask:

> Configure the local WhatsApp integration using the credentials already stored in my private configuration.

The full MCP includes setup tools, but pairing and authorization still require care. Avoid putting live tokens into chat messages.

---

## 🔑 Manual Configuration (Optional)

If you prefer to use the dashboard, it is available at `http://localhost:7143`.

1. Open `http://localhost:7143` → click **Admin Mode**
2. Enter the `WUZAPI_ADMIN_TOKEN` value from your private `.env`
3. Go to **Users → Create User**, enter any name and choose a token.

> Keep configuration and token values in your private local environment, not in shared chats or screenshots.

> 💡 **Two tokens, two purposes:**
> - **User token** → sends/receives messages — this is what you use daily
> - **Admin token** → manages users — only needed for `whatsapp_admin_*` tools

---

## 🔌 Connect to Your AI

### Claude Code (recommended)

```bash
claude mcp add whatsapp -s user -- \
  /path/to/.venv/bin/python -m mcp_whatsapp.server
```

Then ask Claude to configure:
> Configure my locally hosted WhatsApp connection (using local credentials).

### Claude Desktop

Add to `claude_desktop_config.json`:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "/path/to/mcp-whatsapp/.venv/bin/python",
      "args": ["-m", "mcp_whatsapp.server"],
      "env": {
        "WUZAPI_BASE_URL": "http://localhost:7143",
        "WUZAPI_TOKEN": "your_token_here"
      }
    }
  }
}
```

### Cursor / Windsurf / Continue.dev

Add to your editor's MCP config file:

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "/path/to/mcp-whatsapp/.venv/bin/python",
      "args": ["-m", "mcp_whatsapp.server"],
      "env": {
        "WUZAPI_BASE_URL": "http://localhost:7143",
        "WUZAPI_TOKEN": "your_token_here"
      }
    }
  }
}
```

### Any other MCP-compatible AI

- **Transport:** stdio (standard)
- **Command:** `python -m mcp_whatsapp.server`
- **Env vars:** `WUZAPI_BASE_URL`, `WUZAPI_TOKEN`

---

## 🛠️ Available Tools (30+)

| Category | Tools |
|----------|-------|
| **⚙️ Setup** | `whatsapp_configure` |
| **🔗 Session** | `whatsapp_connect`, `whatsapp_disconnect`, `whatsapp_status`, `whatsapp_get_qrcode`, `whatsapp_health` |
| **💬 Messages** | `whatsapp_send_text`, `whatsapp_send_image`, `whatsapp_send_document`, `whatsapp_send_audio`, `whatsapp_send_video`, `whatsapp_send_sticker`, `whatsapp_send_location`, `whatsapp_send_contact`, `whatsapp_send_link`, `whatsapp_send_buttons`, `whatsapp_send_list`, `whatsapp_send_poll` |
| **📥 Chat** | `whatsapp_get_chats`, `whatsapp_get_unread_messages`, `whatsapp_get_messages`, `whatsapp_reply_message`, `whatsapp_delete_message`, `whatsapp_react`, `whatsapp_mark_read`, `whatsapp_download_media`, `whatsapp_search_messages`, `whatsapp_forward_message` |
| **👤 Users** | `whatsapp_get_user_info`, `whatsapp_get_contacts`, `whatsapp_search_contacts`, `whatsapp_check_phones`, `whatsapp_get_avatar` |
| **👥 Groups** | `whatsapp_list_groups`, `whatsapp_get_group_info`, `whatsapp_create_group`, `whatsapp_update_group_participants`, `whatsapp_get_group_invite_link` |
| **🔔 Webhook** | `whatsapp_set_webhook` |
| **📢 Newsletter** | `whatsapp_get_newsletter_messages`, `whatsapp_subscribe_newsletter` |
| **🔐 Admin** | `whatsapp_admin_list_users`, `whatsapp_admin_create_user`, `whatsapp_admin_delete_user` |

---

## 💡 Usage Examples

### First-time setup

```
You: "Check my local WhatsApp configuration"
Claude: ✅ Configuration saved! Now use whatsapp_connect() to connect.

You: "Connect my WhatsApp"
Claude: [generates QR Code — scan with your phone]

You: "Check if WhatsApp is connected"
Claude: ✅ Connected as +5511999998888
```

### Sending messages

```
You: "Send 'Hello!' to +5511999998888"
You: "Send the file report.pdf to the Sales group"
You: "Send my location to +5511987654321"
You: "React with 👍 to the last message in chat 5511999998888@s.whatsapp.net"
```

### Managing groups

```
You: "List all my WhatsApp groups"
You: "Create a group 'Project X' with +5511111111111 and +5522222222222"
You: "Get the invite link for group 120363XXXXXXXX@g.us"
```

---

## 📞 Phone Number Format

WhatsApp via WuzAPI uses numbers **without the `+` prefix**:

| Format | Valid? |
|--------|--------|
| `5511999998888` | ✅ Brazil (DDD 11) |
| `14155552671` | ✅ USA (415) |
| `+5511999998888` | ❌ Remove the `+` |
| `011999998888` | ❌ Use country code |

---

## 📁 Project Structure

```
mcp-whatsapp/
├── pyproject.toml          # Python project config
├── .env.example            # Environment variables template
└── src/
    └── mcp_whatsapp/
        ├── server.py       # MCP server with all tools
        └── wuzapi_client.py # Async HTTP client for WuzAPI
```

---

## 🛡️ Security & Responsible Use

**Unofficial integration:** This project connects to WuzAPI, not to Meta's official WhatsApp Business Platform. It is not endorsed by Meta. Using an unofficial interface may lead to instability, restrictions, or account loss. Consider the official platform for business-critical integrations.

- **Consent and appropriate use:** Avoid unsolicited bulk messaging and automated outreach. Respect applicable platform rules and obtain consent where required.
- **Privacy mode:** The full MCP supports regex-based masking of common identifiers when `WUZAPI_PRIVACY_MODE=true`; its runtime fallback is `false` when the variable is unset. Masking is limited and does **not** guarantee anonymization or legal compliance. The Reader applies additional masking to its text responses.
- **Audit behavior:** The full MCP configures local logging at `logs/audit_privacy.log`; the Reader entry does not enable that logger by default. Logging and masking do not automatically satisfy LGPD/GDPR obligations.
- **Protect credentials:** Keep the WuzAPI admin and user tokens, encryption keys, and local `.env` private. Do not paste real tokens into AI prompts or commit them to Git.
- **Limit agent permissions:** Use the separate [read-only Reader](docs/reader-install.md) for history queries when sending or administrative features are unnecessary. Its tools are read-only, but the underlying WuzAPI user token itself is not inherently read-only.
- **Review sensitive operations:** Confirm recipients and content before letting an agent send messages, share files, administer groups, or change sessions. Data sent to an AI provider is governed by that provider's processing terms.

---

## 🤝 Contributing

Contributions are welcome! Feel free to open issues and pull requests.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes
4. Open a Pull Request

---

## 📄 License

This project is open-source and available under the [MIT License](LICENSE).

---

<div align="center">

**Built with ❤️ for the MCP ecosystem**

If this project helped you, please ⭐ star it!

</div>
