"""Tests for post-LLM output validation (PR 4)."""

import json
import pytest

from orchestrator.services.llm_validator import (
    ClassificationOut,
    SummaryOut,
    ActionOut,
    semantic_validate,
    validate_and_retry,
    _extract_json,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_classification_schema_valid():
    m = ClassificationOut.model_validate({
        "importante": True,
        "prioridade": "Alta",
        "categoria": "financeiro",
        "confianca": 0.9,
        "razao": "cobranca importante",
        "entidades": {"valor": "R$ 100,00"},
    })
    assert m.prioridade == "Alta"
    assert m.categoria == "financeiro"
    assert m.confianca == 0.9


def test_classification_invalid_categoria_clamped_to_outro():
    m = ClassificationOut.model_validate({
        "importante": True,
        "prioridade": "Media",
        "categoria": "algo_inventado",
        "confianca": 0.5,
        "razao": "x",
    })
    assert m.categoria == "outro"


def test_classification_confianca_clamp():
    assert ClassificationOut.model_validate({"confianca": -1}).confianca == 0.0
    # 85 is treated as 85% (0.85) by the normalizer (>1 and <=100)
    assert ClassificationOut.model_validate({"confianca": 85}).confianca == 0.85
    assert ClassificationOut.model_validate({"confianca": 9999}).confianca == 1.0
    assert ClassificationOut.model_validate({"confianca": "abc"}).confianca == 0.5


def test_classification_priority_normalizes_media_with_accent():
    m = ClassificationOut.model_validate({"prioridade": "Média"})
    assert m.prioridade == "Media"


def test_action_schema_accepts_acao_usuario_and_extras():
    m = ActionOut.model_validate({
        "acao": "rascunho",
        "justificativa": "cliente pediu resposta",
        "rascunho_resposta": "Ola, obrigado pelo contato...",
        "acao_usuario": "Responder ate 20/01/2026",
        "extra_field": "preserved",
    })
    assert m.acao == "rascunho"
    assert m.acao_usuario == "Responder ate 20/01/2026"
    dumped = m.model_dump()
    assert dumped.get("extra_field") == "preserved"


def test_action_invalid_acao_defaults_to_notificar():
    m = ActionOut.model_validate({"acao": "explodir_tudo"})
    assert m.acao == "notificar"


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def test_extract_json_direct():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_preamble():
    assert _extract_json('Here is the JSON: {"a": 1, "b": "x"} done.') == {"a": 1, "b": "x"}


def test_extract_json_garbage_returns_none():
    assert _extract_json("no json here") is None
    assert _extract_json("") is None


# ---------------------------------------------------------------------------
# Semantic validation
# ---------------------------------------------------------------------------

def test_semantic_validate_detects_masked_chavoes():
    email = {"subject": "Aviso", "body": "Prezado cliente, favor retornar contato."}
    summary = {"resumo": "Email importante que requer sua atenção"}
    flags = semantic_validate(email, summary=summary)
    assert "resumo_generico" in flags


def test_semantic_chavao_with_number_does_not_flag():
    email = {"subject": "Aviso", "body": "Prezado, contate-nos."}
    summary = {"resumo": "Email importante que requer atenção: R$ 500 devidos"}
    flags = semantic_validate(email, summary=summary)
    # Has a number -> chavão check ignored
    assert "resumo_generico" not in flags


def test_semantic_rascunho_without_value_when_email_has_value():
    email = {"subject": "cobranca", "body": "Voce tem debito de R$ 826,92 em aberto."}
    action = {"rascunho_resposta": "Ola, vou verificar em breve e retorno com detalhes adicionais."}
    flags = semantic_validate(email, action=action)
    assert "rascunho_sem_valor" in flags


def test_semantic_rascunho_with_correct_value_passes():
    email = {"subject": "cobranca", "body": "Voce tem debito de R$ 826,92 em aberto."}
    action = {"rascunho_resposta": "Vou quitar o valor de R$ 826,92 ate amanha conforme combinado."}
    flags = semantic_validate(email, action=action)
    assert "rascunho_sem_valor" not in flags
    assert "rascunho_inventado" not in flags


def test_semantic_rascunho_invented_value_flagged():
    email = {"subject": "cobranca", "body": "Voce tem debito em aberto, favor negociar."}
    # Email has NO money; draft invents one
    action = {"rascunho_resposta": "Pagarei R$ 999,99 conforme combinado, obrigado pela atencao."}
    flags = semantic_validate(email, action=action)
    assert "rascunho_inventado" in flags


def test_semantic_rascunho_invented_date_flagged():
    email = {"subject": "reuniao", "body": "Podemos marcar uma reuniao na proxima semana?"}
    action = {"rascunho_resposta": "Confirmo nossa reuniao para 05/02/2026 as 14h como planejado."}
    flags = semantic_validate(email, action=action)
    assert "rascunho_inventado" in flags


def test_semantic_rascunho_muito_curto():
    email = {"subject": "oi", "body": "tudo bem?"}
    action = {"rascunho_resposta": "Ok, obrigado."}
    classification = {"categoria": "trabalho"}
    flags = semantic_validate(email, classification=classification, action=action)
    assert "rascunho_muito_curto" in flags


def test_semantic_rascunho_curto_skipped_for_newsletter():
    email = {"subject": "oi", "body": "newsletter"}
    action = {"rascunho_resposta": "Ok."}
    classification = {"categoria": "newsletter"}
    flags = semantic_validate(email, classification=classification, action=action)
    assert "rascunho_muito_curto" not in flags


# ---------------------------------------------------------------------------
# Pipeline (validate_and_retry)
# ---------------------------------------------------------------------------

def _make_call_fn(responses):
    """Build an async call_llm_fn that returns the sequence of raw responses."""
    idx = {"i": 0}

    async def _call(prompt):
        i = idx["i"]
        idx["i"] += 1
        if i >= len(responses):
            return responses[-1]
        return responses[i]

    _call.calls = idx  # expose for assertions
    return _call


def _raw(content, **extra):
    base = {
        "content": content,
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "reasoning_tokens": 0,
        "cost_usd": 0.001,
        "model_used": "test/model",
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_pipeline_happy_path_classification():
    email = {"subject": "cliente", "body": "projeto X"}
    good = json.dumps({
        "importante": True, "prioridade": "Alta", "categoria": "cliente",
        "confianca": 0.9, "razao": "cliente VIP",
    })
    call = _make_call_fn([_raw(good)])
    result, meta = await validate_and_retry("classification", "prompt", call, email)
    assert result["categoria"] == "cliente"
    assert meta.retries == 0
    assert meta.schema_valid is True
    assert not meta.fallback_used
    assert call.calls["i"] == 1


@pytest.mark.asyncio
async def test_pipeline_summary_generic_triggers_retry():
    email = {"subject": "cobranca", "body": "Favor verificar"}
    bad = json.dumps({"resumo": "email importante que requer sua atenção"})
    good = json.dumps({"resumo": "Aviso de cobranca da empresa X"})
    call = _make_call_fn([_raw(bad), _raw(good)])
    result, meta = await validate_and_retry("summary", "prompt", call, email)
    assert call.calls["i"] == 2
    assert meta.retries == 1
    assert result["resumo"] == "Aviso de cobranca da empresa X"


@pytest.mark.asyncio
async def test_pipeline_action_rascunho_without_value_retries():
    email = {"subject": "cobranca", "body": "Debito de R$ 826,92 em aberto."}
    bad = json.dumps({
        "acao": "rascunho", "justificativa": "responder",
        "rascunho_resposta": "Ola, vou verificar com calma e retorno com detalhes em breve.",
    })
    good = json.dumps({
        "acao": "rascunho", "justificativa": "responder",
        "rascunho_resposta": "Confirmo o debito de R$ 826,92 e vou regularizar ainda hoje.",
    })
    call = _make_call_fn([_raw(bad), _raw(good)])
    result, meta = await validate_and_retry("action", "prompt", call, email)
    assert call.calls["i"] == 2
    assert meta.retries == 1
    assert "R$ 826,92" in result["rascunho_resposta"]


@pytest.mark.asyncio
async def test_pipeline_action_invented_value_removes_rascunho():
    email = {"subject": "duvida", "body": "Quero saber o horario de atendimento."}
    # Both attempts invent a value
    bad = json.dumps({
        "acao": "rascunho", "justificativa": "responder",
        "rascunho_resposta": "Cobraremos R$ 999,99 conforme combinado anteriormente, obrigado.",
    })
    call = _make_call_fn([_raw(bad), _raw(bad)])
    result, meta = await validate_and_retry("action", "prompt", call, email)
    assert "rascunho_inventado" in meta.flags
    assert meta.fallback_used is True
    assert "removido pela validacao" in result["rascunho_resposta"].lower()


@pytest.mark.asyncio
async def test_pipeline_json_parse_failure_retries_then_fallback():
    email = {"subject": "oi", "body": "teste"}
    call = _make_call_fn([_raw("not json at all"), _raw("still broken")])
    result, meta = await validate_and_retry("classification", "prompt", call, email)
    assert meta.json_parse_failed is True
    assert meta.fallback_used is True
    assert call.calls["i"] == 2
    # default classification filled in
    assert result["categoria"] == "outro"


@pytest.mark.asyncio
async def test_pipeline_schema_invalid_then_good():
    email = {"subject": "oi", "body": "teste"}
    bad = json.dumps({"prioridade": "InvalidLevel", "categoria": "weird", "confianca": "nope"})
    good = json.dumps({
        "importante": True, "prioridade": "Baixa", "categoria": "outro",
        "confianca": 0.4, "razao": "ok",
    })
    # schema is tolerant: bad actually passes (clamps). So we check the tolerance path:
    call = _make_call_fn([_raw(bad)])
    result, meta = await validate_and_retry("classification", "prompt", call, email)
    # Because schema clamps, first response is "valid" after normalization:
    assert meta.schema_valid is True
    assert result["prioridade"] == "Media"  # clamped
    assert result["categoria"] == "outro"


@pytest.mark.asyncio
async def test_pipeline_llm_returns_none_fallback():
    email = {"subject": "oi", "body": "x"}

    async def _call(p):
        return None

    result, meta = await validate_and_retry("action", "prompt", _call, email)
    assert meta.fallback_used is True
    assert result["acao"] == "notificar"
