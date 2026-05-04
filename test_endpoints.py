"""
test_endpoints.py — Verifica quais endpoints do WuzAPI funcionam (GET vs POST).
Usa apenas stdlib (urllib), zero dependências extras.

Uso:
    python test_endpoints.py
"""

import json
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv
import os

# ─── Config ────────────────────────────────────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE, override=True)

BASE_URL    = os.getenv("WUZAPI_BASE_URL", "http://localhost:7143").rstrip("/")
TOKEN       = os.getenv("WUZAPI_TOKEN", "")
ADMIN_TOKEN = os.getenv("WUZAPI_ADMIN_TOKEN", "")

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _req(method: str, path: str, body: dict | None = None, admin: bool = False) -> tuple[int, dict]:
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if admin and ADMIN_TOKEN:
        headers["Authorization"] = ADMIN_TOKEN
    else:
        headers["token"] = TOKEN

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"body": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"body": raw}
    except Exception as ex:
        return 0, {"error": str(ex)}


def check(label: str, method: str, path: str, body: dict | None = None, admin: bool = False):
    status, resp = _req(method, path, body, admin)
    ok_flag = "✅" if 200 <= status < 300 else ("⚠️ " if status == 404 else "❌")
    # Extract a short summary from response
    summary = ""
    if "error" in resp:
        summary = resp["error"]
    elif "code" in resp or "message" in resp or "error" in resp.get("data", {}):
        summary = resp.get("message", resp.get("code", ""))
    elif status == 200:
        summary = "ok"
    else:
        body_text = str(resp)[:120]
        summary = body_text

    print(f"  {ok_flag} [{status}] {method:6} {path:<35} {summary}")


# ─── Testes ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🔍 WuzAPI endpoint checker")
    print(f"   URL   : {BASE_URL}")
    print(f"   Token : {TOKEN[:4]}{'*'*max(0,len(TOKEN)-4)}")
    print()

    print("── Session ──────────────────────────────────────────────────────")
    check("health",          "GET",  "/health")
    check("status",          "GET",  "/session/status")
    check("qr",              "GET",  "/session/qr")

    print("\n── Chat (read-only) ─────────────────────────────────────────────")
    check("unread  GET",     "GET",  "/chat/unread")
    check("unread  POST",    "POST", "/chat/unread", {})
    check("history GET",     "GET",  "/chat/history", None)

    print("\n── User / Contacts ──────────────────────────────────────────────")
    check("contacts GET",    "GET",  "/user/contacts")
    check("contacts POST",   "POST", "/user/contacts", {})
    check("user/info POST",  "POST", "/user/info", {"Phone": "5511999999999"})

    print("\n── Groups ───────────────────────────────────────────────────────")
    check("group/list GET",  "GET",  "/group/list")
    check("group/list POST", "POST", "/group/list", {})

    print("\n── Admin (needs ADMIN_TOKEN) ────────────────────────────────────")
    if ADMIN_TOKEN:
        check("admin/users GET", "GET", "/admin/users", admin=True)
    else:
        print("  ⚠️  WUZAPI_ADMIN_TOKEN não configurado — pulando")

    print()


if __name__ == "__main__":
    main()
