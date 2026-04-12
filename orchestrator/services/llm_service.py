"""
LLM Service - Integração com OpenRouter e OpenAI
Com retry automático via tenacity.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
import httpx
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)


class LLMService:
    """Serviço para interagir com LLM via OpenRouter e OpenAI"""

    def __init__(self):
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")

        self.model = os.getenv("LLM_MODEL", "z-ai/glm-5-turbo")
        self.embedding_model = "text-embedding-3-small"

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
        classification: Dict[str, Any],
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Gera resumo"""
        prompt = self._build_summarizer_prompt(email, classification, context)
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
        account_config: Dict[str, Any],
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Decide ação a tomar"""
        prompt = self._build_action_prompt(email, classification, summary, account_config, context)

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
        """Chama LLM via OpenRouter com retry automático"""
        if not self.openrouter_key:
            logger.error("OpenRouter API key não configurada")
            return None

        return await self._call_llm_with_retry(prompt, max_tokens)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True
    )
    async def _call_llm_with_retry(self, prompt: str, max_tokens: int) -> Optional[Dict[str, Any]]:
        """Implementação com retry exponencial"""
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
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.3,
                        "thinking": {"type": "enabled"}
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    msg = data["choices"][0]["message"]
                    content = msg.get("content") or msg.get("reasoning")

                    usage = data.get("usage", {})
                    details = usage.get("completion_tokens_details") or {}
                    reasoning_tokens = details.get("reasoning_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)

                    return {
                        "content": content,
                        "reasoning_tokens": reasoning_tokens,
                        "total_tokens": total_tokens
                    }
                elif response.status_code == 429:
                    logger.warning("Rate limited pelo OpenRouter, tentando novamente...")
                    raise httpx.TimeoutException("Rate limited")
                else:
                    logger.error(f"Erro LLM: {response.status_code} - {response.text[:200]}")
                    return None

        except (httpx.TimeoutException, httpx.ConnectError):
            raise  # Deixa o retry handler tratar
        except Exception as e:
            logger.error(f"Erro ao chamar LLM: {e}")
            return None
    
    MAX_PROMPT_TOKENS = 6000

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token for mixed pt-BR/en"""
        return len(text) // 4

    def _manage_prompt_size(self, prompt: str) -> str:
        """Truncates prompt if it exceeds MAX_PROMPT_TOKENS.
        Truncation priority: 1) thread context, 2) similar emails, 3) email body"""
        estimated = self._estimate_tokens(prompt)
        if estimated <= self.MAX_PROMPT_TOKENS:
            return prompt

        # Try removing thread context
        if "EMAILS ANTERIORES DESTA THREAD:" in prompt:
            thread_start = prompt.index("EMAILS ANTERIORES DESTA THREAD:")
            thread_end = prompt.find("EMAIL ATUAL:", thread_start)
            if thread_end > thread_start:
                prompt = prompt[:thread_start] + prompt[thread_end:]

        estimated = self._estimate_tokens(prompt)
        if estimated <= self.MAX_PROMPT_TOKENS:
            return prompt

        # Try truncating similar emails section
        if "EMAILS SIMILARES" in prompt:
            similar_start = prompt.index("EMAILS SIMILARES")
            similar_end = prompt.find("\n\n", similar_start + 50)
            if similar_end > similar_start:
                prompt = prompt[:similar_start] + prompt[similar_end:]

        estimated = self._estimate_tokens(prompt)
        if estimated <= self.MAX_PROMPT_TOKENS:
            return prompt

        # Last resort: truncate email body further
        if "Corpo:" in prompt:
            body_start = prompt.index("Corpo:") + 7
            body_end = prompt.find("\n\nResponda em JSON", body_start)
            if body_end > body_start:
                overshoot_chars = (estimated - self.MAX_PROMPT_TOKENS) * 4
                body = prompt[body_start:body_end]
                max_body = max(200, len(body) - overshoot_chars)
                if len(body) > max_body:
                    prompt = prompt[:body_start] + body[:max_body] + "..." + prompt[body_end:]

        return prompt

    def _build_classifier_prompt(self, email: Dict, context: Dict) -> str:
        """Constrói prompt de classificação com contexto enriquecido"""
        vips = context.get("vips", [])
        urgency_words = context.get("urgency_words", [])
        ignore_words = context.get("ignore_words", [])
        similar = context.get("similar_emails", [])
        thread_context = context.get("thread_context", [])
        company = context.get("company_profile", {})
        sender_profile = context.get("sender_profile", {})
        learned_rules = context.get("learned_rules", [])
        domain_rules = context.get("domain_rules", [])

        sections = []

        if company:
            sections.append(
                f"CONTEXTO DA EMPRESA:\n"
                f"Empresa: {company.get('nome', 'N/A')}\n"
                f"Setor: {company.get('setor', 'N/A')}\n"
                f"Tom: {company.get('tom', 'N/A')}"
            )

        if domain_rules:
            rules_text = "\n".join(
                f"- {r.get('dominio')}: categoria={r.get('categoria')}, "
                f"prioridade_min={r.get('prioridade_minima')}, acao={r.get('acao_padrao')}"
                for r in domain_rules[:5]
            )
            sections.append(f"REGRAS DE DOMINIO (manuais - PRIORIDADE MAXIMA, sempre seguir):\n{rules_text}")

        if learned_rules:
            manual_domains = {r.get("dominio", "").lstrip("@").lower() for r in domain_rules} if domain_rules else set()
            filtered_rules = [
                r for r in learned_rules
                if not (r.get("rule_type") == "domain" and r.get("match", "").lstrip("@").lower() in manual_domains)
            ]
            if filtered_rules:
                rules_text = "\n".join(
                    f"- [{r.get('rule_type')}] {r.get('match')}: "
                    f"{r.get('action')}={r.get('value')} (confianca: {r.get('confidence', 0):.0%})"
                    for r in filtered_rules[:10]
                )
                sections.append(f"REGRAS APRENDIDAS (automaticas - usar quando nao houver regra manual):\n{rules_text}")

        if sender_profile and sender_profile.get("count", 0) > 0:
            sp = sender_profile
            profile_text = (
                f"PERFIL DO REMETENTE:\n"
                f"Emails anteriores: {sp.get('count', 0)}\n"
                f"Taxa importante: {sp.get('important_rate', 0):.0%}\n"
                f"Taxa acerto: {sp.get('correct_rate', 0):.0%}"
            )
            patterns = sp.get("correction_patterns", [])
            if patterns:
                corrections = ", ".join(
                    f"{p['from']}->{p['to']} ({p['count']}x)" for p in patterns[:3]
                )
                profile_text += f"\nCorrecoes: {corrections}"
            if sp.get("is_client"):
                profile_text += f"\nCliente: {sp.get('client_name', 'Sim')} - Projeto: {sp.get('client_project', 'N/A')}"
            sections.append(profile_text)

        if similar:
            similar_text = "EMAILS SIMILARES (com feedback do usuario):\n"
            for i, s in enumerate(similar[:3]):
                p = s.get("payload", {})
                feedback = p.get("feedback", "pendente")
                line = f"{i+1}. De: {p.get('from_email', '?')} | Assunto: \"{p.get('subject', '?')}\""
                if feedback == "corrected":
                    orig_p = p.get("feedback_original_priority", "?")
                    corr_p = p.get("feedback_corrected_priority", "?")
                    line += f"\n   Classificacao: {orig_p} -> Usuario corrigiu para: {corr_p}"
                elif feedback == "confirmed":
                    line += "\n   Usuario confirmou classificacao"
                similar_text += line + "\n"
            sections.append(similar_text)

        thread_text = ""
        if thread_context:
            thread_text = "\nEMAILS ANTERIORES DESTA THREAD:\n"
            for i, msg in enumerate(thread_context[-2:]):
                thread_text += f"--- Mensagem {i+1} ---\n"
                thread_text += f"De: {msg.get('from', 'Desconhecido')}\n"
                thread_text += f"Data: {msg.get('date', '')}\n"
                thread_text += f"Texto: {msg.get('body', '')[:300]}\n\n"

        enrichment = "\n\n".join(sections)

        prompt = f"""Voce e um assistente de classificacao de emails. Analise o email e classifique.

{enrichment}

REMETENTES VIP (sempre importante):
{json.dumps(vips, ensure_ascii=False)}

PALAVRAS DE URGENCIA (aumentam prioridade):
{json.dumps(urgency_words, ensure_ascii=False)}

PALAVRAS PARA IGNORAR (provavelmente nao importante):
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
    "prioridade": "Alta/Media/Baixa",
    "categoria": "cliente/financeiro/pessoal/trabalho/promocao/newsletter/outro",
    "confianca": 0.0-1.0,
    "razao": "explicacao breve",
    "entidades": {{
        "cliente": "nome se houver",
        "projeto": "nome se houver",
        "prazo": "data se mencionado",
        "protocolo": "numero se houver"
    }}
}}"""

        return self._manage_prompt_size(prompt)
    
    def _build_summarizer_prompt(self, email: Dict, classification: Dict, context: Dict = None) -> str:
        """Constrói prompt de resumo com contexto da empresa"""
        context = context or {}
        company = context.get("company_profile", {})

        company_section = ""
        if company:
            company_section = f"\nEmpresa: {company.get('nome', 'N/A')} ({company.get('setor', 'N/A')})\n"

        prompt = f"""Resuma este email em portugues. Responda APENAS com JSON valido, sem texto adicional.
{company_section}
EMAIL:
De: {email.get("from", "")}
Assunto: {email.get("subject", "")}
Corpo: {email.get("body", "")[:1500]}

Responda em JSON:
{{"resumo": "resumo em 1-2 frases", "entidades": {{"cliente": ""}}, "sentimento": "neutro"}}"""

        return self._manage_prompt_size(prompt)
    
    def _build_action_prompt(
        self, email: Dict, classification: Dict, summary: Dict,
        config: Dict, context: Dict = None
    ) -> str:
        """Constrói prompt de ação com contexto da empresa"""
        auto_reply = config.get("auto_reply", False)
        context = context or {}
        company = context.get("company_profile", {})
        sender_profile = context.get("sender_profile", {})

        company_section = ""
        if company:
            tom = company.get("tom", "profissional")
            assinatura = company.get("assinatura", "")
            idioma = company.get("idioma", "pt-BR")
            company_section = (
                f"\nCONTEXTO DA EMPRESA:\n"
                f"Tom: {tom}\n"
                f"Idioma: {idioma}\n"
            )
            if assinatura:
                company_section += f"Assinatura:\n{assinatura}\n"

        client_section = ""
        if sender_profile.get("is_client"):
            client_section = (
                f"\nCONTEXTO DO CLIENTE:\n"
                f"Cliente: {sender_profile.get('client_name', 'Desconhecido')}\n"
                f"Projeto: {sender_profile.get('client_project', 'N/A')}\n"
            )

        prompt = f"""Decida a acao apropriada para este email.

EMAIL:
De: {email.get("from", "")}
Assunto: {email.get("subject", "")}

CLASSIFICACAO: {json.dumps(classification, ensure_ascii=False)}

RESUMO: {json.dumps(summary, ensure_ascii=False)}
{company_section}{client_section}
CONFIGURACAO:
- Resposta automatica: {"PERMITIDA" if auto_reply else "NAO PERMITIDA"}

ACOES POSSIVEIS:
1. "notificar" - Apenas notificar no Telegram
2. "arquivar" - Arquivar email (newsletter, promocao)
3. "criar_task" - Criar tarefa no Notion
4. "rascunho" - Criar rascunho de resposta (sem enviar)

IMPORTANTE: SEMPRE gere o campo "rascunho_resposta", independente da acao escolhida.
Excecao: NAO gere rascunho_resposta se a categoria for "spam" ou "newsletter".

O rascunho deve ser em {company.get('idioma', 'portugues')}, tom {company.get('tom', 'profissional')}.
{f'Use esta assinatura: {company.get("assinatura", "")}' if company.get('assinatura') else 'Termine com: Att, Diogenes Mendes'}

Responda em JSON:
{{
    "acao": "notificar/arquivar/criar_task/rascunho",
    "justificativa": "por que essa acao",
    "task": {{
        "titulo": "titulo da tarefa se criar_task",
        "prioridade": "Alta/Media/Baixa",
        "prazo": "YYYY-MM-DD se aplicavel"
    }},
    "rascunho_resposta": "texto do rascunho sempre que nao for spam/newsletter"
}}"""

        return self._manage_prompt_size(prompt)
    
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