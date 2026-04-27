"""Tests for LLMService prompt building (Layer C of reply policy)."""
from orchestrator.services.llm_service import LLMService


def _make_svc():
    """Build LLMService bypassing __init__ so it has prompt_builder via class default."""
    return LLMService.__new__(LLMService)


def test_action_prompt_includes_rascunho_by_default():
    """Default behaviour: rascunho option must be present in the action prompt."""
    svc = _make_svc()
    email = {"from": "user@example.com", "subject": "s", "body": "body"}
    classification = {"categoria": "trabalho", "prioridade": "Media"}
    summary = {"resumo": "r"}
    config = {}
    prompt = svc._build_action_prompt(email, classification, summary, config, {})
    assert "rascunho" in prompt.lower()
    # The 4th option should appear
    assert '"rascunho"' in prompt


def test_action_prompt_omits_rascunho_for_non_replyable():
    """When is_non_replyable=True, the action prompt must not advertise rascunho."""
    svc = _make_svc()
    email = {"from": "noreply@example.com", "subject": "s", "body": "body"}
    classification = {"categoria": "newsletter", "prioridade": "Baixa"}
    summary = {"resumo": "r"}
    config = {}
    prompt_normal = svc._build_action_prompt(
        email, classification, summary, config, {}, is_non_replyable=False,
    )
    assert "rascunho" in prompt_normal.lower()

    prompt_restricted = svc._build_action_prompt(
        email, classification, summary, config, {}, is_non_replyable=True,
    )
    # The "rascunho" option must not appear in actions list or JSON enum
    assert '"rascunho"' not in prompt_restricted
    # JSON enum must list only the 3 allowed actions
    assert "notificar/arquivar/criar_task" in prompt_restricted
    # The 4th action line must be absent
    assert '4. "rascunho"' not in prompt_restricted
    # And the user-facing instruction must mention non-replyable
    assert "NAO-RESPONDIVEL" in prompt_restricted or "nao-respondivel" in prompt_restricted.lower()
    # The JSON template must not have a `rascunho_resposta` field declaration
    # (i.e., the prompt must not ask the LLM to fill it in)
    assert '"rascunho_resposta": "texto do rascunho' not in prompt_restricted


def test_classifier_prompt_source_lists_all_valid_categories():
    """Static check: the classifier prompt source string must mention every
    category in _VALID_CATEGORIES.

    Issue 3 do PR #17: o prompt do classifier so listava 7 categorias antigas,
    mas _VALID_CATEGORIES (Etapa 2) inclui ``notificacao_automatica`` e
    ``transacional``. LLM nunca escolhia as novas -> Layer B (categoria) era
    inalcancavel quando o sender nao batia com o regex.
    """
    import inspect
    from orchestrator.services import llm_service
    from orchestrator.services.llm_validator import _VALID_CATEGORIES

    source = inspect.getsource(llm_service)
    for cat in _VALID_CATEGORIES:
        assert cat in source, (
            f"Category '{cat}' from _VALID_CATEGORIES missing from llm_service.py — "
            f"LLM will never pick it, breaking Layer B (category-based no-reply detection)."
        )
