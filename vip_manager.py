"""
VIP/Blacklist Manager - Gerencia listas VIP e blacklist de remetentes
Separado por conta (account) para suporte multi-conta.
"""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

_BASE_DIR = Path(os.getenv("EMAIL_AGENT_BASE_DIR", Path(__file__).resolve().parent))

VIP_FILE = str(_BASE_DIR / "vip-list.json")
BLACKLIST_FILE = str(_BASE_DIR / "blacklist.json")


def load_json(filepath: str) -> List[Dict]:
    """Carrega lista de um arquivo JSON"""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_json(filepath: str, data: List[Dict]) -> bool:
    """Salva lista em arquivo JSON (escrita atômica)"""
    try:
        dir_path = os.path.dirname(filepath) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, filepath)
            return True
        except Exception:
            os.unlink(tmp_path)
            raise
    except Exception as e:
        print(f"Erro ao salvar {filepath}: {e}")
        return False


def _matches_account(entry: Dict, account: str) -> bool:
    """Verifica se entry pertence à conta. Entries sem account são globais (backward compat)."""
    entry_account = entry.get("account", "")
    if not entry_account:
        return True  # Entry antiga sem account → aceita para qualquer conta
    return entry_account == account


# ============================================================
# VIP FUNCTIONS
# ============================================================

def add_vip(email: str, name: Optional[str] = None, min_urgency: str = "high", account: str = "") -> bool:
    """Adiciona remetente à lista VIP (scoped por account)"""
    vip_list = load_json(VIP_FILE)

    # Verificar se já existe para esta conta
    for entry in vip_list:
        if entry.get("email") == email and _matches_account(entry, account):
            return False  # Já é VIP

    vip_list.append({
        "email": email,
        "name": name or email.split("@")[0],
        "added": datetime.now().strftime("%Y-%m-%d"),
        "min_urgency": min_urgency,
        "account": account
    })

    return save_json(VIP_FILE, vip_list)


def remove_vip(email: str, account: str = "") -> bool:
    """Remove remetente da lista VIP"""
    vip_list = load_json(VIP_FILE)

    new_list = [
        entry for entry in vip_list
        if not (entry.get("email") == email and _matches_account(entry, account))
    ]

    if len(new_list) == len(vip_list):
        return False

    return save_json(VIP_FILE, new_list)


def is_vip(email: str, account: str = "") -> bool:
    """Verifica se remetente é VIP para esta conta"""
    vip_list = load_json(VIP_FILE)

    for entry in vip_list:
        if entry.get("email") == email and _matches_account(entry, account):
            return True

    return False


def get_min_urgency(email: str, account: str = "") -> Optional[str]:
    """Retorna urgência mínima para VIP (null se não é VIP)"""
    vip_list = load_json(VIP_FILE)

    for entry in vip_list:
        if entry.get("email") == email and _matches_account(entry, account):
            return entry.get("min_urgency", "high")

    return None


def get_all_vips(account: str = "") -> List[Dict]:
    """Retorna lista de VIPs para esta conta"""
    vip_list = load_json(VIP_FILE)
    if not account:
        return vip_list
    return [entry for entry in vip_list if _matches_account(entry, account)]


# ============================================================
# BLACKLIST FUNCTIONS
# ============================================================

def add_to_blacklist(email: str, reason: Optional[str] = None, account: str = "") -> bool:
    """Adiciona remetente à blacklist (scoped por account)"""
    blacklist = load_json(BLACKLIST_FILE)

    for entry in blacklist:
        if entry.get("email") == email and _matches_account(entry, account):
            return False  # Já está na blacklist

    blacklist.append({
        "email": email,
        "reason": reason or "silenciado pelo usuário",
        "added": datetime.now().strftime("%Y-%m-%d"),
        "account": account
    })

    return save_json(BLACKLIST_FILE, blacklist)


def remove_from_blacklist(email: str, account: str = "") -> bool:
    """Remove remetente da blacklist"""
    blacklist = load_json(BLACKLIST_FILE)

    new_list = [
        entry for entry in blacklist
        if not (entry.get("email") == email and _matches_account(entry, account))
    ]

    if len(new_list) == len(blacklist):
        return False

    return save_json(BLACKLIST_FILE, new_list)


def is_blacklisted(email: str, account: str = "") -> bool:
    """Verifica se remetente está na blacklist para esta conta"""
    blacklist = load_json(BLACKLIST_FILE)

    for entry in blacklist:
        if entry.get("email") == email and _matches_account(entry, account):
            return True

    return False


def get_blacklist_reason(email: str, account: str = "") -> Optional[str]:
    """Retorna motivo da blacklist"""
    blacklist = load_json(BLACKLIST_FILE)

    for entry in blacklist:
        if entry.get("email") == email and _matches_account(entry, account):
            return entry.get("reason")

    return None


def get_all_blacklisted(account: str = "") -> List[Dict]:
    """Retorna lista completa de blacklist para esta conta"""
    blacklist = load_json(BLACKLIST_FILE)
    if not account:
        return blacklist
    return [entry for entry in blacklist if _matches_account(entry, account)]


# ============================================================
# TESTE
# ============================================================

if __name__ == "__main__":
    print("=== Teste VIP/Blacklist Manager ===")

    test_account = "teste@gmail.com"

    print("\n1. Adicionar VIP...")
    result = add_vip("relacionamento@pagar.me", "Pagar.me Atendimento", account=test_account)
    print(f"   Resultado: {result}")

    print("\n2. Verificar VIP (mesma conta)...")
    print(f"   é VIP: {is_vip('relacionamento@pagar.me', account=test_account)}")

    print("\n3. Verificar VIP (outra conta)...")
    print(f"   é VIP: {is_vip('relacionamento@pagar.me', account='outra@gmail.com')}")

    print("\n4. Adicionar Blacklist...")
    result = add_to_blacklist("newsletter@spam.com", "newsletter chata", account=test_account)
    print(f"   Resultado: {result}")

    print("\n5. Verificar Blacklist...")
    print(f"   blacklisted: {is_blacklisted('newsletter@spam.com', account=test_account)}")

    print("\n6. Listar VIPs da conta...")
    print(f"   VIPs: {get_all_vips(account=test_account)}")

    print("\n✅ Testes concluídos!")
