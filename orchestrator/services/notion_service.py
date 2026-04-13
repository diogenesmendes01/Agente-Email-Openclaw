"""
Notion Service - Gerencia conexão com Notion API via HTTP async
"""

import os
import logging
from typing import List, Optional, Dict, Any
import httpx

logger = logging.getLogger(__name__)


class NotionService:
    """Serviço para interagir com Notion API via HTTP (async)"""

    def __init__(self):
        self.api_key = os.getenv("NOTION_API_KEY")
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }

        self._connected = False

        # Database IDs do config.json (carregados via env ou hardcoded como fallback)
        self.db_config = os.getenv("NOTION_DB_CONFIG", "")
        self.db_projetos = os.getenv("NOTION_DB_PROJETOS", "")
        self.db_tarefas = os.getenv("NOTION_DB_TAREFAS", "")
        self.db_decisoes = os.getenv("NOTION_DB_DECISOES", "")

        if self.api_key and self.db_config:
            self._connected = True
            logger.info("NotionService inicializado com sucesso")
        else:
            if self.api_key and not self.db_config:
                logger.warning("NotionService: NOTION_DB_CONFIG não configurado")

    def is_connected(self) -> bool:
        return self._connected

    async def get_account_config(self, email: str) -> Dict[str, Any]:
        """Busca configuração da conta de email no Notion"""
        if not self.api_key:
            return self._default_config()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/databases/{self.db_config}/query",
                    headers=self.headers,
                    json={
                        "filter": {
                            "property": "Conta",
                            "email": {"equals": email}
                        }
                    }
                )

            if response.status_code == 200:
                data = response.json()

                if data["results"]:
                    page = data["results"][0]
                    props = page["properties"]

                    return {
                        "id": page["id"],
                        "email": email,
                        "vips": self._extract_multi_select(props.get("VIPs", {})),
                        "urgency_words": self._extract_multi_select(props.get("Palavras Urgência", {})),
                        "ignore_words": self._extract_multi_select(props.get("Palavras Ignorar", {})),
                        "tipo": self._extract_select(props.get("Tipo", {})),
                        "telegram_topic": props.get("Tópico Telegram", {}).get("number"),
                        "auto_reply": props.get("Resposta Auto", {}).get("checkbox", False),
                        "projetos": []
                    }

            return self._default_config()

        except Exception as e:
            logger.error(f"Erro ao buscar config do Notion: {e}")
            return self._default_config()

    async def create_task(self, task: Dict[str, Any], account: str) -> Optional[str]:
        """Cria uma nova tarefa no Notion"""
        if not self.api_key:
            return None

        try:
            properties = {
                "Name": {"title": [{"text": {"content": task.get("titulo", "Nova tarefa")}}]},
                "Prioridade": {"select": {"name": task.get("prioridade", "Média")}},
                "Origem": {"select": {"name": "email"}},
                "Email ID": {"rich_text": [{"text": {"content": task.get("email_id", "")}}]}
            }

            # Conta (account) - para separar tarefas por conta
            if account and "@" in account:
                properties["Conta"] = {"email": account}

            if task.get("prazo"):
                properties["Prazo"] = {"date": {"start": task["prazo"]}}

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/pages",
                    headers=self.headers,
                    json={
                        "parent": {"database_id": self.db_tarefas},
                        "properties": properties
                    }
                )

            if response.status_code == 200:
                data = response.json()
                logger.info(f"Task criada: {task.get('titulo')}")
                return data["id"]
            else:
                logger.error(f"Erro ao criar task: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Erro ao criar task no Notion: {e}")
            return None

    async def log_decision(self, decision: Dict[str, Any]) -> Optional[str]:
        """Salva decisão do email no Notion"""
        if not self.api_key:
            return None

        try:
            properties = {
                "Email ID": {"title": [{"text": {"content": decision.get("email_id", "")}}]},
                "Subject": {"rich_text": [{"text": {"content": decision.get("subject", "")[:100]}}]},
                "Classificação": {"select": {"name": decision.get("classificacao", "Importante")}},
                "Prioridade": {"select": {"name": decision.get("prioridade", "Média")}},
                "Categoria": {"select": {"name": decision.get("categoria", "trabalho")}},
                "Ação": {"select": {"name": decision.get("acao", "notificar")}},
                "Feedback": {"select": {"name": "pendente"}},
                "Timestamp": {"date": {"start": decision.get("timestamp", "")}},
                "Resumo": {"rich_text": [{"text": {"content": decision.get("resumo", "")[:200]}}]}
            }

            # Conta (account) - para separar decisões por conta
            account = decision.get("account", "")
            if account and "@" in account:
                properties["Conta"] = {"email": account}

            from_email = decision.get("from", "")
            if from_email and "@" in from_email:
                properties["De"] = {"email": from_email}
            else:
                properties["De"] = {"email": None}

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/pages",
                    headers=self.headers,
                    json={
                        "parent": {"database_id": self.db_decisoes},
                        "properties": properties
                    }
                )

            if response.status_code == 200:
                data = response.json()
                logger.info(f"Decisão salva: {decision.get('email_id')}")
                return data["id"]
            else:
                logger.error(f"Erro ao salvar decisão: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Erro ao salvar decisão no Notion: {e}")
            return None

    def _extract_multi_select(self, prop: Dict) -> List[str]:
        if prop.get("type") == "multi_select":
            return [opt["name"] for opt in prop.get("multi_select", [])]
        return []

    def _extract_select(self, prop: Dict) -> Optional[str]:
        if prop.get("type") == "select" and prop.get("select"):
            return prop["select"].get("name")
        return None

    def _default_config(self) -> Dict[str, Any]:
        return {
            "email": "default",
            "vips": [],
            "urgency_words": ["URGENTE", "ASAP", "urgente", "prazo", "vence", "hoje"],
            "ignore_words": ["newsletter", "unsubscribe", "promoção"],
            "tipo": "pessoal",
            "telegram_topic": None,
            "auto_reply": False,
            "projetos": []
        }
