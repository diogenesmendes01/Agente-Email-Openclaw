"""
LearningEngine - Analyzes user feedback and generates automatic classification rules.

Runs every N emails (configurable via LEARNING_INTERVAL env var).
Stores rules in Qdrant learned_rules collection.
"""

import os
import re
import logging
import hashlib
from typing import Dict, Any, List
from collections import Counter, defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

MIN_EVIDENCE = 3
MIN_CONFIDENCE = 0.7
DELETE_THRESHOLD = 0.5
MIN_WORD_LENGTH = 4

PT_STOPWORDS = {
    "para", "como", "mais", "este", "esta", "esse", "essa", "isso",
    "aqui", "onde", "qual", "quem", "porque", "quando", "muito",
    "tambem", "outro", "outra", "outros", "outras", "mesmo", "mesma",
    "todo", "toda", "todos", "todas", "nada", "cada", "algo",
    "voce", "voces", "nosso", "nossa", "dele", "dela", "deles",
    "sobre", "entre", "depois", "antes", "ainda", "desde", "apenas",
    "agora", "sempre", "nunca", "ja", "ate", "pode", "deve",
    "seria", "sido", "sendo", "estar", "estou", "estamos",
    "tinha", "tenho", "temos", "fazer", "faz", "feito",
    "bom", "boa", "bem", "meu", "minha", "seu", "sua",
    "email", "emails", "mensagem", "assunto", "favor",
    "prezado", "prezada", "prezados", "atenciosamente",
    "obrigado", "obrigada", "cordialmente",
}


class LearningEngine:
    """Analyzes feedback patterns and generates classification rules."""

    def __init__(self, qdrant, telegram=None):
        self.qdrant = qdrant
        self.telegram = telegram

    async def analyze_and_learn(self, account: str) -> List[Dict[str, Any]]:
        """Main learning cycle. Fetches corrected emails, generates rules, stores them."""
        logger.info(f"[LearningEngine] Iniciando ciclo de aprendizado para {account}")

        corrected = await self.qdrant.get_corrected_emails(account)
        if not corrected:
            logger.info("[LearningEngine] Nenhum email corrigido encontrado")
            await self._cleanup_low_confidence_rules(account)
            return []

        confirmed = await self.qdrant.get_confirmed_emails(account)

        rules = []
        rules.extend(self._generate_sender_rules(corrected, account))
        rules.extend(self._generate_domain_rules(corrected, account))
        rules.extend(self._generate_keyword_rules(corrected, confirmed, account))

        if rules:
            await self.qdrant.store_rules(rules)
            logger.info(f"[LearningEngine] {len(rules)} regras geradas/atualizadas")

        await self._cleanup_low_confidence_rules(account)

        if rules and self.telegram and self.telegram._configured:
            summary = ", ".join(f"{r['rule_type']}:{r['match']}" for r in rules[:5])
            msg = f"\U0001f9e0 Aprendi {len(rules)} regras novas:\n{summary}"
            try:
                await self.telegram._send_message(msg)
            except Exception as e:
                logger.error(f"Erro ao notificar aprendizado: {e}")

        return rules

    def _generate_sender_rules(self, corrected: List[Dict], account: str) -> List[Dict[str, Any]]:
        """Generate rules per sender email (priority and category)."""
        by_sender = defaultdict(list)
        for email in corrected:
            sender = email.get("from_email", "")
            if sender:
                by_sender[sender].append(email)

        rules = []
        for sender, emails in by_sender.items():
            # Priority rules
            direction_counts = self._count_directions(emails)
            for (orig, corr), count in direction_counts.items():
                if count >= MIN_EVIDENCE:
                    confidence = count / len(emails)
                    if confidence >= MIN_CONFIDENCE:
                        rules.append({
                            "rule_type": "sender",
                            "match": sender,
                            "account": account,
                            "action": "priority_override",
                            "value": corr,
                            "confidence": round(confidence, 2),
                            "evidence_count": count,
                            "created_at": datetime.utcnow().strftime("%Y-%m-%d"),
                        })
            # Category rules
            cat_counts = self._count_category_directions(emails)
            for (orig, corr), count in cat_counts.items():
                if count >= MIN_EVIDENCE:
                    confidence = count / len(emails)
                    if confidence >= MIN_CONFIDENCE:
                        rules.append({
                            "rule_type": "sender",
                            "match": sender,
                            "account": account,
                            "action": "category_override",
                            "value": corr,
                            "confidence": round(confidence, 2),
                            "evidence_count": count,
                            "created_at": datetime.utcnow().strftime("%Y-%m-%d"),
                        })
        return rules

    def _generate_domain_rules(self, corrected: List[Dict], account: str) -> List[Dict[str, Any]]:
        """Generate rules per sender domain (priority and category)."""
        by_domain = defaultdict(list)
        for email in corrected:
            sender = email.get("from_email", "")
            if sender and "@" in sender:
                domain = sender.split("@")[1]
                by_domain[domain].append(email)

        rules = []
        for domain, emails in by_domain.items():
            direction_counts = self._count_directions(emails)
            for (orig, corr), count in direction_counts.items():
                if count >= MIN_EVIDENCE:
                    confidence = count / len(emails)
                    if confidence >= MIN_CONFIDENCE:
                        rules.append({
                            "rule_type": "domain",
                            "match": f"@{domain}",
                            "account": account,
                            "action": "priority_override",
                            "value": corr,
                            "confidence": round(confidence, 2),
                            "evidence_count": count,
                            "created_at": datetime.utcnow().strftime("%Y-%m-%d"),
                        })
            cat_counts = self._count_category_directions(emails)
            for (orig, corr), count in cat_counts.items():
                if count >= MIN_EVIDENCE:
                    confidence = count / len(emails)
                    if confidence >= MIN_CONFIDENCE:
                        rules.append({
                            "rule_type": "domain",
                            "match": f"@{domain}",
                            "account": account,
                            "action": "category_override",
                            "value": corr,
                            "confidence": round(confidence, 2),
                            "evidence_count": count,
                            "created_at": datetime.utcnow().strftime("%Y-%m-%d"),
                        })
        return rules

    def _generate_keyword_rules(self, corrected: List[Dict], confirmed: List[Dict], account: str) -> List[Dict[str, Any]]:
        """Generate rules from subject keywords. Only words in < 20% of confirmed emails."""
        confirmed_keyword_counts = Counter()
        for email in confirmed:
            subject = email.get("subject", "")
            for word in self._extract_words(subject):
                confirmed_keyword_counts[word] += 1
        total_confirmed = max(len(confirmed), 1)

        keyword_corrections = defaultdict(list)
        for email in corrected:
            subject = email.get("subject", "")
            words = self._extract_words(subject)
            for word in words:
                keyword_corrections[word].append(email)

        rules = []
        for word, emails in keyword_corrections.items():
            if len(emails) < MIN_EVIDENCE:
                continue
            confirmed_rate = confirmed_keyword_counts.get(word, 0) / total_confirmed
            if confirmed_rate >= 0.2:
                continue
            direction_counts = self._count_directions(emails)
            for (orig, corr), count in direction_counts.items():
                if count >= MIN_EVIDENCE:
                    confidence = count / len(emails)
                    if confidence >= MIN_CONFIDENCE:
                        rules.append({
                            "rule_type": "keyword",
                            "match": word,
                            "account": account,
                            "action": "priority_override",
                            "value": corr,
                            "confidence": round(confidence, 2),
                            "evidence_count": count,
                            "created_at": datetime.utcnow().strftime("%Y-%m-%d"),
                        })
        return rules

    def _count_directions(self, emails: List[Dict]) -> Counter:
        """Count priority correction direction patterns."""
        directions = Counter()
        for e in emails:
            orig = e.get("feedback_original_priority")
            corr = e.get("feedback_corrected_priority")
            if orig and corr and orig != corr:
                directions[(orig, corr)] += 1
        return directions

    def _count_category_directions(self, emails: List[Dict]) -> Counter:
        """Count category correction direction patterns."""
        directions = Counter()
        for e in emails:
            orig = e.get("feedback_original_category")
            corr = e.get("feedback_corrected_category")
            if orig and corr and orig != corr:
                directions[(orig, corr)] += 1
        return directions

    def _extract_words(self, text: str) -> set:
        """Extract meaningful words from text, filtering stopwords."""
        words = re.findall(r'[a-z\u00e1\u00e0\u00e2\u00e3\u00e9\u00e8\u00ea\u00ed\u00ef\u00f3\u00f4\u00f5\u00fa\u00fc\u00e7]+', text.lower())
        return {
            w for w in words
            if len(w) >= MIN_WORD_LENGTH and w not in PT_STOPWORDS
        }

    async def _cleanup_low_confidence_rules(self, account: str):
        """Delete rules with confidence below threshold."""
        try:
            existing = await self.qdrant.get_learned_rules(account, min_confidence=0.0)
            to_delete = []
            for rule in existing:
                if rule.get("confidence", 0) < DELETE_THRESHOLD:
                    id_str = f"{rule['rule_type']}:{rule['match']}:{rule['account']}"
                    rule_id = hashlib.md5(id_str.encode()).hexdigest()
                    to_delete.append(rule_id)

            if to_delete:
                await self.qdrant.delete_rules(to_delete)
                logger.info(f"[LearningEngine] {len(to_delete)} regras removidas (baixa confian\u00e7a)")
        except Exception as e:
            logger.error(f"Erro ao limpar regras: {e}")
