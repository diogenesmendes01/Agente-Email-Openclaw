"""
VIP/Blacklist Manager - Gerencia listas VIP e blacklist de remetentes
"""

import json
import os
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
        import tempfile
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


# ============================================================
# VIP FUNCTIONS
# ============================================================

def add_vip(email: str, name: Optional[str] = None, min_urgency: str = "high") -> bool:
    """Adiciona remetente à lista VIP"""
    vip_list = load_json(VIP_FILE)
    
    # Verificar se já existe
    for entry in vip_list:
        if entry.get("email") == email:
            return False  # Já é VIP
    
    # Adicionar novo VIP
    vip_list.append({
        "email": email,
        "name": name or email.split("@")[0],
        "added": datetime.now().strftime("%Y-%m-%d"),
        "min_urgency": min_urgency
    })
    
    return save_json(VIP_FILE, vip_list)


def remove_vip(email: str) -> bool:
    """Remove remetente da lista VIP"""
    vip_list = load_json(VIP_FILE)
    
    # Filtrar para remover
    new_list = [entry for entry in vip_list if entry.get("email") != email]
    
    if len(new_list) == len(vip_list):
        return False  # Não estava na lista
    
    return save_json(VIP_FILE, new_list)


def is_vip(email: str) -> bool:
    """Verifica se remetente é VIP"""
    vip_list = load_json(VIP_FILE)
    
    for entry in vip_list:
        if entry.get("email") == email:
            return True
    
    return False


def get_min_urgency(email: str) -> Optional[str]:
    """Retorna urgência mínima para VIP (null se não é VIP)"""
    vip_list = load_json(VIP_FILE)
    
    for entry in vip_list:
        if entry.get("email") == email:
            return entry.get("min_urgency", "high")
    
    return None


def get_all_vips() -> List[Dict]:
    """Retorna lista completa de VIPs"""
    return load_json(VIP_FILE)


# ============================================================
# BLACKLIST FUNCTIONS
# ============================================================

def add_to_blacklist(email: str, reason: Optional[str] = None) -> bool:
    """Adiciona remetente à blacklist"""
    blacklist = load_json(BLACKLIST_FILE)
    
    # Verificar se já existe
    for entry in blacklist:
        if entry.get("email") == email:
            return False  # Já está na blacklist
    
    # Adicionar
    blacklist.append({
        "email": email,
        "reason": reason or "silenciado pelo usuário",
        "added": datetime.now().strftime("%Y-%m-%d")
    })
    
    return save_json(BLACKLIST_FILE, blacklist)


def remove_from_blacklist(email: str) -> bool:
    """Remove remetente da blacklist"""
    blacklist = load_json(BLACKLIST_FILE)
    
    new_list = [entry for entry in blacklist if entry.get("email") != email]
    
    if len(new_list) == len(blacklist):
        return False  # Não estava na lista
    
    return save_json(BLACKLIST_FILE, new_list)


def is_blacklisted(email: str) -> bool:
    """Verifica se remetente está na blacklist"""
    blacklist = load_json(BLACKLIST_FILE)
    
    for entry in blacklist:
        if entry.get("email") == email:
            return True
    
    return False


def get_blacklist_reason(email: str) -> Optional[str]:
    """Retorna motivo da blacklist"""
    blacklist = load_json(BLACKLIST_FILE)
    
    for entry in blacklist:
        if entry.get("email") == email:
            return entry.get("reason")
    
    return None


def get_all_blacklisted() -> List[Dict]:
    """Retorna lista completa de blacklist"""
    return load_json(BLACKLIST_FILE)


# ============================================================
# TESTE
# ============================================================

if __name__ == "__main__":
    print("=== Teste VIP/Blacklist Manager ===")
    
    # Testar VIP
    print("\n1. Adicionar VIP...")
    result = add_vip("relacionamento@pagar.me", "Pagar.me Atendimento")
    print(f"   Resultado: {result}")
    
    print("\n2. Verificar VIP...")
    is_vip_result = is_vip("relacionamento@pagar.me")
    print(f"   é VIP: {is_vip_result}")
    
    print("\n3. Urgência mínima...")
    min_urg = get_min_urgency("relacionamento@pagar.me")
    print(f"   min_urgency: {min_urg}")
    
    # Testar Blacklist
    print("\n4. Adicionar Blacklist...")
    result = add_to_blacklist("newsletter@spam.com", "newsletter chata")
    print(f"   Resultado: {result}")
    
    print("\n5. Verificar Blacklist...")
    is_blacklisted_result = is_blacklisted("newsletter@spam.com")
    print(f"   está blacklist: {is_blacklisted_result}")
    
    print("\n6. Listar VIPs...")
    vips = get_all_vips()
    print(f"   VIPs: {vips}")
    
    print("\n7. Listar Blacklist...")
    blacklist = get_all_blacklisted()
    print(f"   Blacklist: {blacklist}")
    
    print("\n✅ Testes concluídos!")