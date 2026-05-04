"""HTTP client for WuzAPI WhatsApp API."""

import httpx
from typing import Any


class WuzAPIClient:
    """Async HTTP client to interact with WuzAPI endpoints."""

    def __init__(self, base_url: str, token: str, admin_token: str | None = None, history_sync: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.admin_token = admin_token
        self.history_sync = history_sync

    def _headers(self, admin: bool = False, has_body: bool = False) -> dict[str, str]:
        """Build request headers with appropriate authentication."""
        headers = {}
        if has_body:
            headers["Content-Type"] = "application/json"
        if admin and self.admin_token:
            headers["Authorization"] = self.admin_token
        else:
            headers["token"] = self.token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        admin: bool = False,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Execute an HTTP request against the WuzAPI."""
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=self._headers(admin=admin, has_body=json_data is not None),
                json=json_data,
                params=params,
            )
            # Try to parse JSON, fall back to text
            try:
                return response.json()
            except Exception:
                return {"status": response.status_code, "body": response.text}

    # ──────────────────────────────────────────────
    # Session / Connection
    # ──────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        """Connect/Login to WhatsApp (starts session)."""
        return await self._request("POST", "/session/connect", json_data={}, timeout=60.0)

    async def disconnect(self) -> dict[str, Any]:
        """Disconnect/Logout from WhatsApp."""
        return await self._request("POST", "/session/disconnect", json_data={})

    async def get_status(self) -> dict[str, Any]:
        """Get current connection status."""
        return await self._request("GET", "/session/status")

    async def get_qrcode(self) -> dict[str, Any]:
        """Get QR code for WhatsApp pairing (JSON response)."""
        return await self._request("GET", "/session/qr")

    async def get_qrcode_image(self) -> bytes | None:
        """Get QR code as PNG bytes (decoded from JSON base64 response)."""
        import base64 as _base64
        result = await self.get_qrcode()
        try:
            qr_data: str = result["data"]["QRCode"]  # "data:image/png;base64,..."
            b64 = qr_data.split(",", 1)[1]
            return _base64.b64decode(b64)
        except Exception:
            return None

    async def health(self) -> dict[str, Any]:
        """Check API health status."""
        return await self._request("GET", "/health")

    # ──────────────────────────────────────────────
    # Chat / Messaging
    # ──────────────────────────────────────────────

    async def send_text(self, phone: str, text: str) -> dict[str, Any]:
        """Send a text message."""
        return await self._request("POST", "/chat/send/text", {
            "Phone": phone,
            "Body": text,
        })

    async def send_image(
        self, phone: str, image_url: str, caption: str = ""
    ) -> dict[str, Any]:
        """Send an image message."""
        payload: dict[str, Any] = {"Phone": phone, "Image": image_url}
        if caption:
            payload["Caption"] = caption
        return await self._request("POST", "/chat/send/image", payload)

    async def send_document(
        self, phone: str, document_url: str, filename: str = "", caption: str = ""
    ) -> dict[str, Any]:
        """Send a document message."""
        payload: dict[str, Any] = {"Phone": phone, "Document": document_url}
        if filename:
            payload["FileName"] = filename
        if caption:
            payload["Caption"] = caption
        return await self._request("POST", "/chat/send/document", payload)

    async def send_audio(self, phone: str, audio_url: str) -> dict[str, Any]:
        """Send an audio message."""
        return await self._request("POST", "/chat/send/audio", {
            "Phone": phone,
            "Audio": audio_url,
        })

    async def send_video(
        self, phone: str, video_url: str, caption: str = ""
    ) -> dict[str, Any]:
        """Send a video message."""
        payload: dict[str, Any] = {"Phone": phone, "Video": video_url}
        if caption:
            payload["Caption"] = caption
        return await self._request("POST", "/chat/send/video", payload)

    async def send_sticker(self, phone: str, sticker_url: str) -> dict[str, Any]:
        """Send a sticker message."""
        return await self._request("POST", "/chat/send/sticker", {
            "Phone": phone,
            "Sticker": sticker_url,
        })

    async def send_location(
        self, phone: str, latitude: float, longitude: float, name: str = ""
    ) -> dict[str, Any]:
        """Send a location message."""
        payload: dict[str, Any] = {
            "Phone": phone,
            "Latitude": latitude,
            "Longitude": longitude,
        }
        if name:
            payload["Name"] = name
        return await self._request("POST", "/chat/send/location", payload)

    async def send_contact(
        self, phone: str, contact_name: str, contact_phone: str
    ) -> dict[str, Any]:
        """Send a contact/vcard message."""
        return await self._request("POST", "/chat/send/contact", {
            "Phone": phone,
            "ContactName": contact_name,
            "ContactPhone": contact_phone,
        })

    async def send_link(
        self, phone: str, url: str, text: str = ""
    ) -> dict[str, Any]:
        """Send a link preview message."""
        payload: dict[str, Any] = {"Phone": phone, "Url": url}
        if text:
            payload["Text"] = text
        return await self._request("POST", "/chat/send/link", payload)

    async def send_buttons(
        self,
        phone: str,
        text: str,
        buttons: list[dict[str, str]],
        title: str = "",
        footer: str = "",
    ) -> dict[str, Any]:
        """Send an interactive buttons message."""
        payload: dict[str, Any] = {
            "Phone": phone,
            "Body": text,
            "Buttons": buttons,
        }
        if title:
            payload["Title"] = title
        if footer:
            payload["Footer"] = footer
        return await self._request("POST", "/chat/send/buttons", payload)

    async def send_list(
        self,
        phone: str,
        text: str,
        button_text: str,
        sections: list[dict[str, Any]],
        title: str = "",
        footer: str = "",
    ) -> dict[str, Any]:
        """Send an interactive list message."""
        payload: dict[str, Any] = {
            "Phone": phone,
            "Body": text,
            "ButtonText": button_text,
            "Sections": sections,
        }
        if title:
            payload["Title"] = title
        if footer:
            payload["Footer"] = footer
        return await self._request("POST", "/chat/send/list", payload)

    async def react(self, phone: str, message_id: str, emoji: str) -> dict[str, Any]:
        """React to a message with an emoji."""
        return await self._request("POST", "/chat/react", {
            "Phone": phone,
            "MessageId": message_id,
            "Emoji": emoji,
        })

    async def mark_read(self, phone: str, message_ids: list[str]) -> dict[str, Any]:
        """Mark messages as read."""
        return await self._request("POST", "/chat/markread", {
            "Phone": phone,
            "Id": message_ids,
        })

    async def download_media(
        self, phone: str, message_id: str
    ) -> dict[str, Any]:
        """Download media from a message."""
        return await self._request("POST", "/chat/downloadmedia", {
            "Phone": phone,
            "MessageId": message_id,
        })

    async def get_messages(
        self, phone: str, count: int = 50
    ) -> dict[str, Any]:
        """Get messages from a chat."""
        # Ensure full JID format (e.g. 5511999998888@s.whatsapp.net)
        jid = phone if "@" in phone else f"{phone}@s.whatsapp.net"
        return await self._request("GET", "/chat/history", params={
            "chat_jid": jid,
            "limit": count,
        })

    async def get_chats(self) -> dict[str, Any]:
        """List all active chats/conversations."""
        return await self._request("GET", "/chat/list")

    async def reply_message(
        self, phone: str, message: str, quoted_message_id: str
    ) -> dict[str, Any]:
        """Reply to a specific message (quote reply)."""
        return await self._request("POST", "/chat/send/text", {
            "Phone": phone,
            "Body": message,
            "QuotedMessageId": quoted_message_id,
        })

    async def delete_message(
        self, phone: str, message_id: str, everyone: bool = True
    ) -> dict[str, Any]:
        """Delete a sent message."""
        return await self._request("POST", "/chat/delete", {
            "Phone": phone,
            "MessageId": message_id,
            "Everyone": everyone,
        })

    async def send_poll(
        self,
        phone: str,
        question: str,
        options: list[str],
        max_answers: int = 1,
    ) -> dict[str, Any]:
        """Send a poll message."""
        return await self._request("POST", "/chat/send/poll", {
            "Phone": phone,
            "Question": question,
            "Options": options,
            "MaxAnswers": max_answers,
        })

    async def get_unread_messages(self) -> dict[str, Any]:
        """Get all unread messages across all chats."""
        return await self._request("GET", "/chat/unread")

    # ──────────────────────────────────────────────
    # User / Profile
    # ──────────────────────────────────────────────

    async def get_user_info(self, phone: str) -> dict[str, Any]:
        """Get WhatsApp info (name, about, business) for a specific phone number."""
        return await self._request("POST", "/user/info", json_data={"Phone": phone})

    async def get_contacts(self) -> dict[str, Any]:
        """Get contacts list."""
        return await self._request("GET", "/user/contacts")

    async def check_phones(self, phones: list[str]) -> dict[str, Any]:
        """Check if phone numbers are registered on WhatsApp."""
        return await self._request("POST", "/user/check", {
            "Phones": phones,
        })

    async def get_avatar(self, phone: str) -> dict[str, Any]:
        """Get profile picture for a phone number."""
        return await self._request("POST", "/user/avatar", {
            "Phone": phone,
        })

    # ──────────────────────────────────────────────
    # Group
    # ──────────────────────────────────────────────

    async def list_groups(self) -> dict[str, Any]:
        """List all groups."""
        return await self._request("GET", "/group/list")

    async def get_group_info(self, group_jid: str) -> dict[str, Any]:
        """Get group info."""
        return await self._request("GET", "/group/info", params={"GroupJID": group_jid})

    async def create_group(
        self, name: str, participants: list[str]
    ) -> dict[str, Any]:
        """Create a new group."""
        return await self._request("POST", "/group/create", {
            "Name": name,
            "Participants": participants,
        })

    async def update_group_participants(
        self, group_jid: str, participants: list[str], action: str = "add"
    ) -> dict[str, Any]:
        """Add or remove participants from a group. Action: 'add' or 'remove'."""
        return await self._request("POST", "/group/updateparticipants", {
            "GroupJID": group_jid,
            "Participants": participants,
            "Action": action,
        })

    async def get_group_invite_link(self, group_jid: str) -> dict[str, Any]:
        """Get invite link for a group."""
        return await self._request("GET", "/group/invitelink", params={
            "GroupJID": group_jid,
        })

    # ──────────────────────────────────────────────
    # Webhook
    # ──────────────────────────────────────────────

    async def set_webhook(self, webhook_url: str) -> dict[str, Any]:
        """Set the webhook URL for receiving incoming messages."""
        return await self._request("POST", "/webhook", {
            "WebhookURL": webhook_url,
        })

    # ──────────────────────────────────────────────
    # Newsletter / Channel
    # ──────────────────────────────────────────────

    async def get_newsletter_messages(
        self, newsletter_jid: str, count: int = 50
    ) -> dict[str, Any]:
        """Get messages from a newsletter/channel."""
        return await self._request("POST", "/newsletter/messages", {
            "NewsletterJID": newsletter_jid,
            "Count": count,
        })

    async def subscribe_newsletter(self, newsletter_jid: str) -> dict[str, Any]:
        """Subscribe to a newsletter/channel."""
        return await self._request("POST", "/newsletter/subscribe", {
            "NewsletterJID": newsletter_jid,
        })

    # ──────────────────────────────────────────────
    # Admin
    # ──────────────────────────────────────────────

    async def admin_update_user(self, user_id: str, **fields: Any) -> dict[str, Any]:
        """Update a WuzAPI user's settings (admin)."""
        return await self._request("PUT", f"/admin/users/{user_id}", json_data=fields, admin=True)

    async def admin_list_users(self) -> dict[str, Any]:
        """List all WuzAPI users (admin)."""
        return await self._request("GET", "/admin/users", admin=True)

    async def admin_create_user(self, name: str, token: str, **kwargs: Any) -> dict[str, Any]:
        """Create a new WuzAPI user (admin)."""
        payload = {
            "name": name,
            "token": token,
        }
        payload.update(kwargs)
        return await self._request("POST", "/admin/users", payload, admin=True)

    async def admin_delete_user(self, name: str) -> dict[str, Any]:
        """Delete a WuzAPI user (admin)."""
        return await self._request("DELETE", f"/admin/users/{name}", admin=True)

    async def ensure_user_exists(self) -> bool:
        """Check if user with current token exists, create if not (requires admin_token)."""
        if not self.admin_token:
            return False
        try:
            users = await self.admin_list_users()
            # If the response is an error or not what we expect, we can't check
            if not isinstance(users, dict) or "data" not in users:
                return False
                
            user_list = users.get("data") or []
            for user in user_list:
                if user.get("token") == self.token:
                    return True
            
            # Not found, create it with a generic name
            result = await self.admin_create_user(name="mcp_whatsapp_user", token=self.token)
            if not result.get("success"):
                return False
                
            # If created successfully and history_sync is requested, update it
            if self.history_sync:
                user_id = result.get("data", {}).get("id")
                if user_id:
                    await self.admin_update_user(
                        user_id, 
                        history=True, 
                        events="Message,ReadReceipt,HistorySync"
                    )
            
            return True
        except Exception:
            return False
