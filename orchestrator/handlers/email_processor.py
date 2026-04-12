"""
Email Processor - Orquestra o processamento de emails
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from orchestrator.services.notion_service import NotionService
from orchestrator.services.qdrant_service import QdrantService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.gog_service import GOGService
from orchestrator.services.telegram_service import TelegramService
from orchestrator.utils.email_parser import EmailParser
from orchestrator.utils.text_cleaner import TextCleaner
from orchestrator.services.company_service import CompanyService
from orchestrator.services.learning_engine import LearningEngine

logger = logging.getLogger(__name__)


class EmailProcessor:
    """Processador principal de emails"""

    def __init__(
        self,
        notion: NotionService,
        qdrant: QdrantService,
        llm: LLMService,
        gog: GOGService,
        telegram: TelegramService,
        company: CompanyService = None,
        learning: LearningEngine = None
    ):
        self.notion = notion
        self.qdrant = qdrant
        self.llm = llm
        self.gog = gog
        self.telegram = telegram
        self.company = company
        self.learning = learning
        self.parser = EmailParser()
        self.cleaner = TextCleaner()
        self._learning_interval = int(os.getenv("LEARNING_INTERVAL", "50"))
        self._emails_processed = 0
        self._counter_loaded = False
    
    async def process_email(self, email_id: str, account: str) -> Dict[str, Any]:
        """
        Processa um email completo
        
        Fluxo:
        1. Fetch email via GOG
        2. Parse e limpeza
        3. Buscar contexto (Notion + Qdrant)
        4. Gerar embedding
        5. Classificar
        6. Resumir
        7. Decidir ação
        8. Persistir decisão
        9. Executar ação
        10. Notificar Telegram
        
        Args:
            email_id: ID do email no Gmail
            account: Email da conta
        
        Returns:
            Dict com resultado do processamento
        """
        result = {
            "email_id": email_id,
            "account": account,
            "status": "processing",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        try:
            # 1. Fetch email
            logger.info(f"[{email_id}] Buscando email...")
            raw_email = await self.gog.get_email(email_id, account)
            
            if not raw_email:
                result["status"] = "error"
                result["error"] = "Não foi possível buscar o email"
                return result
            
            # 2. Parse e limpeza (se raw_email for string, parse; se dict, usar direto)
            logger.info(f"[{email_id}] Parseando email...")
            if isinstance(raw_email, str):
                email = self.parser.parse(raw_email)
            else:
                email = raw_email  # Já parseado pelo GOGService
            email["body_clean"] = self.cleaner.clean(email.get("body", ""))
            
            # 2.5. Buscar contexto da thread se houver
            thread_id = email.get("threadId")
            thread_context = []
            if thread_id and thread_id != email_id:
                logger.info(f"[{email_id}] Buscando contexto da thread {thread_id}...")
                thread_emails = await self.gog.get_thread(thread_id, account)
                if thread_emails:
                    # Pegar últimos emails da thread como contexto
                    thread_context = thread_emails[-3:]  # Últimos 3 emails
                    logger.info(f"[{email_id}] Thread com {len(thread_emails)} mensagens")
            
            # 3. Buscar contexto
            logger.info(f"[{email_id}] Buscando contexto...")
            config = await self.notion.get_account_config(account)
            
            context = {
                "vips": config.get("vips", []),
                "urgency_words": config.get("urgency_words", []),
                "ignore_words": config.get("ignore_words", []),
                "projetos": config.get("projetos", []),
                "thread_context": thread_context  # Emails anteriores da thread
            }
            
            # Buscar emails similares (se Qdrant disponível)
            if self.qdrant.is_connected():
                # Gerar embedding para busca
                email_text = f"{email.get('subject', '')} {email.get('body_clean', '')}"
                embedding = await self.llm.create_embedding(email_text[:8000])
                
                if embedding:
                    similar = await self.qdrant.search_similar(embedding, account, limit=5)
                    context["similar_emails"] = similar
                    result["embedding"] = embedding
            else:
                context["similar_emails"] = []

            # Fetch company profile (cached)
            if self.company:
                try:
                    company_profile = await self.company.get_profile(account)
                    context["company_profile"] = company_profile

                    # Cross-reference sender with clients
                    from_email = email.get("from_email", "") or email.get("from", "")
                    if company_profile.get("clientes"):
                        client = self.company.is_client_contact(from_email, company_profile["clientes"])
                        if client:
                            context["sender_profile_client"] = client

                    # Domain rules from Notion
                    if company_profile.get("domain_rules"):
                        context["domain_rules"] = company_profile["domain_rules"]
                        domain_match = self.company.match_domain_rule(from_email, company_profile["domain_rules"])
                        if domain_match:
                            context["domain_match"] = domain_match
                except Exception as e:
                    logger.warning(f"[{email_id}] Erro ao buscar company profile: {e}")

            # Fetch sender profile from Qdrant
            if self.qdrant.is_connected():
                try:
                    from_email = email.get("from_email", "") or email.get("from", "")
                    sender_profile = await self.qdrant.get_sender_profile(from_email, account)
                    # Enrich with client info
                    if context.get("sender_profile_client"):
                        client = context["sender_profile_client"]
                        sender_profile["is_client"] = True
                        sender_profile["client_name"] = client.get("nome", "")
                        sender_profile["client_project"] = client.get("projeto", "")
                    context["sender_profile"] = sender_profile
                except Exception as e:
                    logger.warning(f"[{email_id}] Erro ao buscar sender profile: {e}")

            # Fetch learned rules from Qdrant
            if self.qdrant.is_connected():
                try:
                    learned_rules = await self.qdrant.get_learned_rules(account)
                    context["learned_rules"] = learned_rules
                except Exception as e:
                    logger.warning(f"[{email_id}] Erro ao buscar learned rules: {e}")

            # 4. Classificar
            logger.info(f"[{email_id}] Classificando...")
            classification = await self.llm.classify_email(email, context)
            result["classification"] = classification
            
            # Se não for importante e tiver baixa confiança, pular resumo
            if not classification.get("importante") and classification.get("confianca", 0) > 0.8:
                logger.info(f"[{email_id}] Email não importante, pulando...")
                result["status"] = "skipped"
                result["reason"] = "Email não importante"
                return result
            
            # 5. Resumir
            logger.info(f"[{email_id}] Resumindo...")
            summary = await self.llm.summarize_email(email, classification, context)
            result["summary"] = summary
            
            # 6. Decidir ação
            logger.info(f"[{email_id}] Decidindo ação...")
            action = await self.llm.decide_action(email, classification, summary, config, context)
            result["action"] = action
            
            # 7. Persistir decisão
            decision_data = {
                "email_id": email_id,
                "account": account,
                "subject": email.get("subject", ""),
                "from": email.get("from", ""),
                "classificacao": classification.get("categoria", "outro"),
                "prioridade": classification.get("prioridade", "Média"),
                "categoria": classification.get("categoria", "outro"),
                "acao": action.get("acao", "notificar"),
                "resumo": summary.get("resumo", ""),
                "timestamp": result["timestamp"]
            }
            
            notion_page_id = await self.notion.log_decision(decision_data)
            result["notion_page_id"] = notion_page_id
            
            # Armazenar no Qdrant
            if self.qdrant.is_connected() and result.get("embedding"):
                await self.qdrant.store_email(
                    email_id=email_id,
                    embedding=result["embedding"],
                    metadata=decision_data
                )
            
            # 8. Executar ação
            logger.info(f"[{email_id}] Executando ação: {action.get('acao')}")
            await self._execute_action(action, email, account)
            
            # 9. Notificar Telegram
            # Calcular total de reasoning tokens
            total_reasoning_tokens = (
                classification.get("reasoning_tokens", 0) +
                summary.get("reasoning_tokens", 0) +
                action.get("reasoning_tokens", 0)
            )
            
            topic_id = config.get("telegram_topic")
            logger.info(f"[{email_id}] Enviando notificação Telegram (topic_id={topic_id})...")
            message_id = await self.telegram.send_email_notification(
                email=email,
                classification=classification,
                summary=summary,
                action=action,
                topic_id=topic_id,
                reasoning_tokens=total_reasoning_tokens
            )
            result["telegram_message_id"] = message_id
            result["reasoning_tokens"] = total_reasoning_tokens
            
            # Lazy-load counter from Qdrant on first successful email
            if not self._counter_loaded and self.qdrant.is_connected():
                try:
                    self._emails_processed = await self.qdrant.get_learning_counter(account) or 0
                    self._counter_loaded = True
                except Exception:
                    pass

            # Increment counter and trigger learning
            self._emails_processed += 1
            if self.learning and self._emails_processed % self._learning_interval == 0:
                try:
                    logger.info(f"[{email_id}] Disparando ciclo de aprendizado (#{self._emails_processed})")
                    await self.learning.analyze_and_learn(account)
                    await self.qdrant.update_learning_counter(account, self._emails_processed)
                except Exception as e:
                    logger.error(f"[{email_id}] Erro no learning engine: {e}")

            result["status"] = "success"
            logger.info(f"[{email_id}] Processamento concluído")
            
            return result
            
        except Exception as e:
            logger.error(f"[{email_id}] Erro no processamento: {e}", exc_info=True)
            result["status"] = "error"
            result["error"] = str(e)
            return result
    
    async def _execute_action(
        self,
        action: Dict[str, Any],
        email: Dict[str, Any],
        account: str
    ):
        """Executa a ação decidida"""
        acao = action.get("acao", "notificar")
        email_id = email.get("id", "")
        
        if acao == "arquivar":
            await self.gog.archive_email(email_id, account)
            logger.info(f"Email {email_id} arquivado")
        
        elif acao == "criar_task":
            task = action.get("task", {})
            task["email_id"] = email_id
            await self.notion.create_task(task, account)
            logger.info(f"Task criada para email {email_id}")
        
        elif acao == "rascunho":
            draft = await self.gog.create_draft(
                to=email.get("from", ""),
                subject=f"Re: {email.get('subject', '')}",
                body=action.get("rascunho_resposta", ""),
                account=account,
                thread_id=email.get("threadId")
            )
            if draft:
                logger.info(f"Rascunho criado: {draft}")
        
        # "notificar" não precisa de ação adicional (já vai para Telegram)