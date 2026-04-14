"""
Email Processor - Orquestra o processamento de emails
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from orchestrator.services.database_service import DatabaseService
from orchestrator.services.qdrant_service import QdrantService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.gmail_service import GmailService
from orchestrator.services.telegram_service import TelegramService
from orchestrator.utils.email_parser import EmailParser
from orchestrator.utils.text_cleaner import TextCleaner
from orchestrator.utils.pdf_reader import PdfReader
from orchestrator.services.learning_engine import LearningEngine

logger = logging.getLogger(__name__)


class EmailProcessor:
    """Processador principal de emails"""

    def __init__(
        self,
        db: DatabaseService,
        qdrant: QdrantService,
        llm: LLMService,
        gmail: GmailService,
        telegram: TelegramService,
        learning: LearningEngine = None,
        pdf_reader: PdfReader = None,
        metrics=None,
        job_queue=None,
        playbook_service=None,
    ):
        self.db = db
        self.qdrant = qdrant
        self.llm = llm
        self.gmail = gmail
        self.telegram = telegram
        self.learning = learning
        self.pdf_reader = pdf_reader
        self.metrics = metrics
        self.job_queue = job_queue
        self.playbook_service = playbook_service
        self.parser = EmailParser()
        self.cleaner = TextCleaner()
        self._learning_interval = int(os.getenv("LEARNING_INTERVAL", "50"))
        self._emails_processed = 0
        self._counter_loaded = False
    
    async def process_email(self, email_id: str, account: str, _is_retry: bool = False) -> Dict[str, Any]:
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
        # Tracks whether irreversible external side effects have fired
        # (playbook send_reply, _execute_action). Once True, releasing the
        # claim and retrying would duplicate those effects.
        _side_effects_executed = False

        try:
            # 1. Fetch email
            logger.info(f"[{email_id}] Buscando email...")
            raw_email = await self.gmail.get_email(email_id, account)
            
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

            # Extract PDF attachment text
            if self.pdf_reader:
                for attachment in email.get("attachments", []):
                    if attachment.get("mimeType") == "application/pdf":
                        logger.info(f"[{email_id}] Extracting PDF: {attachment['filename']}")
                        pdf_bytes = await self.gmail.get_attachment(
                            email_id, attachment["attachmentId"], account
                        )
                        if pdf_bytes:
                            pdf_text = await self.pdf_reader.extract(pdf_bytes)
                            if pdf_text:
                                email["body_clean"] += f"\n\n--- ANEXO PDF: {attachment['filename']} ---\n{pdf_text}"

            # 2.5. Buscar contexto da thread se houver
            thread_id = email.get("threadId")
            thread_context = []
            if thread_id and thread_id != email_id:
                logger.info(f"[{email_id}] Buscando contexto da thread {thread_id}...")
                thread_emails = await self.gmail.get_thread(thread_id, account)
                if thread_emails:
                    # Pegar últimos emails da thread como contexto
                    thread_context = thread_emails[-3:]  # Últimos 3 emails
                    logger.info(f"[{email_id}] Thread com {len(thread_emails)} mensagens")
            
            # 3. Buscar contexto
            logger.info(f"[{email_id}] Buscando contexto...")
            config = await self.db.get_account_config(account)

            # Fetch account_id early (needed for playbook check and later DB ops)
            account_data = await self.db.get_account(account)
            account_id = account_data["id"] if account_data else None

            # Atomic claim: INSERT a skeleton row into decisions.
            # The UNIQUE(account_id, email_id) constraint means only one worker
            # wins. Losers get None and bail out — no playbook, no actions, nothing.
            decision_id = None
            if account_id:
                decision_id = await self.db.claim_email(account_id, email_id)
                if decision_id is None:
                    logger.info(f"[{email_id}] Already claimed by another worker — skipping entire pipeline")
                    result["status"] = "duplicate"
                    return result
            result["decision_id"] = decision_id

            context = {
                "vips": config.get("vips", []),
                "urgency_words": config.get("urgency_words", []),
                "ignore_words": config.get("ignore_words", []),
                "projetos": config.get("projetos", []),
                "thread_context": thread_context,  # Emails anteriores da thread
            }

            # Fetch company profile and domain rules for LLM context
            if account_id:
                try:
                    company_profile = await self.db.get_company_profile(account_id)
                    if company_profile:
                        # Map DB field names to what LLM prompts expect
                        context["company_profile"] = {
                            "nome": company_profile.get("company_name", ""),
                            "cnpj": company_profile.get("cnpj", ""),
                            "tom": company_profile.get("tone", "profissional"),
                            "assinatura": company_profile.get("signature", ""),
                            "idioma": company_profile.get("language", "pt-BR"),
                            "whatsapp_url": company_profile.get("whatsapp_url", ""),
                        }
                        # Domain rules need company_id from the profile
                        domain_rules_raw = await self.db.get_domain_rules(company_profile["id"])
                        if domain_rules_raw:
                            context["domain_rules"] = [
                                {
                                    "dominio": r.get("domain", ""),
                                    "categoria": r.get("category", ""),
                                    "prioridade_minima": r.get("min_priority", ""),
                                    "acao_padrao": r.get("default_action", ""),
                                }
                                for r in domain_rules_raw
                            ]
                except Exception as e:
                    logger.warning(f"[{email_id}] Error fetching company profile/domain rules: {e}")
            
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

            # Fetch sender profile from Qdrant
            if self.qdrant.is_connected():
                try:
                    from_email = email.get("from_email", "") or email.get("from", "")
                    sender_profile = await self.qdrant.get_sender_profile(from_email, account)
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
            
            # Se classificado como não importante com alta confiança, pular processamento
            if not classification.get("importante") and classification.get("confianca", 0) >= 0.8:
                logger.info(f"[{email_id}] Email não importante, pulando...")
                # Fill in the claimed decision row so it's not a ghost skeleton
                if decision_id:
                    await self.db.update_decision(decision_id, {
                        "subject": email.get("subject", ""),
                        "from": email.get("from", ""),
                        "classificacao": classification.get("categoria", "outro"),
                        "prioridade": classification.get("prioridade", "Baixa"),
                        "categoria": classification.get("categoria", "outro"),
                        "acao": "ignorar",
                        "resumo": "Email não importante — processamento pulado",
                        "reasoning_tokens": classification.get("reasoning_tokens", 0),
                    })
                result["status"] = "skipped"
                result["reason"] = "Email não importante"
                return result

            # 4.5. Check playbooks (if configured)
            auto_responded = False
            if self.playbook_service and account_id:
                try:
                    playbook_match = await self.playbook_service.match(
                        account_id=account_id,
                        email_body=email.get("body_clean", ""),
                        email_subject=email.get("subject", ""),
                    )
                    if playbook_match:
                        result["playbook_matched"] = True
                        result["playbook_id"] = playbook_match["playbook_id"]
                        if playbook_match.get("auto_respond"):
                            # Generate and send auto-response
                            from_name = email.get("from_name", "") or email.get("from", "")
                            contact_name = from_name.split("<")[0].strip().strip('"') if "<" in from_name else from_name
                            response_text = await self.playbook_service.generate_response(
                                template=playbook_match["template"],
                                company=playbook_match["company"],
                                contact_name=contact_name,
                                email_body=email.get("body_clean", ""),
                            )
                            if response_text:
                                to_email = email.get("from_email", "") or email.get("from", "")
                                sent = await self.gmail.send_reply(
                                    email_id, response_text, account,
                                    to=to_email,
                                )
                                if sent is not False:
                                    auto_responded = True
                                    _side_effects_executed = True
                                    logger.info(f"[{email_id}] Auto-responded via playbook #{playbook_match['playbook_id']}")
                                else:
                                    logger.warning(f"[{email_id}] send_reply returned False — not marking as auto-responded")
                except Exception as e:
                    logger.warning(f"[{email_id}] Playbook check error: {e}")

            # 5. Resumir
            logger.info(f"[{email_id}] Resumindo...")
            summary = await self.llm.summarize_email(email, classification, context)
            result["summary"] = summary
            
            # 6. Decidir ação
            logger.info(f"[{email_id}] Decidindo ação...")
            action = await self.llm.decide_action(email, classification, summary, config, context)
            result["action"] = action
            
            # 7. Persistir decisão (update the skeleton row claimed earlier)
            decision_data = {
                "subject": email.get("subject", ""),
                "from": email.get("from", ""),
                "classificacao": classification.get("categoria", "outro"),
                "prioridade": classification.get("prioridade", "Média"),
                "categoria": classification.get("categoria", "outro"),
                "acao": action.get("acao", "notificar"),
                "resumo": summary.get("resumo", ""),
                "reasoning_tokens": (
                    classification.get("reasoning_tokens", 0) +
                    summary.get("reasoning_tokens", 0) +
                    action.get("reasoning_tokens", 0)
                ),
            }
            if decision_id:
                await self.db.update_decision(decision_id, decision_data)

            # Armazenar no Qdrant
            if self.qdrant.is_connected() and result.get("embedding"):
                await self.qdrant.store_email(
                    email_id=email_id,
                    embedding=result["embedding"],
                    metadata=decision_data
                )

            # 8. Executar ação
            logger.info(f"[{email_id}] Executando ação: {action.get('acao')}")
            _side_effects_executed = True
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
                reasoning_tokens=total_reasoning_tokens,
                account=account,
                auto_responded=auto_responded,
            )
            result["telegram_message_id"] = message_id
            result["reasoning_tokens"] = total_reasoning_tokens
            
            # Lazy-load counter from database on first successful email
            if not self._counter_loaded and account_id:
                try:
                    self._emails_processed = await self.db.get_learning_counter(account_id)
                    self._counter_loaded = True
                except Exception:
                    pass

            # Increment counter and trigger learning
            self._emails_processed += 1
            if self.learning and self._emails_processed % self._learning_interval == 0:
                try:
                    logger.info(f"[{email_id}] Disparando ciclo de aprendizado (#{self._emails_processed})")
                    await self.learning.analyze_and_learn(account)
                    if account_id:
                        await self.db.update_learning_counter(account_id, self._emails_processed)
                except Exception as e:
                    logger.error(f"[{email_id}] Erro no learning engine: {e}")

            # Record metrics
            if self.metrics and account_id:
                await self.metrics.record(
                    event="email_processed",
                    service="pipeline",
                    account_id=account_id,
                    tokens_used=total_reasoning_tokens,
                    success=True,
                )

            result["status"] = "success"
            logger.info(f"[{email_id}] Processamento concluído")

            return result

        except Exception as e:
            logger.error(f"[{email_id}] Erro no processamento: {e}", exc_info=True)
            result["status"] = "error"
            result["error"] = str(e)

            decision_id = result.get("decision_id")

            if _side_effects_executed and decision_id:
                # Side effects already fired (send_reply, archive, create_task, etc.).
                # Releasing the claim and retrying would DUPLICATE those effects.
                # Keep the claim and finalize the decision row with whatever data we have.
                try:
                    await self.db.update_decision(decision_id, {
                        "subject": result.get("classification", {}).get("subject", ""),
                        "from": "",
                        "classificacao": result.get("classification", {}).get("categoria", ""),
                        "prioridade": result.get("classification", {}).get("prioridade", ""),
                        "categoria": result.get("classification", {}).get("categoria", ""),
                        "acao": result.get("action", {}).get("acao", "erro_parcial"),
                        "resumo": f"Processamento parcial — erro: {str(e)[:200]}",
                        "reasoning_tokens": 0,
                    })
                    logger.warning(f"[{email_id}] Side effects already executed — keeping claim, NOT retrying")
                except Exception as upd_err:
                    logger.error(f"[{email_id}] Failed to finalize partial decision: {upd_err}")
            elif decision_id:
                # No side effects yet — safe to release claim and retry.
                try:
                    await self.db.release_claim(decision_id)
                    logger.info(f"[{email_id}] Released claim (decision #{decision_id}) for retry")
                except Exception as rel_err:
                    logger.error(f"[{email_id}] Failed to release claim: {rel_err}")

                # Enqueue for retry (only on first failure, not during retry worker reprocessing)
                if self.job_queue and not _is_retry:
                    try:
                        acct = await self.db.get_account(account) if account else None
                        acct_id = acct["id"] if acct else None
                        await self.job_queue.enqueue(
                            job_type="process_email",
                            payload={"email_id": email_id, "account": account},
                            account_id=acct_id,
                        )
                    except Exception as enq_err:
                        logger.error(f"[{email_id}] Failed to enqueue retry: {enq_err}")

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
            await self.gmail.archive_email(email_id, account)
            logger.info(f"Email {email_id} arquivado")
        
        elif acao == "criar_task":
            task = action.get("task", {})
            account_data = await self.db.get_account(account)
            if account_data:
                await self.db.create_task(
                    account_id=account_data["id"],
                    title=task.get("titulo", f"Task from email {email_id}"),
                    priority=task.get("prioridade", "Média"),
                    email_id=email_id,
                )
            logger.info(f"Task criada para email {email_id}")
        
        elif acao == "rascunho":
            draft = await self.gmail.create_draft(
                to=email.get("from", ""),
                subject=f"Re: {email.get('subject', '')}",
                body=action.get("rascunho_resposta", ""),
                account=account,
                thread_id=email.get("threadId")
            )
            if draft:
                logger.info(f"Rascunho criado: {draft}")
        
        # "notificar" não precisa de ação adicional (já vai para Telegram)