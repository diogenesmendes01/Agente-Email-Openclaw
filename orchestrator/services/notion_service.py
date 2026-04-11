"""
Notion Service - Gerencia conexão com Notion API (v3.x via HTTP)
"""

import os
import logging
from typing import List, Optional, Dict, Any
import httpx

logger = logging.getLogger(__name__)


class NotionService:
    """Serviço para interagir com Notion API via HTTP"""
    
    def __init__(self):
        self.api_key = os.getenv("NOTION_API_KEY")
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        
        self._connected = False
        
        # Database IDs
        self.db_config = "33d2b64f-5143-811a-93c9-eb5bb474c102"
        self.db_projetos = "33d2b64f-5143-8107-8d89-da96277a6635"
        self.db_tarefas = "33d2b64f-5143-81b5-803d-d042dbb50525"
        self.db_decisoes = "33d2b64f-5143-8139-85d4-dfd46d674216"
        
        if self.api_key:
            self._connected = True
            logger.info("NotionService inicializado com sucesso")
    
    def is_connected(self) -> bool:
        return self._connected
    
    def get_account_config(self, email: str) -> Dict[str, Any]:
        """Busca configuração da conta de email no Notion"""
        if not self.api_key:
            return self._default_config()
        
        try:
            response = httpx.post(
                f"{self.base_url}/databases/{self.db_config}/query",
                headers=self.headers,
                json={
                    "filter": {
                        "property": "Conta",
                        "email": {"equals": email}
                    }
                },
                timeout=30.0
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
    
    def create_task(self, task: Dict[str, Any], account: str) -> Optional[str]:
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
            
            if task.get("prazo"):
                properties["Prazo"] = {"date": {"start": task["prazo"]}}
            
            response = httpx.post(
                f"{self.base_url}/pages",
                headers=self.headers,
                json={
                    "parent": {"database_id": self.db_tarefas},
                    "properties": properties
                },
                timeout=30.0
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
    
    def log_decision(self, decision: Dict[str, Any]) -> Optional[str]:
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
            
            # Adicionar campo "De" se tiver email válido
            from_email = decision.get("from", "")
            if from_email and "@" in from_email:
                properties["De"] = {"email": from_email}
            else:
                properties["De"] = {"email": None}
            
            response = httpx.post(
                f"{self.base_url}/pages",
                headers=self.headers,
                json={
                    "parent": {"database_id": self.db_decisoes},
                    "properties": properties
                },
                timeout=30.0
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