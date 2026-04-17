"""Tests for enriched LLM prompts with company context, sender profile, learned rules"""
import pytest
from unittest.mock import patch
from orchestrator.services.llm_service import LLMService


@pytest.fixture
def llm():
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
        svc = LLMService()
        return svc


class TestClassifierPromptEnrichment:
    def test_includes_company_context(self, llm):
        email = {"from": "joao@xyz.com", "to": "me@test.com", "subject": "Test", "body": "Hello"}
        context = {
            "vips": [], "urgency_words": [], "ignore_words": [],
            "similar_emails": [], "thread_context": [],
            "company_profile": {
                "nome": "Mendes Consultoria",
                "setor": "Tecnologia",
                "tom": "profissional",
            },
            "sender_profile": {},
            "learned_rules": [],
            "domain_rules": [],
        }
        prompt = llm._build_classifier_prompt(email, context)
        assert "Mendes Consultoria" in prompt
        assert "Tecnologia" in prompt

    def test_includes_learned_rules(self, llm):
        email = {"from": "joao@xyz.com", "to": "me@test.com", "subject": "Test", "body": "Hello"}
        context = {
            "vips": [], "urgency_words": [], "ignore_words": [],
            "similar_emails": [], "thread_context": [],
            "company_profile": {},
            "sender_profile": {},
            "learned_rules": [
                {"rule_type": "sender", "match": "joao@xyz.com",
                 "action": "priority_override", "value": "Alta", "confidence": 0.9}
            ],
            "domain_rules": [],
        }
        prompt = llm._build_classifier_prompt(email, context)
        assert "joao@xyz.com" in prompt
        assert "Alta" in prompt

    def test_includes_sender_profile(self, llm):
        email = {"from": "joao@xyz.com", "to": "me@test.com", "subject": "Test", "body": "Hello"}
        context = {
            "vips": [], "urgency_words": [], "ignore_words": [],
            "similar_emails": [], "thread_context": [],
            "company_profile": {},
            "sender_profile": {
                "count": 15, "important_rate": 0.8,
                "correction_patterns": [{"from": "Media", "to": "Alta", "count": 3}]
            },
            "learned_rules": [],
            "domain_rules": [],
        }
        prompt = llm._build_classifier_prompt(email, context)
        assert "15" in prompt
        assert "Media" in prompt and "Alta" in prompt

    def test_includes_similar_with_feedback(self, llm):
        email = {"from": "a@b.com", "to": "me@test.com", "subject": "Test", "body": "Hello"}
        context = {
            "vips": [], "urgency_words": [], "ignore_words": [],
            "similar_emails": [
                {"payload": {
                    "subject": "Contrato antigo", "from_email": "joao@xyz.com",
                    "feedback": "corrected",
                    "feedback_original_priority": "Media",
                    "feedback_corrected_priority": "Alta",
                }}
            ],
            "thread_context": [],
            "company_profile": {},
            "sender_profile": {},
            "learned_rules": [],
            "domain_rules": [],
        }
        prompt = llm._build_classifier_prompt(email, context)
        assert "corrigiu" in prompt.lower() or "corrected" in prompt.lower() or "corr" in prompt.lower() or "Media" in prompt


class TestActionPromptEnrichment:
    def test_includes_company_tone_and_signature(self, llm):
        email = {"from": "joao@xyz.com", "subject": "Test", "body": "Hello"}
        classification = {"categoria": "cliente", "prioridade": "Alta"}
        summary = {"resumo": "Test summary"}
        config = {"auto_reply": False}
        context = {
            "company_profile": {
                "tom": "formal",
                "assinatura": "Att, Diogenes\nMendes Consultoria",
                "idioma": "pt-BR",
            },
            "sender_profile": {"is_client": True, "client_name": "XYZ Corp"},
        }
        prompt = llm._build_action_prompt(email, classification, summary, config, context)
        assert "formal" in prompt
        assert "Mendes Consultoria" in prompt
        assert "XYZ Corp" in prompt


class TestPromptSizeManagement:
    def test_long_prompt_gets_truncated(self, llm):
        """Prompt over 6000 tokens should be truncated"""
        email = {"from": "a@b.com", "to": "me@test.com", "subject": "Test", "body": "x" * 30000}
        context = {
            "vips": [], "urgency_words": [], "ignore_words": [],
            "similar_emails": [], "thread_context": [],
            "company_profile": {},
            "sender_profile": {},
            "learned_rules": [],
            "domain_rules": [],
        }
        prompt = llm._build_classifier_prompt(email, context)
        estimated_tokens = len(prompt) // 4
        assert estimated_tokens <= 7000  # Some tolerance

    def test_action_prompt_preserves_instructions_when_long(self, llm):
        """Long email body must not cause _manage_prompt_size to strip
        CLASSIFICACAO / RESUMO / JSON instructions (including acao_usuario).
        Body is truncated at 2000 chars before being inserted into the prompt."""
        email = {
            "from": "a@b.com",
            "to": "me@test.com",
            "subject": "Longo",
            "body_clean": "L" * 50000,  # very long body
        }
        classification = {"prioridade": "Alta", "importante": True, "categoria": "cliente"}
        summary = {"resumo": "resumo breve"}
        config = {"auto_reply": False}
        context = {
            "company_profile": {"nome": "Acme", "tom": "profissional", "idioma": "pt-BR"},
            "sender_profile": {},
        }
        prompt = llm._build_action_prompt(email, classification, summary, config, context)

        # Body was pre-truncated to 2000 chars + marker
        assert "[...corpo truncado...]" in prompt
        assert "L" * 2000 in prompt
        assert "L" * 2001 not in prompt

        # Critical structural blocks must survive
        assert "CLASSIFICACAO:" in prompt
        assert "RESUMO:" in prompt
        assert "Responda em JSON" in prompt
        assert "acao_usuario" in prompt
        assert "rascunho_resposta" in prompt
        assert "ACOES POSSIVEIS" in prompt

    def test_short_prompt_unchanged(self, llm):
        """Prompt under 6000 tokens should pass through unchanged"""
        email = {"from": "a@b.com", "to": "me@test.com", "subject": "Test", "body": "Hello world"}
        context = {
            "vips": [], "urgency_words": [], "ignore_words": [],
            "similar_emails": [], "thread_context": [],
            "company_profile": {},
            "sender_profile": {},
            "learned_rules": [],
            "domain_rules": [],
        }
        prompt = llm._build_classifier_prompt(email, context)
        assert "Hello world" in prompt
