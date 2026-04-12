"""Tests for LearningEngine - analyzes feedback and generates rules"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from orchestrator.services.learning_engine import LearningEngine


@pytest.fixture
def engine():
    qdrant = MagicMock()
    qdrant.get_corrected_emails = AsyncMock(return_value=[])
    qdrant.get_confirmed_emails = AsyncMock(return_value=[])
    qdrant.store_rules = AsyncMock(return_value=True)
    qdrant.delete_rules = AsyncMock(return_value=True)
    qdrant.get_learned_rules = AsyncMock(return_value=[])
    telegram = MagicMock()
    telegram._configured = True
    telegram._send_message = AsyncMock(return_value=123)
    return LearningEngine(qdrant, telegram)


class TestSenderRules:
    @pytest.mark.asyncio
    async def test_generates_sender_rule_from_3_corrections(self, engine):
        """3+ corrections in same direction -> create sender rule"""
        engine.qdrant.get_corrected_emails.return_value = [
            {"from_email": "joao@xyz.com", "feedback_original_priority": "Media",
             "feedback_corrected_priority": "Alta", "subject": "Contrato"},
            {"from_email": "joao@xyz.com", "feedback_original_priority": "Media",
             "feedback_corrected_priority": "Alta", "subject": "Renovacao"},
            {"from_email": "joao@xyz.com", "feedback_original_priority": "Media",
             "feedback_corrected_priority": "Alta", "subject": "Prazo"},
        ]

        rules = await engine.analyze_and_learn("test@test.com")
        sender_rules = [r for r in rules if r["rule_type"] == "sender"]
        assert len(sender_rules) >= 1
        assert sender_rules[0]["match"] == "joao@xyz.com"
        assert sender_rules[0]["value"] == "Alta"
        assert sender_rules[0]["confidence"] >= 0.7


class TestDomainRules:
    @pytest.mark.asyncio
    async def test_generates_domain_rule_from_3_corrections(self, engine):
        """3+ corrections for same domain -> create domain rule"""
        engine.qdrant.get_corrected_emails.return_value = [
            {"from_email": "a@pagar.me", "feedback_original_priority": "Baixa",
             "feedback_corrected_priority": "Alta", "subject": "Cobranca"},
            {"from_email": "b@pagar.me", "feedback_original_priority": "Baixa",
             "feedback_corrected_priority": "Alta", "subject": "Fatura"},
            {"from_email": "c@pagar.me", "feedback_original_priority": "Baixa",
             "feedback_corrected_priority": "Alta", "subject": "Pagamento"},
        ]

        rules = await engine.analyze_and_learn("test@test.com")
        domain_rules = [r for r in rules if r["rule_type"] == "domain"]
        assert len(domain_rules) >= 1
        assert domain_rules[0]["match"] == "@pagar.me"


class TestKeywordRules:
    @pytest.mark.asyncio
    async def test_generates_keyword_rule(self, engine):
        """Keywords appearing in 3+ corrected emails should generate rules"""
        engine.qdrant.get_corrected_emails.return_value = [
            {"from_email": "a@x.com", "feedback_original_priority": "Baixa",
             "feedback_corrected_priority": "Alta", "subject": "Contrato urgente"},
            {"from_email": "b@y.com", "feedback_original_priority": "Baixa",
             "feedback_corrected_priority": "Alta", "subject": "Renovacao contrato"},
            {"from_email": "c@z.com", "feedback_original_priority": "Baixa",
             "feedback_corrected_priority": "Alta", "subject": "Assinatura contrato"},
        ]

        rules = await engine.analyze_and_learn("test@test.com")
        keyword_rules = [r for r in rules if r["rule_type"] == "keyword"]
        keywords = [r["match"] for r in keyword_rules]
        assert "contrato" in keywords


class TestConfidenceThresholds:
    @pytest.mark.asyncio
    async def test_low_confidence_rules_deleted(self, engine):
        """Rules with confidence < 0.5 should be auto-deleted"""
        engine.qdrant.get_learned_rules.return_value = [
            {"rule_type": "sender", "match": "old@test.com", "confidence": 0.3,
             "account": "test@test.com"}
        ]
        engine.qdrant.get_corrected_emails.return_value = []

        await engine.analyze_and_learn("test@test.com")
        engine.qdrant.delete_rules.assert_called()


class TestNoRulesFromInsufficient:
    @pytest.mark.asyncio
    async def test_no_rule_from_2_corrections(self, engine):
        """Less than 3 corrections should NOT generate a rule"""
        engine.qdrant.get_corrected_emails.return_value = [
            {"from_email": "joao@xyz.com", "feedback_original_priority": "Media",
             "feedback_corrected_priority": "Alta", "subject": "Test 1"},
            {"from_email": "joao@xyz.com", "feedback_original_priority": "Media",
             "feedback_corrected_priority": "Alta", "subject": "Test 2"},
        ]

        rules = await engine.analyze_and_learn("test@test.com")
        sender_rules = [r for r in rules if r["rule_type"] == "sender"]
        assert len(sender_rules) == 0
