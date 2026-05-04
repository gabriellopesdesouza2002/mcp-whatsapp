"""
test_groq.py — Testa o MCP WhatsApp usando Groq (gratuito) como LLM.

Requisitos extras (além do pyproject.toml):
    pip install mcp-use langchain-groq

API Key gratuita: https://console.groq.com/keys

Uso:
    python test_groq.py                  # menu interativo
    python test_groq.py --test health    # roda só o teste de saúde
    python test_groq.py --test status    # status da conexão
    python test_groq.py --test send      # envia mensagem de teste
    python test_groq.py --test chats     # lista chats
    python test_groq.py --test contacts  # lista contatos
    python test_groq.py --test groups    # lista grupos
    python test_groq.py --test admin     # lista usuários WuzAPI (admin)
    python test_groq.py --test all       # roda todos os testes sem envio
"""

import asyncio
import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

# ─── Carrega .env do projeto ───────────────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE, override=True)

WUZAPI_BASE_URL   = os.getenv("WUZAPI_BASE_URL", "http://localhost:7143")
WUZAPI_TOKEN      = os.getenv("WUZAPI_TOKEN", "")
WUZAPI_ADMIN_TOKEN = os.getenv("WUZAPI_ADMIN_TOKEN", "")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")

# Caminho do Python dentro do venv do projeto
VENV_PYTHON = str(Path(__file__).parent / ".venv" / "Scripts" / "python.exe")
if not Path(VENV_PYTHON).exists():
    # Linux/macOS
    VENV_PYTHON = str(Path(__file__).parent / ".venv" / "bin" / "python")

# ─── Validações iniciais ────────────────────────────────────────────────────
def _check_requirements():
    errors = []
    if not WUZAPI_TOKEN:
        errors.append("❌ WUZAPI_TOKEN não configurado no .env")
    if not GROQ_API_KEY:
        errors.append(
            "❌ GROQ_API_KEY não configurado.\n"
            "   Adicione no .env: GROQ_API_KEY=sua_chave\n"
            "   Obtenha grátis em: https://console.groq.com/keys"
        )
    if not Path(VENV_PYTHON).exists():
        errors.append(f"❌ Python do venv não encontrado em: {VENV_PYTHON}\n   Execute: python -m venv .venv && .venv\\Scripts\\activate && pip install -e .")
    if errors:
        print("\n".join(errors))
        sys.exit(1)

# ─── Configuração do agente ─────────────────────────────────────────────────
def _build_agent(model: str = "llama-3.3-70b-versatile"):
    """Monta o MCPAgent com Groq e o servidor MCP local."""
    try:
        from mcp_use import MCPAgent, MCPClient
        from langchain_groq import ChatGroq
    except ImportError:
        print(
            "❌ Dependências não instaladas. Execute:\n"
            "   pip install mcp-use langchain-groq"
        )
        sys.exit(1)

    client = MCPClient({
        "mcpServers": {
            "whatsapp": {
                "command": VENV_PYTHON,
                "args": ["-m", "mcp_whatsapp.server"],
                "env": {
                    "WUZAPI_BASE_URL":   WUZAPI_BASE_URL,
                    "WUZAPI_TOKEN":      WUZAPI_TOKEN,
                    "WUZAPI_ADMIN_TOKEN": WUZAPI_ADMIN_TOKEN,
                    "WUZAPI_HISTORY_SYNC": os.getenv("WUZAPI_HISTORY_SYNC", "true"),
                },
            }
        }
    })

    llm = ChatGroq(
        model=model,
        api_key=GROQ_API_KEY,
        temperature=0,          # respostas determinísticas para testes
    )

    return MCPAgent(llm=llm, client=client, max_steps=5, verbose=True)


# ══════════════════════════════════════════════════════════════════════════════
# TESTES INDIVIDUAIS
# ══════════════════════════════════════════════════════════════════════════════

async def test_health(agent):
    """Verifica se a API WuzAPI está de pé."""
    print("\n🔍 [HEALTH] Verificando saúde do WuzAPI...")
    result = await agent.run("Check WuzAPI health. Use the whatsapp_health tool.")
    print(f"✅ Resultado:\n{result}\n")


async def test_status(agent):
    """Verifica o status da sessão WhatsApp."""
    print("\n🔍 [STATUS] Verificando status da sessão...")
    result = await agent.run("Check WhatsApp connection status. Use the whatsapp_status tool and report if it is connected, disconnected, or waiting for QR scan.")
    print(f"✅ Resultado:\n{result}\n")


async def test_chats(agent):
    """Lista os chats ativos."""
    print("\n🔍 [CHATS] Listando chats ativos...")
    result = await agent.run("List all active WhatsApp chats using whatsapp_get_chats. Show a summary with the number of chats found.")
    print(f"✅ Resultado:\n{result}\n")


async def test_contacts(agent):
    """Lista contatos."""
    print("\n🔍 [CONTACTS] Buscando contatos...")
    result = await agent.run("Get the WhatsApp contacts list using whatsapp_get_contacts. Show how many contacts were found.")
    print(f"✅ Resultado:\n{result}\n")


async def test_groups(agent):
    """Lista grupos."""
    print("\n🔍 [GROUPS] Listando grupos...")
    result = await agent.run("List all WhatsApp groups using whatsapp_list_groups. Show how many groups were found and their names.")
    print(f"✅ Resultado:\n{result}\n")


async def test_unread(agent):
    """Mensagens não lidas."""
    print("\n🔍 [UNREAD] Buscando mensagens não lidas...")
    result = await agent.run("Get all unread WhatsApp messages using whatsapp_get_unread_messages. Show a summary.")
    print(f"✅ Resultado:\n{result}\n")


async def test_admin(agent):
    """Lista usuários WuzAPI (requer admin token)."""
    print("\n🔍 [ADMIN] Listando usuários WuzAPI...")
    if not WUZAPI_ADMIN_TOKEN:
        print("⚠️  WUZAPI_ADMIN_TOKEN não configurado, pulando teste admin.")
        return
    result = await agent.run("List all WuzAPI users using whatsapp_admin_list_users. Show how many users exist.")
    print(f"✅ Resultado:\n{result}\n")


async def test_send(agent, phone: str, message: str):
    """Envia mensagem de texto real."""
    print(f"\n📤 [SEND] Enviando mensagem para {phone}...")
    result = await agent.run(
        f"Send a WhatsApp text message to phone number {phone} with this message: '{message}'. "
        f"Use the whatsapp_send_text tool."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_check_phone(agent, phone: str):
    """Verifica se número está no WhatsApp."""
    print(f"\n🔍 [CHECK] Verificando se {phone} está no WhatsApp...")
    result = await agent.run(
        f"Check if the phone number {phone} is registered on WhatsApp using whatsapp_check_phones."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_user_info(agent):
    """Info do usuário logado."""
    print("\n🔍 [USER_INFO] Buscando info do usuário logado...")
    result = await agent.run("Get info about the currently logged-in WhatsApp user using whatsapp_get_user_info.")
    print(f"✅ Resultado:\n{result}\n")


# ══════════════════════════════════════════════════════════════════════════════
# SUÍTE COMPLETA (sem envio de mensagem)
# ══════════════════════════════════════════════════════════════════════════════

async def run_all(agent):
    """Roda todos os testes que NÃO enviam mensagens."""
    print("\n" + "="*60)
    print("  🚀 SUÍTE COMPLETA DE TESTES (sem envio de mensagem)")
    print("="*60)
    tests = [
        ("Health",     test_health),
        ("Status",     test_status),
        ("User Info",  test_user_info),
        ("Chats",      test_chats),
        ("Unread",     test_unread),
        ("Contacts",   test_contacts),
        ("Groups",     test_groups),
        ("Admin",      test_admin),
    ]
    passed, failed = 0, 0
    for name, fn in tests:
        try:
            if name == "Admin":
                await fn(agent)
            else:
                await fn(agent)
            passed += 1
        except Exception as e:
            print(f"❌ [{name}] FALHOU: {e}\n")
            failed += 1

    print("\n" + "="*60)
    print(f"  ✅ Passou: {passed}  |  ❌ Falhou: {failed}")
    print("="*60 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# MENU INTERATIVO
# ══════════════════════════════════════════════════════════════════════════════

def _print_menu():
    print("""
╔══════════════════════════════════════════════╗
║     MCP WhatsApp — Teste com Groq (grátis)   ║
╠══════════════════════════════════════════════╣
║  1. Health check                             ║
║  2. Status da sessão                         ║
║  3. Info do usuário logado                   ║
║  4. Listar chats                             ║
║  5. Mensagens não lidas                      ║
║  6. Listar contatos                          ║
║  7. Listar grupos                            ║
║  8. Admin — listar usuários WuzAPI           ║
║  9. Verificar número no WhatsApp             ║
║  10. Enviar mensagem (REAL — cuidado!)       ║
║  0. Rodar TODOS os testes (sem envio)        ║
║  q. Sair                                     ║
╚══════════════════════════════════════════════╝
""")


async def interactive_menu(agent):
    """Menu interativo para rodar testes sob demanda."""
    while True:
        _print_menu()
        choice = input("Escolha uma opção: ").strip().lower()

        if choice == "q":
            print("👋 Saindo...")
            break
        elif choice == "0":
            await run_all(agent)
        elif choice == "1":
            await test_health(agent)
        elif choice == "2":
            await test_status(agent)
        elif choice == "3":
            await test_user_info(agent)
        elif choice == "4":
            await test_chats(agent)
        elif choice == "5":
            await test_unread(agent)
        elif choice == "6":
            await test_contacts(agent)
        elif choice == "7":
            await test_groups(agent)
        elif choice == "8":
            await test_admin(agent)
        elif choice == "9":
            phone = input("  📞 Digite o número (ex: 5511999998888): ").strip()
            if phone:
                await test_check_phone(agent, phone)
        elif choice == "10":
            phone   = input("  📞 Número destino (ex: 5511999998888): ").strip()
            message = input("  💬 Mensagem: ").strip()
            confirm = input(f"  ⚠️  Confirma envio REAL para {phone}? (s/N): ").strip().lower()
            if confirm == "s" and phone and message:
                await test_send(agent, phone, message)
            else:
                print("  ↩️  Cancelado.")
        else:
            print("  ⚠️  Opção inválida.")

        input("\n  [Enter para continuar]")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="Testa MCP WhatsApp com Groq")
    parser.add_argument("--test",  choices=["health","status","send","chats","contacts","groups","admin","all"], help="Teste específico para rodar")
    parser.add_argument("--phone", default="", help="Número de destino para --test send")
    parser.add_argument("--message", default="Olá! Mensagem de teste via MCP WhatsApp + Groq 🤖", help="Mensagem para --test send")
    parser.add_argument("--model", default="llama-3.3-70b-versatile", help="Modelo Groq a usar")
    args = parser.parse_args()

    _check_requirements()

    print(f"\n⚙️  Configuração:")
    print(f"   WuzAPI URL : {WUZAPI_BASE_URL}")
    print(f"   Token      : {WUZAPI_TOKEN[:4]}{'*' * max(0, len(WUZAPI_TOKEN)-4)}")
    print(f"   Admin Token: {'✅ configurado' if WUZAPI_ADMIN_TOKEN else '⚠️  não configurado'}")
    print(f"   Groq Model : {args.model}")
    print(f"   Groq Key   : {GROQ_API_KEY[:8]}...\n")

    print("🔧 Iniciando agente Groq + MCP (pode demorar alguns segundos)...")
    agent = _build_agent(model=args.model)

    if args.test == "health":
        await test_health(agent)
    elif args.test == "status":
        await test_status(agent)
    elif args.test == "chats":
        await test_chats(agent)
    elif args.test == "contacts":
        await test_contacts(agent)
    elif args.test == "groups":
        await test_groups(agent)
    elif args.test == "admin":
        await test_admin(agent)
    elif args.test == "send":
        if not args.phone:
            print("❌ Passe --phone para o teste de envio. Ex: --phone 5511999998888")
            sys.exit(1)
        await test_send(agent, args.phone, args.message)
    elif args.test == "all":
        await run_all(agent)
    else:
        # Menu interativo
        await interactive_menu(agent)


if __name__ == "__main__":
    asyncio.run(main())
