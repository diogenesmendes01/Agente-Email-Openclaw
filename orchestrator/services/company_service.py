"""
CompanyService - Fetches company profiles, clients, domain rules from Notion.
In-memory TTL cache to avoid Notion rate limits (3 req/sec).
"""

import os
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
import httpx

logger = logging.getLogger(__name__)

CACHE_TTL = 300  # 5 minutes


class CompanyService:
    """Fetches and caches company context from Notion databases."""

    def __init__(self):
        self.api_key = os.getenv("NOTION_API_KEY")
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.db_profiles = os.getenv("NOTION_DB_COMPANY_PROFILES", "")
        self.db_clientes = os.getenv("NOTION_DB_CLIENTES", "")
        self.db_domain_rules = os.getenv("NOTION_DB_DOMAIN_RULES", "")

        self._cache: Dict[str, Tuple[float, Dict]] = {}

    async def get_profile(self, account: str) -> Dict[str, Any]:
        """Returns unified company profile for an email account. Uses TTL cache."""
        if account in self._cache:
            cached_time, cached_profile = self._cache[account]
            if time.time() - cached_time < CACHE_TTL:
                return cached_profile

        if not self.api_key or not self.db_profiles:
            return {}

        try:
            profile = await self._fetch_profile(account)
            if profile:
                self._cache[account] = (time.time(), profile)
            return profile
        except Exception as e:
            logger.error(f"Erro ao buscar company profile: {e}")
            return {}

    async def _fetch_profile(self, account: str) -> Dict[str, Any]:
        """Fetches company profile, clients, and domain rules from Notion."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/databases/{self.db_profiles}/query",
                headers=self.headers,
                json={
                    "filter": {
                        "property": "Conta Email",
                        "email": {"equals": account}
                    }
                }
            )

            if resp.status_code != 200 or not resp.json().get("results"):
                return {}

            page = resp.json()["results"][0]
            page_id = page["id"]
            props = page["properties"]

            profile = {
                "nome": self._extract_title(props.get("Nome", {})),
                "setor": self._extract_select(props.get("Setor", {})),
                "tom": self._extract_select(props.get("Tom", {})),
                "assinatura": self._extract_rich_text(props.get("Assinatura", {})),
                "idioma": self._extract_select(props.get("Idioma Padrao", {})) or "pt-BR",
                "clientes": [],
                "domain_rules": [],
            }

            if self.db_clientes:
                profile["clientes"] = await self._fetch_clients(client, page_id)

            if self.db_domain_rules:
                profile["domain_rules"] = await self._fetch_domain_rules(client, page_id)

            return profile

    async def _fetch_clients(self, client: httpx.AsyncClient, profile_page_id: str) -> List[Dict]:
        """Fetches clients related to a company profile."""
        try:
            resp = await client.post(
                f"{self.base_url}/databases/{self.db_clientes}/query",
                headers=self.headers,
                json={
                    "filter": {
                        "property": "Company Profile",
                        "relation": {"contains": profile_page_id}
                    }
                }
            )
            if resp.status_code != 200:
                return []

            clients = []
            for page in resp.json().get("results", []):
                props = page["properties"]
                contatos_raw = self._extract_rich_text(props.get("Contatos", {}))
                contatos = [c.strip() for c in contatos_raw.split(",") if c.strip()] if contatos_raw else []
                clients.append({
                    "nome": self._extract_title(props.get("Nome", {})),
                    "contatos": contatos,
                    "projeto": self._extract_rich_text(props.get("Projeto Ativo", {})),
                    "prioridade": self._extract_select(props.get("Prioridade", {})),
                    "notas": self._extract_rich_text(props.get("Notas", {})),
                })
            return clients
        except Exception as e:
            logger.error(f"Erro ao buscar clientes: {e}")
            return []

    async def _fetch_domain_rules(self, client: httpx.AsyncClient, profile_page_id: str) -> List[Dict]:
        """Fetches domain rules related to a company profile."""
        try:
            resp = await client.post(
                f"{self.base_url}/databases/{self.db_domain_rules}/query",
                headers=self.headers,
                json={
                    "filter": {
                        "property": "Company Profile",
                        "relation": {"contains": profile_page_id}
                    }
                }
            )
            if resp.status_code != 200:
                return []

            rules = []
            for page in resp.json().get("results", []):
                props = page["properties"]
                rules.append({
                    "dominio": self._extract_title(props.get("Dominio", {})),
                    "categoria": self._extract_select(props.get("Categoria", {})),
                    "prioridade_minima": self._extract_select(props.get("Prioridade Minima", {})),
                    "acao_padrao": self._extract_select(props.get("Acao Padrao", {})),
                })
            return rules
        except Exception as e:
            logger.error(f"Erro ao buscar domain rules: {e}")
            return []

    def match_domain_rule(self, sender_email: str, domain_rules: List[Dict]) -> Optional[Dict]:
        """Matches sender email against domain rules. Supports subdomain matching."""
        if not sender_email or "@" not in sender_email:
            return None

        sender_domain = sender_email.split("@")[1].lower()

        for rule in domain_rules:
            rule_domain = rule.get("dominio", "").lstrip("@").lower()
            if not rule_domain:
                continue
            if sender_domain == rule_domain or sender_domain.endswith(f".{rule_domain}"):
                return rule

        return None

    def is_client_contact(self, sender_email: str, clients: List[Dict]) -> Optional[Dict]:
        """Checks if sender is a known client contact. Returns client dict or None."""
        if not sender_email:
            return None
        sender_lower = sender_email.lower()
        for client in clients:
            for contato in client.get("contatos", []):
                if contato.lower() == sender_lower:
                    return client
        return None

    def _extract_title(self, prop: Dict) -> str:
        title_list = prop.get("title", [])
        return title_list[0]["text"]["content"] if title_list else ""

    def _extract_select(self, prop: Dict) -> Optional[str]:
        sel = prop.get("select")
        return sel["name"] if sel else None

    def _extract_rich_text(self, prop: Dict) -> str:
        rt_list = prop.get("rich_text", [])
        return rt_list[0]["text"]["content"] if rt_list else ""
