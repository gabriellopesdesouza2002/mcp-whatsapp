import asyncio
import json
import zipfile
from pathlib import Path
from datetime import datetime, timedelta

import pytest
import yaml
from mcp.server.fastmcp.exceptions import ToolError

from mcp_whatsapp import reader_server as reader
from mcp_whatsapp import server as core
from mcp_whatsapp.wuzapi_client import WuzAPIClient

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "whatsapp_status",
    "whatsapp_get_datetime",
    "whatsapp_get_chats",
    "whatsapp_get_messages",
    "whatsapp_search_messages",
    "whatsapp_search_contacts",
    "whatsapp_get_messages_by_contact_name",
    "whatsapp_list_groups",
    "whatsapp_get_group_info",
}


def test_reader_exposes_exact_read_only_allowlist():
    tools = reader.reader._tool_manager.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    forbidden = {"whatsapp_send_text", "whatsapp_delete_message", "whatsapp_react",
                 "whatsapp_admin", "whatsapp_create_group", "whatsapp_reset_session",
                 "whatsapp_disconnect", "whatsapp_force_sync", "whatsapp_get_unread_messages"}
    assert not ({tool.name for tool in tools} & forbidden)


def test_reader_tool_calls_delegate_only_to_read_functions(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("write operation must never be called")

    for name in ("whatsapp_send_text", "whatsapp_delete_message", "whatsapp_react",
                 "whatsapp_admin", "whatsapp_reset_session", "whatsapp_disconnect",
                 "whatsapp_force_sync"):
        monkeypatch.setattr(core, name, forbidden, raising=False)
    monkeypatch.setattr(core, "whatsapp_search_messages", forbidden)
    with pytest.raises(AssertionError, match="write operation"):
        asyncio.run(reader.whatsapp_search_messages("test"))
    assert not hasattr(reader, "whatsapp_send_text")

    forbidden_names = (
        "whatsapp_send_text", "whatsapp_forward_message", "whatsapp_delete_message",
        "whatsapp_react", "whatsapp_mark_read", "whatsapp_admin",
        "whatsapp_admin_delete_user", "whatsapp_create_group",
        "whatsapp_update_group_participants", "whatsapp_reset_session",
        "whatsapp_disconnect", "whatsapp_force_sync", "whatsapp_configure",
    )
    async def invoke(name):
        return await reader.reader._tool_manager.call_tool(name, {})

    for name in forbidden_names:
        with pytest.raises(ToolError, match="Unknown tool"):
            asyncio.run(invoke(name))


def test_query_wrappers_delegate_and_redact(monkeypatch):
    async def fake(*args, **kwargs):
        return '{"message":"Email: person@example.com phone 5511999998888"}'

    monkeypatch.setattr(core, "whatsapp_get_messages", fake)
    result = asyncio.run(reader.whatsapp_get_messages("5511999990000"))
    assert "person@example.com" not in result
    assert "5511999998888" not in result
    assert "REDACTED_EMAIL" in result


def test_empty_search_and_stale_warning_are_preserved(monkeypatch):
    async def empty(*args, **kwargs):
        return "No messages found containing 'missing'."

    async def stale(*args, **kwargs):
        return json.dumps({"ok": True, "n": 0, "msgs": [], "stale_warning": {"stale": True}})

    monkeypatch.setattr(core, "whatsapp_search_messages", empty)
    assert "No messages found" in asyncio.run(reader.whatsapp_search_messages("missing"))
    monkeypatch.setattr(core, "whatsapp_get_chats", stale)
    assert '"stale": true' in asyncio.run(reader.whatsapp_get_chats())


def test_contact_name_fallback_preserves_jid_and_can_query(monkeypatch):
    contacts = {"1234567890@s.whatsapp.net": {"PushName": "Test Contact"}}
    async def get_contacts():
        return {"data": contacts}

    monkeypatch.setattr(core.client, "get_contacts", get_contacts)
    monkeypatch.setattr(core, "WUZAPI_PRIVACY_MODE", False)
    match = json.loads(asyncio.run(core.whatsapp_search_contacts("Test Contact")))
    assert match["data"][0]["jid"] == "1234567890@s.whatsapp.net"


def test_contact_name_message_lookup_keeps_raw_jid_under_privacy_mode(monkeypatch):
    monkeypatch.setattr(core, "WUZAPI_PRIVACY_MODE", True)
    async def contacts():
        return {"data": {"1234567890@s.whatsapp.net": {"PushName": "Test Contact"}}}

    async def query(sql, params=(), limit=50):
        assert params[0] == "1234567890@s.whatsapp.net"
        return []

    monkeypatch.setattr(core.client, "get_contacts", contacts)
    monkeypatch.setattr(core, "_query_db", query)
    result = asyncio.run(core.whatsapp_get_messages_by_contact_name("Test Contact", False, 10))
    assert "Nenhuma mensagem" in result


def test_skill_frontmatter_and_package_validation(tmp_path, monkeypatch):
    skill = ROOT / "skills" / "whatsapp-reader" / "SKILL.md"
    if not skill.exists():
        pytest.skip("private Agensi skill source is not present in this public checkout")
    content = skill.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    frontmatter = content.split("---", 2)[1]
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "whatsapp-reader"
    assert isinstance(metadata["description"], str) and metadata["description"]

    from scripts import package_whatsapp_reader as packager
    monkeypatch.setattr(packager, "OUTPUT", tmp_path / "whatsapp-reader.zip")
    packager.main()
    with zipfile.ZipFile(packager.OUTPUT) as archive:
        assert "whatsapp-reader/SKILL.md" in archive.namelist()
        assert "whatsapp-reader/mcp/claude-code.json" in archive.namelist()
        assert all(".env" not in name and "server.py" not in name for name in archive.namelist())


def test_history_queries_return_real_fallback_and_redaction(monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", ROOT / "missing-test-db.db")
    async def mock_messages(phone, count):
        return {"success": True, "phone": phone, "messages": []}

    monkeypatch.setattr(core.client, "get_messages", mock_messages)
    result = asyncio.run(core.whatsapp_get_messages("1234567890", 4))
    assert '"messages": []' in result


def test_search_contacts_fallback_and_name_message_query(monkeypatch, tmp_path):
    db_path = tmp_path / "present-test-db.db"
    db_path.touch()
    monkeypatch.setattr(core, "DB_PATH", db_path)
    monkeypatch.setattr(core, "WUZAPI_PRIVACY_MODE", False)
    async def no_contacts():
        return {"data": {}}

    calls = []
    async def fake_query(sql, params=(), limit=50):
        calls.append((sql, params))
        if "SELECT chat_jid, datajson, text_content, timestamp" in sql:
            data = {"Info": {"IsFromMe": False, "PushName": "Sample Person"}}
            return [("1234567890@s.whatsapp.net", json.dumps(data), "hello", "2026-10-15")]
        return []

    monkeypatch.setattr(core.client, "get_contacts", no_contacts)
    monkeypatch.setattr(core, "_query_db", fake_query)
    result = json.loads(asyncio.run(core.whatsapp_search_contacts("Sample Person")))
    assert result["data"][0]["jid"] == "1234567890@s.whatsapp.net"
    assert result["data"][0]["phone"] == "1234567890"
    assert calls


def test_message_history_queries_with_simulated_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "simulated.db"
    db_path.touch()
    monkeypatch.setattr(core, "DB_PATH", db_path)
    old_ts = (datetime.now() - timedelta(hours=15)).strftime("%Y-%m-%dT%H:%M:%S")
    data = {"Info": {"Timestamp": old_ts, "IsFromMe": False, "Type": "text"},
            "Message": {"conversation": "Discussed the project estimate"}}

    async def query(sql, params=(), limit=50):
        if "SELECT datajson, sender_jid, message_id, media_link" in sql:
            return [(json.dumps(data), "1111111111@s.whatsapp.net", "mock-id", None)]
        if "SELECT chat_jid, timestamp, text_content" in sql:
            return [("1111111111@s.whatsapp.net", old_ts, "Discussed the project estimate")] if "estimate" in params[0] else []
        return []

    monkeypatch.setattr(core, "_query_db", query)
    messages = json.loads(awaitable_result(core.whatsapp_get_messages("1111111111", 10)))
    assert messages["msgs"][0]["text"] == "Discussed the project estimate"
    assert messages["stale_warning"]["stale"] is True

    searched = awaitable_result(core.whatsapp_search_messages("estimate", 10))
    assert "Discussed the project estimate" in searched
    empty = awaitable_result(core.whatsapp_search_messages("not-present", 10))
    assert "No messages found" in empty


def test_wuzapi_history_settings_use_integer_retention():
    client = WuzAPIClient("http://127.0.0.1:7143", "unit-test-token", "admin-test-token")
    updates = []

    async def users():
        return {"data": [{"id": "user-id", "token": "unit-test-token", "history": 0, "events": ""}]}

    async def update(user_id, **fields):
        updates.append((user_id, fields))
        return {"success": True}

    client.admin_list_users = users
    client.admin_update_user = update
    assert asyncio.run(client.ensure_user_exists()) is True
    assert updates == [("user-id", {"history": 1000, "events": "Message,ReadReceipt,HistorySync"})]


def awaitable_result(awaitable):
    return asyncio.run(awaitable)
