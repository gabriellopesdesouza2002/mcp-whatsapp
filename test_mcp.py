"""
test_mcp.py — Testa o MCP WhatsApp com Groq (gratuito) ou OpenAI GPT.

Requisitos extras:
    pip install mcp-use langchain-groq langchain-openai

Uso:
    python test_mcp.py                                    # menu interativo (Groq)
    python test_mcp.py --provider openai                  # menu interativo (OpenAI)
    python test_mcp.py --provider openai --model gpt-4o   # escolhe o modelo
    python test_mcp.py --test all                         # roda todos os testes
    python test_mcp.py --test health
    python test_mcp.py --test status
    python test_mcp.py --test send --phone 5511999998888
    python test_mcp.py --test chats
    python test_mcp.py --test contacts
    python test_mcp.py --test groups
    python test_mcp.py --test admin
    python test_mcp.py --test unread
    python test_mcp.py --chat                             # vai direto pro chat livre
"""

import asyncio
import logging
import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

# ─── Silencia loggers barulhentos por padrão ────────────────────────────────────
# Mantém apenas avisos críticos — remova/ajuste se quiser ver tool calls raw
for _noisy in ("mcp_use", "mcp_use.agents.display", "langchain", "langsmith", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING, format="%(message)s")

# ─── Config ────────────────────────────────────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE, override=True)

WUZAPI_BASE_URL    = os.getenv("WUZAPI_BASE_URL", "http://localhost:7143")
WUZAPI_TOKEN       = os.getenv("WUZAPI_TOKEN", "")
WUZAPI_ADMIN_TOKEN = os.getenv("WUZAPI_ADMIN_TOKEN", "")

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL    = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

VENV_PYTHON = str(Path(__file__).parent / ".venv" / "Scripts" / "python.exe")
if not Path(VENV_PYTHON).exists():
    VENV_PYTHON = str(Path(__file__).parent / ".venv" / "bin" / "python")

# ─── Validações ────────────────────────────────────────────────────────────────
def _check_requirements(provider: str):
    errors = []
    if not WUZAPI_TOKEN:
        errors.append("❌ WUZAPI_TOKEN não configurado no .env")
    if provider == "groq" and not GROQ_API_KEY:
        errors.append(
            "❌ GROQ_API_KEY não configurado.\n"
            "   Adicione no .env: GROQ_API_KEY=sua_chave\n"
            "   Obtenha grátis em: https://console.groq.com/keys"
        )
    if provider == "openai" and not OPENAI_API_KEY:
        errors.append(
            "❌ OPENAI_API_KEY não configurado.\n"
            "   Adicione no .env: OPENAI_API_KEY=sua_chave\n"
            "   Obtenha em: https://platform.openai.com/api-keys"
        )
    if not Path(VENV_PYTHON).exists():
        errors.append(
            f"❌ Python do venv não encontrado em: {VENV_PYTHON}\n"
            "   Execute: python -m venv .venv && .venv\\Scripts\\activate && pip install -e ."
        )
    if errors:
        print("\n".join(errors))
        sys.exit(1)


# ─── Agent ─────────────────────────────────────────────────────────────────────
def _build_agent(provider: str, model: str, verbose: bool = False):
    """Monta o MCPAgent com o provider escolhido e o servidor MCP local."""
    try:
        from mcp_use import MCPAgent, MCPClient
    except ImportError:
        print("❌ Dependência ausente. Execute:\n   pip install mcp-use")
        sys.exit(1)

    # Build a clean env for the MCP subprocess:
    # Start with the full current environment so the child process has PATH,
    # SYSTEMROOT, TEMP, etc.  Then overlay only the WuzAPI-specific keys.
    _subprocess_env = {**os.environ}
    _subprocess_env.update({
        "WUZAPI_BASE_URL":    WUZAPI_BASE_URL,
        "WUZAPI_TOKEN":       WUZAPI_TOKEN,
        "WUZAPI_ADMIN_TOKEN": WUZAPI_ADMIN_TOKEN,
        "WUZAPI_HISTORY_SYNC": os.getenv("WUZAPI_HISTORY_SYNC", "true"),
    })

    client = MCPClient({
        "mcpServers": {
            "whatsapp": {
                "command": VENV_PYTHON,
                "args": ["-m", "mcp_whatsapp.server"],
                "env": _subprocess_env,
            }
        }
    })

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            print("❌ Dependência ausente. Execute:\n   pip install langchain-openai")
            sys.exit(1)
        llm = ChatOpenAI(model=model, api_key=OPENAI_API_KEY, temperature=0)
        provider_label = f"OpenAI ({model})"
    else:
        try:
            from langchain_groq import ChatGroq
        except ImportError:
            print("❌ Dependência ausente. Execute:\n   pip install langchain-groq")
            sys.exit(1)
        llm = ChatGroq(model=model, api_key=GROQ_API_KEY, temperature=0)
        provider_label = f"Groq ({model})"

    print(f"🤖 Provider : {provider_label}")
    return MCPAgent(llm=llm, client=client, max_steps=10, verbose=verbose)


# ══════════════════════════════════════════════════════════════════════════════
# TESTES
# ══════════════════════════════════════════════════════════════════════════════

_TIMEOUT = 90.0  # segundos por chamada ao agente


async def _run(agent, prompt: str) -> str:
    """Wrapper com timeout para todas as chamadas ao agente."""
    return await asyncio.wait_for(agent.run(prompt), timeout=_TIMEOUT)


async def test_health(agent):
    print("\n🔍 [HEALTH] Verificando saúde do WuzAPI...")
    result = await _run(agent, "Check WuzAPI health. Use the whatsapp_health tool.")
    print(f"✅ Resultado:\n{result}\n")


async def test_status(agent):
    print("\n🔍 [STATUS] Verificando status da sessão...")
    result = await _run(agent,
        "Check WhatsApp connection status. Use the whatsapp_status tool and report "
        "if it is connected, disconnected, or waiting for QR scan."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_chats(agent):
    print("\n🔍 [CHATS] Listando chats ativos...")
    result = await _run(agent,
        "List all active WhatsApp chats using whatsapp_get_chats. "
        "Show a summary with the number of chats found."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_unread(agent):
    print("\n🔍 [UNREAD] Mensagens recebidas recentes...")
    result = await _run(agent,
        "Get recent incoming WhatsApp messages using whatsapp_get_unread_messages. "
        "Show a summary of who sent what."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_contacts(agent):
    print("\n🔍 [CONTACTS] Buscando contatos...")
    result = await _run(agent,
        "Get the WhatsApp contacts list using whatsapp_get_contacts. "
        "Show how many contacts were found."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_groups(agent):
    print("\n🔍 [GROUPS] Listando grupos...")
    result = await _run(agent,
        "List all WhatsApp groups using whatsapp_list_groups. "
        "Show how many groups were found and their names."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_admin(agent):
    print("\n🔍 [ADMIN] Listando usuários WuzAPI...")
    if not WUZAPI_ADMIN_TOKEN:
        print("⚠️  WUZAPI_ADMIN_TOKEN não configurado, pulando.")
        return
    result = await _run(agent,
        "List all WuzAPI users using whatsapp_admin_list_users. "
        "Show how many users exist."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_user_info(agent):
    print("\n🔍 [USER_INFO] Info da sessão logada via status...")
    result = await _run(agent,
        "Use whatsapp_status to get the current session info. "
        "Report the phone number (JID), name, and whether history sync is enabled."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_send(agent, phone: str, message: str):
    print(f"\n📤 [SEND] Enviando mensagem para {phone}...")
    result = await _run(agent,
        f"Send a WhatsApp text message to phone number {phone} "
        f"with this message: '{message}'. Use the whatsapp_send_text tool."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_check_phone(agent, phone: str):
    print(f"\n🔍 [CHECK] Verificando se {phone} está no WhatsApp...")
    result = await _run(agent,
        f"Check if the phone number {phone} is registered on WhatsApp "
        f"using whatsapp_check_phones."
    )
    print(f"✅ Resultado:\n{result}\n")


async def test_lookup_phone(agent, phone: str):
    print(f"\n🔍 [LOOKUP] Buscando perfil do número {phone}...")
    result = await _run(agent,
        f"Use whatsapp_get_user_info to get the WhatsApp profile info for phone number {phone}. "
        f"Report the display name and about."
    )
    print(f"✅ Resultado:\n{result}\n")


# ══════════════════════════════════════════════════════════════════════════════
# SUÍTE COMPLETA
# ══════════════════════════════════════════════════════════════════════════════

async def run_all(agent):
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
            await fn(agent)
            passed += 1
        except Exception as e:
            print(f"❌ [{name}] FALHOU: {e}\n")
            failed += 1

    print("\n" + "="*60)
    print(f"  ✅ Passou: {passed}  |  ❌ Falhou: {failed}")
    print("="*60 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# CHAT LIVRE (conversacional)
# ══════════════════════════════════════════════════════════════════════════════

async def chat_livre(agent):
    """Loop conversacional: o usuário digita qualquer coisa em português."""
    print("""
╔══════════════════════════════════════════════════════════╗
║  💬 Chat Livre — digite qualquer comando em português    ║
║                                                          ║
║  Exemplos:                                               ║
║    "quais mensagens não li hoje?"                        ║
║    "mande 'boa noite ❤️' para 5511999998888"             ║
║    "quantos grupos eu tenho?"                            ║
║    "qual o meu número logado?"                           ║
║    "liste os 5 contatos mais recentes"                   ║
║                                                          ║
║  Digite 'voltar' para retornar ao menu                   ║
╚══════════════════════════════════════════════════════════╝
""")

    while True:
        try:
            user_input = input("\n🧑 Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Saindo do chat...")
            break

        if not user_input:
            continue
        if user_input.lower() in ("voltar", "sair", "menu", "q"):
            print("↩️  Voltando ao menu...\n")
            break

        try:
            print("⏳ ", end="", flush=True)
            result = await asyncio.wait_for(agent.run(user_input), timeout=90.0)
            # Limpa o "⏳ " e mostra a resposta
            print(f"\r🤖 Assistente: {result}")
        except asyncio.TimeoutError:
            print(f"\r⏳ Timeout (90s) — o servidor MCP não respondeu. Verifique se o WuzAPI está rodando.")
        except Exception as e:
            erro_msg = str(e)
            if "rate_limit" in erro_msg.lower() or "429" in erro_msg:
                print(f"\r⏳ Limite de tokens atingido. Aguarde 1-2 min e tente novamente.")
            elif "413" in erro_msg:
                print(f"\r⏳ Request muito grande. Tente uma pergunta mais específica.")
            elif "connection" in erro_msg.lower() or "mcp" in erro_msg.lower():
                print(f"\r❌ Erro de conexão MCP: {erro_msg[:300]}")
            else:
                print(f"\r❌ Erro: {erro_msg[:200]}")


# ══════════════════════════════════════════════════════════════════════════════
# MENU INTERATIVO
# ══════════════════════════════════════════════════════════════════════════════

def _print_menu(provider: str, model: str):
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║    MCP WhatsApp — Tester ({provider}: {model[:18]:<18})      ║
╠══════════════════════════════════════════════════════════════╣
║  1.  Health check                                             ║
║  2.  Status da sessão                                         ║
║  3.  Info da sessão logada                                    ║
║  4.  Listar chats                                             ║
║  5.  Mensagens recebidas recentes                             ║
║  6.  Listar contatos                                          ║
║  7.  Listar grupos                                            ║
║  8.  Admin — listar usuários WuzAPI                           ║
║  9.  Verificar número no WhatsApp                             ║
║  10. Buscar perfil de um número                               ║
║  11. Enviar mensagem (REAL — cuidado!)                        ║
║  ─────────────────────────────────────────────────            ║
║  c.  💬 CHAT LIVRE (fale qualquer coisa)                     ║
║  ─────────────────────────────────────────────────            ║
║  0.  Rodar TODOS os testes (sem envio)                        ║
║  q.  Sair                                                     ║
╚══════════════════════════════════════════════════════════════╝
""")


async def interactive_menu(agent, provider: str, model: str):
    while True:
        _print_menu(provider, model)
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
            phone = input("  📞 Número (ex: 5511999998888): ").strip()
            if phone:
                await test_check_phone(agent, phone)
        elif choice == "10":
            phone = input("  📞 Número para buscar perfil (ex: 5511999998888): ").strip()
            if phone:
                await test_lookup_phone(agent, phone)
        elif choice == "11":
            phone   = input("  📞 Número destino (ex: 5511999998888): ").strip()
            message = input("  💬 Mensagem: ").strip()
            confirm = input(f"  ⚠️  Confirma envio REAL para {phone}? (s/N): ").strip().lower()
            if confirm == "s" and phone and message:
                await test_send(agent, phone, message)
            else:
                print("  ↩️  Cancelado.")
        elif choice == "c":
            await chat_livre(agent)
        else:
            print("  ⚠️  Opção inválida.")

        input("\n  [Enter para continuar]")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="Testa MCP WhatsApp com Groq ou OpenAI")
    parser.add_argument(
        "--provider", choices=["groq", "openai"], default="groq",
        help="Provider LLM (padrão: groq)"
    )
    parser.add_argument(
        "--model", default="",
        help="Modelo a usar (padrão vem do .env: GROQ_MODEL ou OPENAI_MODEL)"
    )
    parser.add_argument(
        "--test",
        choices=["health", "status", "send", "chats", "contacts", "groups", "admin", "unread", "all"],
        help="Teste específico para rodar"
    )
    parser.add_argument("--phone",   default="", help="Número para --test send")
    parser.add_argument("--message", default="Olá! Teste via MCP WhatsApp 🤖", help="Mensagem para --test send")
    parser.add_argument("--verbose", action="store_true", help="Mostra logs detalhados do agente (debug)")
    parser.add_argument("--chat",    action="store_true", help="Vai direto pro chat livre (sem menu)")
    args = parser.parse_args()

    provider = args.provider
    model    = args.model or (OPENAI_MODEL if provider == "openai" else GROQ_MODEL)

    # Se --verbose, habilita os loggers que silenciamos no topo
    if args.verbose:
        for _log in ("mcp_use", "mcp_use.agents.display"):
            logging.getLogger(_log).setLevel(logging.INFO)

    _check_requirements(provider)

    print(f"\n⚙️  Configuração:")
    print(f"   WuzAPI URL  : {WUZAPI_BASE_URL}")
    print(f"   Token       : {WUZAPI_TOKEN[:4]}{'*' * max(0, len(WUZAPI_TOKEN)-4)}")
    print(f"   Admin Token : {'✅' if WUZAPI_ADMIN_TOKEN else '⚠️  não configurado'}")
    print(f"   Provider    : {provider}")
    print(f"   Modelo      : {model}")

    print("\n🔧 Iniciando agente (pode demorar alguns segundos)...")
    agent = _build_agent(provider, model, verbose=args.verbose)

    if args.chat:
        await chat_livre(agent)
    elif args.test == "health":
        await test_health(agent)
    elif args.test == "status":
        await test_status(agent)
    elif args.test == "chats":
        await test_chats(agent)
    elif args.test == "unread":
        await test_unread(agent)
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
        await interactive_menu(agent, provider, model)


if __name__ == "__main__":
    asyncio.run(main())