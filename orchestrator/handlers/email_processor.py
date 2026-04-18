"""
Email Processor - Orquestra o processamento de emails
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from orchestrator.services.database_service import DatabaseService
from orchestrator.services.qdrant_service import QdrantService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.gmail_service import GmailService
from orchestrator.services.telegram_service import TelegramService
from orchestrator.utils.email_parser import EmailParser, emails_match
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
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
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

            # Extract PDF attachments — robust: digital / escaneado / protegido / corrompido.
            # Rule: if we could not read it, we MUST NOT inject fabricated content.
            email["pdf_attachments"] = []
            if self.pdf_reader:
                await self._process_pdf_attachments(email_id, email, account)

            # 2.5. Buscar contexto da thread se houver
            thread_id = email.get("threadId")
            thread_context = []
            owner_already_replied = False
            if thread_id and thread_id != email_id:
                logger.info(f"[{email_id}] Buscando contexto da thread {thread_id}...")
                thread_emails = await self.gmail.get_thread(thread_id, account)
                if thread_emails:
                    # Pegar últimos emails da thread como contexto
                    thread_context = thread_emails[-5:]  # Últimos 5 emails
                    logger.info(f"[{email_id}] Thread com {len(thread_emails)} mensagens")

                    # Verifica se a última mensagem da thread foi enviada pelo dono (você)
                    # Se sim, a thread já foi respondida e não precisa gerar rascunho.
                    # Usa parsing RFC-5322 (não substring) para evitar falso-positivo em
                    # emails tipo "admin@x.com" vs "admin@xavier.com".
                    last_msg = thread_emails[-1] if thread_emails else None
                    if last_msg:
                        last_from = last_msg.get("from_email") or last_msg.get("from") or ""
                        if emails_match(last_from, account):
                            owner_already_replied = True
                            logger.info(
                                f"[{email_id}] Ultima mensagem da thread eh do dono "
                                f"({account}), thread ja respondida."
                            )
            
            # 3. Buscar contexto
            logger.info(f"[{email_id}] Buscando contexto...")
            config = await self.db.get_account_config(account)

            # Fetch account_id early (needed for playbook check and later DB ops)
            account_data = await self.db.get_account(account)
            account_id = account_data["id"] if account_data else None

            # Resolve per-account LLM model (NULL = use global default)
            model_override = account_data.get("llm_model") if account_data else None
            if model_override:
                logger.info(f"[{email_id}] Usando modelo da conta: {model_override}")

            owner_name = account_data.get("owner_name", "") if account_data else ""
            context = {
                "vips": config.get("vips", []),
                "urgency_words": config.get("urgency_words", []),
                "ignore_words": config.get("ignore_words", []),
                "projetos": config.get("projetos", []),
                "thread_context": thread_context,  # Emails anteriores da thread
                "owner_name": owner_name,
                "owner_email": account,
                "owner_already_replied": owner_already_replied,
            }

            # Fetch company profile and domain rules for LLM context
            if account_id:
                try:
                    company_profile = await self.db.get_company_profile(account_id)
                    if isinstance(company_profile, dict) and company_profile:
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
                        company_id = company_profile.get("id")
                        if company_id is not None:
                            domain_rules_raw = await self.db.get_domain_rules(company_id)
                            if isinstance(domain_rules_raw, list) and domain_rules_raw:
                                context["domain_rules"] = [
                                    {
                                        "dominio": r.get("domain", ""),
                                        "categoria": r.get("category", ""),
                                        "prioridade_minima": r.get("min_priority", ""),
                                        "acao_padrao": r.get("default_action", ""),
                                    }
                                    for r in domain_rules_raw
                                    if isinstance(r, dict)
                                ]
                except Exception as e:
                    logger.warning(f"[{email_id}] Error fetching company profile/domain rules: {e}")

                # Per-account prompt customization (Layer 3)
                try:
                    apc = await self.db.get_account_prompt_config(account_id)
                    if isinstance(apc, dict) and apc:
                        context["account_prompt_config"] = apc
                except Exception as e:
                    logger.warning(f"[{email_id}] Error fetching account_prompt_config: {e}")
            
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
            # Metadata is returned per-call so concurrent emails don't collide
            # on shared state in the LLMService singleton.
            validation_metas: Dict[str, Any] = {}
            logger.info(f"[{email_id}] Classificando...")
            classification, classification_meta = await self.llm.classify_email(
                email, context, model_override=model_override
            )
            validation_metas["classification"] = classification_meta
            result["classification"] = classification
            
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
                                    logger.info(f"[{email_id}] Auto-responded via playbook #{playbook_match['playbook_id']}")
                                else:
                                    logger.warning(f"[{email_id}] send_reply returned False — not marking as auto-responded")
                except Exception as e:
                    logger.warning(f"[{email_id}] Playbook check error: {e}")

            # 5. Resumir
            logger.info(f"[{email_id}] Resumindo...")
            summary, summary_meta = await self.llm.summarize_email(
                email, classification, context, model_override=model_override
            )
            validation_metas["summary"] = summary_meta
            result["summary"] = summary

            # 6. Decidir ação
            logger.info(f"[{email_id}] Decidindo ação...")
            action, action_meta = await self.llm.decide_action(
                email, classification, summary, config, context, model_override=model_override
            )
            validation_metas["action"] = action_meta
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
            
            # account_data and account_id already fetched above
            if account_id:
                decision_data["account_id"] = account_id
            decision_id = await self.db.log_decision(decision_data)
            result["decision_id"] = decision_id
            
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
            # Calcular totais de tokens e custo das 3 chamadas LLM
            total_prompt_tokens = (
                classification.get("prompt_tokens", 0) +
                summary.get("prompt_tokens", 0) +
                action.get("prompt_tokens", 0)
            )
            total_completion_tokens = (
                classification.get("completion_tokens", 0) +
                summary.get("completion_tokens", 0) +
                action.get("completion_tokens", 0)
            )
            total_tokens = (
                classification.get("total_tokens", 0) +
                summary.get("total_tokens", 0) +
                action.get("total_tokens", 0)
            )
            total_cost_usd = (
                classification.get("cost_usd", 0.0) +
                summary.get("cost_usd", 0.0) +
                action.get("cost_usd", 0.0)
            )
            # Legacy field (keep for backwards compat)
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
                total_tokens=total_tokens,
                cost_usd=total_cost_usd,
                account=account,
                auto_responded=auto_responded,
            )
            result["telegram_message_id"] = message_id
            result["total_tokens"] = total_tokens
            result["cost_usd"] = total_cost_usd
            
            # Lazy-load counter from database on first successful email
            if not self._counter_loaded and account_id:
                try:
                    counter_value = await self.db.get_learning_counter(account_id)
                    if isinstance(counter_value, int) and counter_value >= 0:
                        self._emails_processed = counter_value
                    else:
                        self._emails_processed = 0
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

            # Log LLM quality (validation telemetry) — best-effort, non-blocking.
            # Metadata is read from the per-email `validation_metas` dict, NOT
            # from shared state on the LLMService singleton — that prevents
            # concurrent emails from stomping each other's telemetry.
            try:
                for kind, meta in validation_metas.items():
                    if meta is None:
                        continue
                    await self.db.log_llm_quality(
                        account_id=account_id,
                        email_id=email_id,
                        kind=kind,
                        model=meta.model,
                        retries=meta.retries,
                        flags=meta.flags,
                        json_parse_failed=meta.json_parse_failed,
                        schema_valid=meta.schema_valid,
                        fallback_used=meta.fallback_used,
                        prompt_tokens_successful=meta.prompt_tokens_successful,
                        completion_tokens_successful=meta.completion_tokens_successful,
                        prompt_tokens_total=meta.prompt_tokens_total,
                        completion_tokens_total=meta.completion_tokens_total,
                        cost_total_usd=meta.cost_total_usd,
                    )
            except Exception as qe:
                logger.warning(f"[{email_id}] llm_quality_log error: {qe}")

            # Record metrics
            if self.metrics and account_id:
                await self.metrics.record(
                    event="email_processed",
                    service="pipeline",
                    account_id=account_id,
                    tokens_used=total_tokens,
                    cost_usd=total_cost_usd,
                    success=True,
                )

            result["status"] = "success"
            logger.info(f"[{email_id}] Processamento concluído")

            return result

        except Exception as e:
            logger.error(f"[{email_id}] Erro no processamento: {e}", exc_info=True)
            result["status"] = "error"
            result["error"] = str(e)

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

    async def _process_pdf_attachments(
        self, email_id: str, email: Dict[str, Any], account: str,
    ):
        """Extract each PDF attachment using the robust reader.

        Appends successful extractions to ``body_clean`` and flags unread PDFs
        with a very explicit marker so the LLM never hallucinates content
        from the filename alone.
        """
        from orchestrator.utils.pdf_reader import extract_pdf_attachment
        from orchestrator.utils import pdf_ratelimit
        from orchestrator.utils.crypto import decrypt, is_configured as crypto_configured

        email.setdefault("pdf_attachments", [])
        attachments = [a for a in email.get("attachments", []) if a.get("mimeType") == "application/pdf"]
        if not attachments:
            return

        # Resolve account_id & sender for per-account password lookup
        account_data = await self.db.get_account(account)
        account_id = account_data["id"] if account_data else None
        sender_email = (email.get("from_email") or email.get("from") or "").lower()
        body_for_hints = email.get("body_clean", "") or ""

        # Prefetch cadastradas passwords (decrypted) and inferred candidates.
        cadastradas: list = []
        inferred: list = []
        if account_id and crypto_configured():
            try:
                rows = await self.db.get_pdf_passwords_for_sender(account_id, sender_email)
                for r in rows:
                    pattern = r["sender_pattern"]
                    # Skip DB-level lockouts and in-memory rate-limit lockouts
                    if r.get("locked_until"):
                        from datetime import datetime, timezone
                        if r["locked_until"] > datetime.now(timezone.utc):
                            continue
                    if pdf_ratelimit.is_locked(account_id, pattern):
                        continue
                    pwd = decrypt(r["password_encrypted"])
                    if pwd:
                        cadastradas.append({"id": r["id"], "password": pwd, "pattern": pattern})
            except Exception as e:
                logger.warning(f"[{email_id}] Failed to fetch pdf_passwords: {e}")

            try:
                from orchestrator.utils.pdf_reader import _inferred_passwords_from_body
                docs_row = await self.db.get_account_documents(account_id)
                if docs_row:
                    docs_plain = {
                        "cpf": decrypt(docs_row.get("cpf_encrypted")) if docs_row.get("cpf_encrypted") else None,
                        "cnpj": decrypt(docs_row.get("cnpj_encrypted")) if docs_row.get("cnpj_encrypted") else None,
                        "birthdate": decrypt(docs_row.get("birthdate_encrypted")) if docs_row.get("birthdate_encrypted") else None,
                    }
                    inferred = _inferred_passwords_from_body(body_for_hints, docs_plain)
            except Exception as e:
                logger.warning(f"[{email_id}] Failed to build inferred passwords: {e}")

        for attachment in attachments:
            filename = attachment.get("filename", "arquivo.pdf")
            logger.info(f"[{email_id}] Processing PDF: {filename}")
            pdf_bytes = await self.gmail.get_attachment(
                email_id, attachment["attachmentId"], account,
            )
            if not pdf_bytes:
                email["pdf_attachments"].append({
                    "filename": filename, "leitura_sucesso": False,
                    "motivo_falha": "download_falhou", "tipo": None,
                })
                email["body_clean"] += (
                    f"\n\n--- ANEXO PDF NÃO LIDO: {filename} "
                    f"(MOTIVO: falha ao baixar o anexo do Gmail) ---"
                )
                continue

            try:
                result = await extract_pdf_attachment(
                    pdf_bytes, filename,
                    reader=self.pdf_reader,
                    passwords_cadastradas=cadastradas,
                    inferred_candidates=inferred,
                )
            except Exception as e:
                logger.error(f"[{email_id}] extract_pdf_attachment crashed: {e}", exc_info=True)
                result = {
                    "filename": filename, "tipo": "corrompido",
                    "texto": None, "campos": {}, "leitura_sucesso": False,
                    "motivo_falha": "corrompido", "senha_usada_hash": None,
                }

            email["pdf_attachments"].append(result)

            if result["leitura_sucesso"] and result.get("texto"):
                email["body_clean"] += (
                    f"\n\n--- ANEXO PDF: {filename} (tipo: {result['tipo']}) ---\n"
                    f"{result['texto']}"
                )
                # Update usage counter for cadastrada password hit
                if result.get("matched_password_id"):
                    try:
                        await self.db.touch_pdf_password(result["matched_password_id"])
                    except Exception:
                        pass
                # Reset rate-limit counters on success — ONLY for the pattern that worked
                if account_id and result.get("pattern_used"):
                    pdf_ratelimit.record_success(account_id, result["pattern_used"])
            else:
                motivo = result.get("motivo_falha") or "desconhecido"
                motivo_human = {
                    "sem_senha_cadastrada": "protegido por senha — nenhuma senha está cadastrada para este remetente",
                    "senha_incorreta": "protegido por senha — senhas cadastradas para este remetente não funcionaram",
                    "senha_ausente": "protegido por senha — nenhuma senha cadastrada foi capaz de abrir",  # legacy alias
                    "ocr_falhou": "escaneado — OCR não extraiu texto",
                    "corrompido": "arquivo corrompido ou formato inválido",
                    "download_falhou": "falha ao baixar o anexo do Gmail",
                }.get(motivo, motivo)
                email["body_clean"] += (
                    f"\n\n--- ANEXO PDF NÃO LIDO: {filename} "
                    f"(MOTIVO: {motivo_human}) ---"
                )
                # Rate-limit: count failure ONLY against patterns actually attempted on this PDF.
                # If no pattern matched this sender (sem_senha_cadastrada), there's nothing to
                # rate-limit — don't touch unrelated patterns registered on the account.
                if account_id and result.get("tipo") == "protegido":
                    for pattern in (result.get("patterns_attempted") or []):
                        activated = pdf_ratelimit.record_failure(account_id, pattern)
                        if activated:
                            try:
                                await self.db.lock_pdf_pattern(
                                    account_id, pattern, minutes=30,
                                )
                            except Exception:
                                pass
