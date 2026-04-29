<div align="center">

# 📱 MCP WhatsApp

**Send messages, images, documents and more on WhatsApp — directly from any AI.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-Compatible-blueviolet)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/gabriellopesdesouza2002/mcp-whatsapp?style=social)](https://github.com/gabriellopesdesouza2002/mcp-whatsapp)

Works with **Claude**, **Gemini**, **Cursor**, **Windsurf**, **Continue.dev** and any MCP-compatible AI.

**100% Open Source and MIT Licensed.**

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

**No code. No manual API calls. Just ask.**

---

## 🚀 Quick Start (3 steps)

### Step 1 — Clone and start WuzAPI

```bash
git clone https://github.com/YOUR_USER/mcp-whatsapp
cd mcp-whatsapp
docker compose up -d
```

That's it — WuzAPI starts automatically on `http://localhost:7143` with message history enabled. ✅

> The included `docker-compose.yml` sets everything up for you.  
> Default admin token: `admin123` (change via `WUZAPI_ADMIN_TOKEN` env var).

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

> *"Configure my WhatsApp with token **mytoken123**"*

**Magic:** The server will automatically create the user in WuzAPI for you (Plug & Play). No manual dashboard work required! 🪄

---

## 🔑 Manual Configuration (Optional)

If you prefer to use the dashboard, it is available at `http://localhost:7143`.

1. Open `http://localhost:7143` → click **Admin Mode**
2. Enter the admin token — default is `admin123`
3. Go to **Users → Create User**, enter any name and choose a token.

> *"Configure my WhatsApp with token **myusertoken**"*

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
> *"Configure my WhatsApp: token=abc123, url=http://localhost:7143"*

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
You: "Configure my WhatsApp with token abc123"
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

**IMPORTANT: This project uses an unofficial WhatsApp API. Use it at your own risk.**

To ensure your account stays safe and you remain compliant with global data laws (LGPD/GDPR), please follow these guidelines:

1.  **Avoid Spam:** Do not use this tool for bulk messaging or automated marketing. Excessive automated activity is the #1 cause of WhatsApp account bans.
2.  **Privacy Guardrails:** By default, this server includes masking for sensitive information (PII) like CPFs, Credit Cards, and Emails (when `WUZAPI_PRIVACY_MODE=true`).
3.  **Audit Logs:** All tool calls are logged in `logs/audit_privacy.log`. This is essential for transparency and accountability.
4.  **Token Safety:** Never share your `WUZAPI_TOKEN` or `WUZAPI_ADMIN_TOKEN`. These grant full access to your messages.
5.  **AI Autonomy:** Be careful when giving the AI "autonomy" to send messages. Always review the output if the AI is performing high-stakes tasks.
6.  **Terms of Service:** Be aware that using unofficial APIs violates WhatsApp's Terms of Service. This tool is intended for personal productivity and research.

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
