"""
LLM Service - Integração com OpenRouter e OpenAI
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)


class LLMService:
    """Serviço para interagir com LLM via OpenRouter e OpenAI"""
    
    def __init__(self):
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        
        # Configuração do modelo principal
        self.model = "z-ai/glm-5-turbo"  # GLM-5 Turbo via OpenRouter (reasoning model)
        self.embedding_model = "text-embedding-3-small"
        
        # Cliente OpenAI para embeddings
        self.openai_client = None
        if self.openai_key:
            self.openai_client = OpenAI(api_key=self.openai_key)
        
        self._configured = bool(self.openrouter_key)
        
        if self._configured:
            logger.info(f"LLMService configurado com modelo {self.model}")
        else:
            logger.warning("LLMService não configurado - chaves não encontradas")
    
    def is_configured(self) -> bool:
        return self._configured
    
    async def classify_email(
        self,
        email: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Classifica email"""
        prompt = self._build_classifier_prompt(email, context)
        response = await self._call_llm(prompt, max_tokens=32768)
        
        if response:
            result = self._parse_classification(response.get("content", ""))
            result["reasoning_tokens"] = response.get("reasoning_tokens", 0)
            result["total_tokens"] = response.get("total_tokens", 0)
            return result
        
        return self._default_classification()
    
    async def summarize_email(
        self,
        email: Dict[str, Any],
        classification: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Gera resumo"""
        prompt = self._build_summarizer_prompt(email, classification)
        response = await self._call_llm(prompt, max_tokens=32768)
        
        if response:
            result = self._parse_summary(response.get("content", ""))
            result["reasoning_tokens"] = response.get("reasoning_tokens", 0)
            result["total_tokens"] = response.get("total_tokens", 0)
            return result
        
        return {"resumo": "Erro ao gerar resumo", "entidades": {}, "prazo": None}
    
    async def decide_action(
        self,
        email: Dict[str, Any],
        classification: Dict[str, Any],
        summary: Dict[str, Any],
        account_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Decide ação a tomar
        
        Args:
            email: Dados do email
            classification: Classificação
            summary: Resumo
            account_config: Config da conta (auto_reply, etc.)
        
        Returns:
            Dict com ação, task (se criar), draft_resposta (se responder)
        """
        prompt = self._build_action_prompt(email, classification, summary, account_config)
        
        response = await self._call_llm(prompt, max_tokens=32768)
        
        if response:
            result = self._parse_action(response.get("content", ""))
            result["reasoning_tokens"] = response.get("reasoning_tokens", 0)
            result["total_tokens"] = response.get("total_tokens", 0)
            return result
        
        return {"acao": "notificar", "justificativa": "Erro ao decidir"}
    
    async def create_embedding(self, text: str) -> Optional[List[float]]:
        """
        Cria embedding do texto usando OpenAI
        
        Args:
            text: Texto para embedding
        
        Returns:
            Lista de floats (1536 dims) ou None se falhar
        """
        if not self.openai_client:
            logger.warning("OpenAI não configurado para embeddings")
            return None
        
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=text[:8000]  # Limite de tokens
            )
            
            return response.data[0].embedding
            
        except Exception as e:
            logger.error(f"Erro ao criar embedding: {e}")
            return None
    
    async def _call_llm(self, prompt: str, max_tokens: int = 500) -> Optional[Dict[str, Any]]:
        """Chama LLM via OpenRouter - retorna dict com content e usage"""
        if not self.openrouter_key:
            logger.error("OpenRouter API key não configurada")
            return None
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openrouter_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://openclaw.ai",
                        "X-Title": "Email Agent"
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.3,
                        "thinking": {"type": "enabled"}
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    msg = data["choices"][0]["message"]
                    # Fallback: GLM-5 Turbo usa 'reasoning' em vez de 'content'
                    content = msg.get("content") or msg.get("reasoning")
                    
                    # Extrair tokens de reasoning
                    usage = data.get("usage", {})
                    reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)
                    
                    return {
                        "content": content,
                        "reasoning_tokens": reasoning_tokens,
                        "total_tokens": total_tokens
                    }
                else:
                    logger.error(f"Erro LLM: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Erro ao chamar LLM: {e}")
            return None
    
    def _build_classifier_prompt(self, email: Dict, context: Dict) -> str:
        """Constrói prompt de classificação"""
        vips = context.get("vips", [])
        urgency_words = context.get("urgency_words", [])
        ignore_words = context.get("ignore_words", [])
        similar = context.get("similar_emails", [])
        thread_context = context.get("thread_context", [])
        
        # Montar contexto da thread se houver
        thread_text = ""
        if thread_context:
            thread_text = "\n\nEMAILS ANTERIORES DESTA THREAD:\n"
            for i, msg in enumerate(thread_context[-2:]):  # Últimos 2
                thread_text += f"--- Mensagem {i+1} ---\n"
                thread_text += f"De: {msg.get('from', 'Desconhecido')}\n"
                thread_text += f"Data: {msg.get('date', '')}\n"
                thread_text += f"Texto: {msg.get('body', '')[:300]}\n\n"
        
        return f"""Você é um assistente de classificação de emails. Analise o email e classifique.

REMETENTES VIP (sempre importante):
{json.dumps(vips, ensure_ascii=False)}

PALAVRAS DE URGÊNCIA (aumentam prioridade):
{json.dumps(urgency_words, ensure_ascii=False)}

PALAVRAS PARA IGNORAR (provavelmente não importante):
{json.dumps(ignore_words, ensure_ascii=False)}
{thread_text}
EMAIL ATUAL:
De: {email.get("from", "")}
Para: {email.get("to", "")}
Assunto: {email.get("subject", "")}
Corpo: {email.get("body", "")[:1500]}

Responda em JSON:
{{
    "importante": true/false,
    "prioridade": "Alta/Média/Baixa",
    "categoria": "cliente/financeiro/pessoal/trabalho/promoção/newsletter/outro",
    "confianca": 0.0-1.0,
    "razao": "explicação breve",
    "entidades": {{
        "cliente": "nome se houver",
        "projeto": "nome se houver",
        "prazo": "data se mencionado",
        "protocolo": "número se houver"
    }}
}}"""
    
    def _build_summarizer_prompt(self, email: Dict, classification: Dict) -> str:
        """Constrói prompt de resumo"""
        return f"""Resuma este email em português. Responda APENAS com JSON válido, sem texto adicional.

EMAIL:
De: {email.get("from", "")}
Assunto: {email.get("subject", "")}
Corpo: {email.get("body", "")[:1500]}

Responda em JSON:
{{\"resumo\": \"resumo em 1-2 frases\", \"entidades\": {{\"cliente\": \"\"}}, \"sentimento\": \"neutro\"}}"""
    
    def _build_action_prompt(self, email: Dict, classification: Dict, summary: Dict, config: Dict) -> str:
        """Constrói prompt de ação"""
        auto_reply = config.get("auto_reply", False)

        return f"""Decida a ação apropriada para este email.

EMAIL:
De: {email.get("from", "")}
Assunto: {email.get("subject", "")}

CLASSIFICAÇÃO: {json.dumps(classification, ensure_ascii=False)}

RESUMO: {json.dumps(summary, ensure_ascii=False)}

CONFIGURAÇÃO:
- Resposta automática: {"PERMITIDA" if auto_reply else "NÃO PERMITIDA"}

AÇÕES POSSÍVEIS:
1. "notificar" - Apenas notificar no Telegram
2. "arquivar" - Arquivar email (newsletter, promoção)
3. "criar_task" - Criar tarefa no Notion
4. "rascunho" - Criar rascunho de resposta (sem enviar)

IMPORTANTE: SEMPRE gere o campo "rascunho_resposta", independente da ação escolhida.
Exceção: NÃO gere rascunho_resposta se a categoria for "spam" ou "newsletter".

O rascunho deve ser em português, profissional e terminado com:
Att, Diógenes Mendes

Responda em JSON:
{{\n    "acao": "notificar/arquivar/criar_task/rascunho",\n    "justificativa": "por que essa ação",\n    "task": {{\n        "titulo": "título da tarefa se criar_task",\n        "prioridade": "Alta/Média/Baixa",\n        "prazo": "YYYY-MM-DD se aplicável"\n    }},\n    "rascunho_resposta": "texto do rascunho sempre que não for spam/newsletter"\n}}"""
    
    def _parse_classification(self, response: str) -> Dict[str, Any]:
        """Parse da resposta de classificação"""
        try:
            # Tentar extrair JSON da resposta
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                return json.loads(response[json_start:json_end])
        except:
            pass
        
        return self._default_classification()
    
    def _parse_summary(self, response: str) -> Dict[str, Any]:
        """Parse da resposta de resumo"""
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                return json.loads(response[json_start:json_end])
        except:
            pass
        
        return {"resumo": response[:200], "entidades": {}, "sentimento": "neutro"}
    
    def _parse_action(self, response: str) -> Dict[str, Any]:
        """Parse da resposta de ação"""
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                return json.loads(response[json_start:json_end])
        except:
            pass
        
        return {"acao": "notificar", "justificativa": "Falha ao processar"}
    
    def _default_classification(self) -> Dict[str, Any]:
        """Classificação padrão"""
        return {
            "importante": True,
            "prioridade": "Média",
            "categoria": "outro",
            "confianca": 0.5,
            "razao": "Classificação padrão (erro no processamento)",
            "entidades": {}
        }