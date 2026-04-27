# Code Quality Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remover criação de rascunho dentro do Gmail, suprimir geração de rascunho para emails não-respondíveis, e aplicar 4 melhorias prioritárias (log redaction, refactor de `process_email`, workers resilientes, erros tipados) em **1 PR único** com 6 commits sequenciais.

**Architecture:** Branch `feat/code-quality-improvements` saindo de `master`. Cada etapa = 1 commit coeso, revertível via `git revert`. Suite de testes deve passar após **cada** commit. Sem migração breaking — só adicionar coluna. Sem alteração de API externa.

**Tech Stack:** Python 3.11+, FastAPI, asyncio, asyncpg, Pydantic v2, pytest+pytest-asyncio, OpenRouter (LLM), Gmail API (googleapiclient), Qdrant.

**Spec:** [`docs/superpowers/specs/2026-04-27-code-quality-improvements-design.md`](../specs/2026-04-27-code-quality-improvements-design.md)

---

## Setup (executar uma vez antes da Etapa 1)

- [ ] **Step 0.1: Confirmar que está em `master` e atualizar**

```bash
cd "C:\Users\PC Di\Desktop\CODIGO\Agente-Email-Openclaw"
git checkout master
git pull origin master
git status
```

Esperado: working tree clean, branch master atualizado.

- [ ] **Step 0.2: Criar branch de trabalho**

```bash
git checkout -b feat/code-quality-improvements
git status
```

Esperado: `On branch feat/code-quality-improvements`, nothing to commit.

- [ ] **Step 0.3: Confirmar suite verde antes de começar**

```bash
.venv\Scripts\activate
pytest tests/ -x --tb=short
```

Esperado: todos os testes passam. Se algum estiver falhando em `master`, parar e investigar antes de mexer.

---

## Etapa 1 — Remover criação de rascunho no Gmail

**Files:**
- Modify: `orchestrator/handlers/email_processor.py:492-501` (remover ramo `acao == "rascunho"`)
- Modify: `orchestrator/services/gmail_service.py:206-236` (remover método `create_draft`)
- Test: `tests/test_email_processor_no_gmail_draft.py` (novo, 1 teste de regressão)

### Task 1.1: Teste de regressão garantindo que `create_draft` nunca é chamado

- [ ] **Step 1.1.1: Escrever teste que falha**

Criar `tests/test_email_processor_no_gmail_draft.py`:

```python
"""Regression test: ação 'rascunho' não deve mais chamar gmail.create_draft."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_rascunho_action_does_not_call_create_draft():
    """Quando LLM decide acao='rascunho', NÃO devemos criar draft no Gmail."""
    from orchestrator.handlers.email_processor import EmailProcessor

    # Setup: mock todas as dependências
    gmail = MagicMock()
    gmail.create_draft = AsyncMock()  # se for chamado, capturamos
    gmail.archive_email = AsyncMock()

    db = MagicMock()
    db.get_account = AsyncMock(return_value={"id": 1})
    db.create_task = AsyncMock()

    processor = EmailProcessor(
        gmail=gmail,
        llm=MagicMock(),
        db=db,
        qdrant=MagicMock(),
        telegram=MagicMock(),
        learning=MagicMock(),
        metrics=MagicMock(),
        alerts=MagicMock(),
    )

    action = {
        "acao": "rascunho",
        "rascunho_resposta": "Olá, vou responder amanhã.",
    }
    email = {"id": "abc", "from": "x@y.com", "subject": "Test", "threadId": "t1"}

    # Act
    await processor._execute_action(action, email, "conta_test")

    # Assert
    gmail.create_draft.assert_not_called()
```

- [ ] **Step 1.1.2: Rodar teste e verificar que falha**

```bash
pytest tests/test_email_processor_no_gmail_draft.py -v
```

Esperado: **FAIL** — `create_draft.assert_not_called()` falha porque o código atual chama o método.

### Task 1.2: Remover o ramo `rascunho` em `_execute_action`

- [ ] **Step 1.2.1: Editar `email_processor.py`**

Em [`orchestrator/handlers/email_processor.py`](orchestrator/handlers/email_processor.py), localizar o bloco na linha ~492:

```python
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
```

**Substituir por:**

```python
elif acao == "rascunho":
    # Rascunho NÃO é mais salvo no Gmail. Texto vai pro Telegram via
    # notificacao (ja gerada antes em process_email). Usuario envia via
    # botao "Enviar" se quiser, ou ignora.
    logger.info(f"[{email_id}] Acao=rascunho — texto enviado ao Telegram, sem draft no Gmail")
```

- [ ] **Step 1.2.2: Rodar teste e verificar que passa**

```bash
pytest tests/test_email_processor_no_gmail_draft.py -v
```

Esperado: **PASS**.

### Task 1.3: Remover método `create_draft` do `gmail_service.py`

- [ ] **Step 1.3.1: Confirmar que nada mais usa `create_draft`**

```bash
grep -rn "create_draft" orchestrator/ tests/ --include="*.py"
```

Esperado: apenas referências dentro de `gmail_service.py` (definição) — nenhuma chamada externa após Step 1.2.1.

- [ ] **Step 1.3.2: Remover o método `create_draft` em `gmail_service.py`**

Em [`orchestrator/services/gmail_service.py`](orchestrator/services/gmail_service.py), apagar linhas 206-236 (método `async def create_draft(...)` inteiro). Manter o resto do arquivo intacto.

- [ ] **Step 1.3.3: Rodar suite completa**

```bash
pytest tests/ -x --tb=short
```

Esperado: **TODOS PASSAM**. Se algum teste de gmail_service quebrar, é porque depende do método removido — ajustar (geralmente é teste obsoleto, deletar).

### Task 1.4: Commit da Etapa 1

- [ ] **Step 1.4.1: Stage + commit**

```bash
git add orchestrator/handlers/email_processor.py orchestrator/services/gmail_service.py tests/test_email_processor_no_gmail_draft.py
git commit -m "feat: stop saving drafts in Gmail when action=rascunho

Texto do rascunho continua sendo enviado ao Telegram pelo fluxo de
notificacao. Usuario pode enviar resposta via botao Enviar (que usa
gmail.send_reply, nao create_draft).

- Remove ramo 'rascunho' em email_processor._execute_action
- Remove metodo gmail_service.create_draft (nada mais usa)
- Adiciona teste de regressao
"
```

---

## Etapa 2 — Detectar emails não-respondíveis

**Files:**
- Create: `orchestrator/utils/reply_policy.py` (módulo novo: regex sender + categorias)
- Create: `tests/test_reply_policy.py`
- Create: `sql/migrations/009_decisions_no_reply_detected.sql`
- Modify: `orchestrator/services/llm_validator.py:36-40` (adicionar categorias)
- Modify: `orchestrator/services/llm_validator.py` (regra de rebaixamento)
- Modify: `orchestrator/handlers/email_processor.py` (chamar reply_policy antes do action prompt; passar flag pro DB)
- Modify: `orchestrator/services/llm_service.py:608-611` (prompt de ação condicional)
- Modify: `orchestrator/services/database_service.py` (gravar `no_reply_detected` na decision)

### Task 2.1: Módulo `reply_policy.py` com detecção determinística

- [ ] **Step 2.1.1: Escrever testes que falham**

Criar `tests/test_reply_policy.py`:

```python
"""Tests for orchestrator.utils.reply_policy."""
import pytest


class TestIsNoReplySender:
    @pytest.mark.parametrize("addr,expected", [
        # MUST match
        ("noreply@github.com", True),
        ("no-reply@stripe.com", True),
        ("no_reply@example.com", True),
        ("donotreply@bank.com", True),
        ("do-not-reply@aws.com", True),
        ("mailer-daemon@gmail.com", True),
        ("postmaster@example.com", True),
        ("notifications@github.com", True),
        ("notification@linkedin.com", True),
        ("alerts@grafana.com", True),
        ("alert@pagerduty.com", True),
        ("news@medium.com", True),
        ("newsletter@substack.com", True),
        ("bounce@mailchimp.com", True),
        ("automated@bot.com", True),
        ("system@app.com", True),
        ("Joe Doe <noreply@x.com>", True),  # com display name
        ("NOREPLY@upper.com", True),  # case insensitive
        # MUST NOT match
        ("john@gmail.com", False),
        ("contact@company.com", False),
        ("support@stripe.com", False),
        ("ana.silva@empresa.com.br", False),
        ("", False),
        (None, False),
    ])
    def test_detection(self, addr, expected):
        from orchestrator.utils.reply_policy import is_no_reply_sender
        assert is_no_reply_sender(addr) is expected


class TestIsNonReplyableCategory:
    @pytest.mark.parametrize("cat,expected", [
        ("newsletter", True),
        ("promocao", True),
        ("notificacao_automatica", True),
        ("transacional", True),
        ("NEWSLETTER", True),  # case insensitive
        ("cliente", False),
        ("financeiro", False),
        ("trabalho", False),
        ("", False),
        (None, False),
    ])
    def test_category(self, cat, expected):
        from orchestrator.utils.reply_policy import is_non_replyable_category
        assert is_non_replyable_category(cat) is expected
```

- [ ] **Step 2.1.2: Rodar testes e verificar falha**

```bash
pytest tests/test_reply_policy.py -v
```

Esperado: **FAIL** com `ImportError` ou `ModuleNotFoundError`.

- [ ] **Step 2.1.3: Implementar módulo mínimo**

Criar `orchestrator/utils/reply_policy.py`:

```python
"""Deterministic detection of non-replyable emails.

Layer A: regex on sender address (no-reply, mailer-daemon, etc.)
Layer B: classifier-driven category check
"""
from __future__ import annotations

import re
from typing import Optional

# Patterns case-insensitive. Matched against the local-part (before @) and
# also full address. We compile once at import.
_NO_REPLY_LOCAL_PARTS = re.compile(
    r"""(?ix)
    ^(
        no[-_]?reply
      | do[-_]?not[-_]?reply
      | mailer[-_]?daemon
      | postmaster
      | bounces?
      | notifications?
      | alerts?
      | news
      | newsletter
      | automated
      | system
    )(\+.*)?$
    """,
)

# Non-replyable categories produced by the classifier (see llm_validator.py).
_NON_REPLYABLE_CATEGORIES = frozenset({
    "newsletter",
    "promocao",
    "notificacao_automatica",
    "transacional",
})


def _extract_local_part(addr: str) -> str:
    """Return the local-part of an email address.

    Accepts forms: 'foo@bar.com', '<foo@bar.com>', 'Name <foo@bar.com>'.
    Returns lowercase local-part or empty string.
    """
    if not addr or not isinstance(addr, str):
        return ""
    s = addr.strip()
    # Pull out angle-bracketed address if present
    m = re.search(r"<([^>]+)>", s)
    if m:
        s = m.group(1).strip()
    if "@" not in s:
        return ""
    return s.split("@", 1)[0].strip().lower()


def is_no_reply_sender(from_addr: Optional[str]) -> bool:
    """True if the sender address looks like an automated/no-reply mailbox."""
    local = _extract_local_part(from_addr or "")
    if not local:
        return False
    return bool(_NO_REPLY_LOCAL_PARTS.match(local))


def is_non_replyable_category(category: Optional[str]) -> bool:
    """True if the classifier category is one we should never draft a reply for."""
    if not category or not isinstance(category, str):
        return False
    return category.strip().lower() in _NON_REPLYABLE_CATEGORIES
```

- [ ] **Step 2.1.4: Rodar testes e verificar passa**

```bash
pytest tests/test_reply_policy.py -v
```

Esperado: **TODOS PASSAM** (24+ casos parametrizados).

### Task 2.2: Adicionar novas categorias ao validator

- [ ] **Step 2.2.1: Escrever teste em `tests/test_llm_validator.py` (anexar ao final)**

```python
def test_validator_accepts_new_non_replyable_categories():
    from orchestrator.services.llm_validator import ClassificationOut
    for cat in ["notificacao_automatica", "transacional"]:
        out = ClassificationOut(categoria=cat)
        assert out.categoria == cat
```

- [ ] **Step 2.2.2: Rodar e ver falhar**

```bash
pytest tests/test_llm_validator.py::test_validator_accepts_new_non_replyable_categories -v
```

Esperado: **FAIL** — categoria vira "outro" porque não está no set.

- [ ] **Step 2.2.3: Atualizar `_VALID_CATEGORIES`**

Em `orchestrator/services/llm_validator.py:36-39`, substituir:

```python
_VALID_CATEGORIES = {
    "cliente", "financeiro", "pessoal", "trabalho",
    "promocao", "newsletter", "outro",
}
```

por:

```python
_VALID_CATEGORIES = {
    "cliente", "financeiro", "pessoal", "trabalho",
    "promocao", "newsletter", "notificacao_automatica", "transacional",
    "outro",
}
```

- [ ] **Step 2.2.4: Rodar e ver passar**

```bash
pytest tests/test_llm_validator.py -v
```

Esperado: **PASS** (incluindo novo teste).

### Task 2.3: Migration `009_decisions_no_reply_detected.sql`

- [ ] **Step 2.3.1: Criar arquivo de migração**

Criar `sql/migrations/009_decisions_no_reply_detected.sql`:

```sql
-- Migration 009: marca decisões geradas para emails não-respondíveis.
-- Permite auditoria + base para tuning futuro do classifier.

ALTER TABLE decisions
    ADD COLUMN IF NOT EXISTS no_reply_detected BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN decisions.no_reply_detected IS
    'True quando reply_policy bateu (sender no-reply OU categoria nao-respondivel). Quando true, acao=rascunho foi rebaixada para notificar.';

CREATE INDEX IF NOT EXISTS idx_decisions_no_reply
    ON decisions(no_reply_detected)
    WHERE no_reply_detected = TRUE;
```

- [ ] **Step 2.3.2: Atualizar `sql/schema.sql` para refletir a coluna**

Localizar a definição da tabela `decisions` em `sql/schema.sql` e adicionar a coluna `no_reply_detected BOOLEAN NOT NULL DEFAULT FALSE,` na ordem certa (após colunas existentes, antes de PKs/constraints). Manter consistência com migration.

### Task 2.4: Wire reply_policy no email_processor

- [ ] **Step 2.4.1: Escrever teste de integração**

Criar `tests/test_email_processor_no_reply_detection.py`:

```python
"""Integration: emails de senders no-reply nunca geram acao=rascunho."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_no_reply_sender_demotes_rascunho_to_notificar(monkeypatch):
    """Mesmo se LLM retornar 'rascunho', sender no-reply deve rebaixar para 'notificar'."""
    from orchestrator.services.llm_validator import demote_rascunho_if_non_replyable

    # Caso 1: sender no-reply + LLM tentou rascunho
    action = {"acao": "rascunho", "rascunho_resposta": "Resposta inutil"}
    result = demote_rascunho_if_non_replyable(
        action,
        from_addr="noreply@github.com",
        categoria="trabalho",
    )
    assert result["acao"] == "notificar"
    assert result.get("rascunho_resposta") is None
    assert result.get("flags", {}).get("rascunho_em_no_reply") is True

    # Caso 2: categoria newsletter
    action2 = {"acao": "rascunho", "rascunho_resposta": "Resposta a newsletter"}
    result2 = demote_rascunho_if_non_replyable(
        action2,
        from_addr="alguem@empresa.com",
        categoria="newsletter",
    )
    assert result2["acao"] == "notificar"

    # Caso 3: caso normal — não rebaixa
    action3 = {"acao": "rascunho", "rascunho_resposta": "Boa resposta"}
    result3 = demote_rascunho_if_non_replyable(
        action3,
        from_addr="cliente@empresa.com",
        categoria="cliente",
    )
    assert result3["acao"] == "rascunho"
    assert result3.get("rascunho_resposta") == "Boa resposta"
```

- [ ] **Step 2.4.2: Rodar e ver falhar**

```bash
pytest tests/test_email_processor_no_reply_detection.py -v
```

Esperado: **FAIL** — `demote_rascunho_if_non_replyable` não existe.

- [ ] **Step 2.4.3: Adicionar função `demote_rascunho_if_non_replyable` em `llm_validator.py`**

Adicionar ao final de `orchestrator/services/llm_validator.py`:

```python
def demote_rascunho_if_non_replyable(
    action: Dict[str, Any],
    from_addr: Optional[str],
    categoria: Optional[str],
) -> Dict[str, Any]:
    """Rebaixa acao='rascunho' para 'notificar' quando email é não-respondível.

    Cenarios:
    - sender no-reply (regex em from_addr)
    - categoria nao-respondivel (newsletter, promocao, etc.)

    Quando rebaixa: remove rascunho_resposta e marca flag
    'rascunho_em_no_reply' em action['flags'] para telemetria.
    """
    from orchestrator.utils.reply_policy import (
        is_no_reply_sender,
        is_non_replyable_category,
    )

    if action.get("acao") != "rascunho":
        return action

    is_no_reply = is_no_reply_sender(from_addr) or is_non_replyable_category(categoria)
    if not is_no_reply:
        return action

    new_action = dict(action)
    new_action["acao"] = "notificar"
    new_action["rascunho_resposta"] = None
    flags = dict(new_action.get("flags") or {})
    flags["rascunho_em_no_reply"] = True
    new_action["flags"] = flags
    return new_action
```

- [ ] **Step 2.4.4: Rodar e ver passar**

```bash
pytest tests/test_email_processor_no_reply_detection.py -v
```

Esperado: **PASS**.

### Task 2.5: Aplicar `demote_rascunho_if_non_replyable` no fluxo principal

- [ ] **Step 2.5.1: Localizar onde `action` é decidida em `email_processor.py`**

Procurar pela linha onde a action é construída/atribuída antes de `_execute_action` ser chamado. Tipicamente após `decide_action(...)` retornar.

```bash
grep -n "decide_action\|_execute_action" orchestrator/handlers/email_processor.py
```

- [ ] **Step 2.5.2: Inserir chamada `demote_rascunho_if_non_replyable`**

Logo após o `action = ...` (resultado do LLM action), antes de persistir/executar:

```python
# NOVO: rebaixar rascunho se email é nao-respondivel
from orchestrator.services.llm_validator import demote_rascunho_if_non_replyable
action = demote_rascunho_if_non_replyable(
    action,
    from_addr=email.get("from", ""),
    categoria=classification.get("categoria", ""),
)
no_reply_detected = bool(action.get("flags", {}).get("rascunho_em_no_reply"))
```

(Use `no_reply_detected` ao gravar a decision no DB — ver Step 2.6.)

- [ ] **Step 2.5.3: Rodar suite completa**

```bash
pytest tests/ -x --tb=short
```

Esperado: **PASS**.

### Task 2.6: Persistir `no_reply_detected` na tabela `decisions`

- [ ] **Step 2.6.1: Localizar `INSERT INTO decisions` no `database_service.py`**

```bash
grep -n "INSERT INTO decisions\|create_decision" orchestrator/services/database_service.py
```

- [ ] **Step 2.6.2: Adicionar coluna ao INSERT**

Atualizar `create_decision(...)` (ou função equivalente) para aceitar `no_reply_detected: bool = False` e incluir na query:

```python
INSERT INTO decisions (..., no_reply_detected) VALUES (..., $N)
```

- [ ] **Step 2.6.3: Atualizar a chamada em `email_processor.py`**

Passar `no_reply_detected=no_reply_detected` (calculado em Step 2.5.2) na criação da decision.

- [ ] **Step 2.6.4: Rodar suite**

```bash
pytest tests/ -x --tb=short
```

### Task 2.7: Atualizar prompt de ação (Camada C)

- [ ] **Step 2.7.1: Editar `orchestrator/services/llm_service.py:608-611`**

Localizar:

```python
1. "notificar" - Apenas notificar no Telegram
2. "arquivar" - Arquivar email (newsletter, promocao)
3. "criar_task" - Criar tarefa no Notion
4. "rascunho" - Criar rascunho de resposta (sem enviar)
```

Adicionar lógica condicional: a função que monta esse prompt deve receber `is_non_replyable: bool` e, quando True, omitir a opção 4. Usar f-string ou template:

```python
def _build_action_prompt(..., is_non_replyable: bool) -> str:
    actions_block = """
1. "notificar" - Apenas notificar no Telegram
2. "arquivar" - Arquivar email (newsletter, promocao)
3. "criar_task" - Criar tarefa no Notion
"""
    if not is_non_replyable:
        actions_block += '4. "rascunho" - Criar rascunho de resposta (sem enviar)\n'
    # ... resto do prompt referenciando actions_block ...
```

A chamada em `email_processor.py` passa `is_non_replyable=is_no_reply_sender(...) or is_non_replyable_category(...)` calculado **antes** da chamada do LLM de ação.

- [ ] **Step 2.7.2: Adicionar teste para o prompt condicional**

Em `tests/test_llm_service.py` (ou criar):

```python
def test_action_prompt_omits_rascunho_for_non_replyable():
    from orchestrator.services.llm_service import LLMService
    svc = LLMService.__new__(LLMService)  # bypass __init__
    prompt = svc._build_action_prompt(..., is_non_replyable=True)
    assert "rascunho" not in prompt.lower()
    prompt_normal = svc._build_action_prompt(..., is_non_replyable=False)
    assert "rascunho" in prompt_normal.lower()
```

(Ajustar argumentos conforme assinatura real.)

- [ ] **Step 2.7.3: Rodar e ver passar**

```bash
pytest tests/test_llm_service.py -v
```

### Task 2.8: Flag opcional `NO_REPLY_AUTO_ARCHIVE`

- [ ] **Step 2.8.1: Adicionar setting**

Em `orchestrator/settings.py`, adicionar:

```python
no_reply_auto_archive: bool = False  # Se True, sender no-reply força acao=arquivar sem chamar LLM
```

- [ ] **Step 2.8.2: Aplicar em `email_processor.py`**

Antes de chamar o LLM de action, se `is_no_reply_sender(...) and settings.no_reply_auto_archive`:

```python
action = {"acao": "arquivar", "justificativa": "Sender no-reply (auto-archive)"}
no_reply_detected = True
# pular chamada do LLM de ação
```

(Manter `False` como default — só ativa via env var quando quisermos.)

### Task 2.9: Commit da Etapa 2

- [ ] **Step 2.9.1: Stage + commit**

```bash
git add orchestrator/utils/reply_policy.py \
        tests/test_reply_policy.py \
        tests/test_email_processor_no_reply_detection.py \
        sql/migrations/009_decisions_no_reply_detected.sql \
        sql/schema.sql \
        orchestrator/services/llm_validator.py \
        orchestrator/services/llm_service.py \
        orchestrator/services/database_service.py \
        orchestrator/handlers/email_processor.py \
        orchestrator/settings.py \
        tests/test_llm_validator.py \
        tests/test_llm_service.py
git commit -m "feat: detect non-replyable emails, suppress draft generation

Defesa em camadas:
- A: regex em sender (noreply, mailer-daemon, notifications, etc.)
- B: novas categorias no classifier (notificacao_automatica, transacional)
- C: prompt de acao omite opcao 'rascunho' para emails nao-respondiveis
- D: rebaixamento pos-LLM se acao=rascunho foi gerada mesmo assim

Migration 009: coluna decisions.no_reply_detected para auditoria.
Flag NO_REPLY_AUTO_ARCHIVE=false (default) — opcao futura de pular
LLM de acao para senders no-reply.
"
```

---

## Etapa 3 — Redaction de payload sensível em logs

**Files:**
- Create: `orchestrator/utils/log_redaction.py`
- Create: `tests/test_log_redaction.py`
- Modify: `orchestrator/main.py:267` (e qualquer outro `logger.*` que loga body de webhook ou response externa)

### Task 3.1: Função `redact_sensitive`

- [ ] **Step 3.1.1: Escrever testes**

Criar `tests/test_log_redaction.py`:

```python
import pytest


class TestRedactSensitive:
    def test_redacts_top_level_token(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({"token": "abc123", "ok": True})
        assert out == {"token": "<REDACTED>", "ok": True}

    def test_redacts_case_insensitive(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({"Token": "x", "AUTHORIZATION": "y"})
        assert out["Token"] == "<REDACTED>"
        assert out["AUTHORIZATION"] == "<REDACTED>"

    def test_redacts_nested_dict(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({
            "level1": {"password": "secret", "ok": 1},
        })
        assert out["level1"]["password"] == "<REDACTED>"
        assert out["level1"]["ok"] == 1

    def test_redacts_list_of_dicts(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        out = redact_sensitive({
            "items": [{"api_key": "k1"}, {"api_key": "k2", "name": "x"}]
        })
        assert out["items"][0]["api_key"] == "<REDACTED>"
        assert out["items"][1]["name"] == "x"

    def test_full_key_list(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        keys = ["token", "authorization", "password", "secret",
                "api_key", "access_token", "refresh_token", "cookie"]
        d = {k: "value" for k in keys}
        out = redact_sensitive(d)
        for k in keys:
            assert out[k] == "<REDACTED>"

    def test_does_not_mutate_input(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        original = {"token": "a"}
        redact_sensitive(original)
        assert original == {"token": "a"}

    def test_handles_non_dict_input_gracefully(self):
        from orchestrator.utils.log_redaction import redact_sensitive
        assert redact_sensitive(None) is None
        assert redact_sensitive("string") == "string"
        assert redact_sensitive([1, 2]) == [1, 2]
```

- [ ] **Step 3.1.2: Rodar e ver falhar**

```bash
pytest tests/test_log_redaction.py -v
```

Esperado: **FAIL** (módulo inexistente).

- [ ] **Step 3.1.3: Implementar**

Criar `orchestrator/utils/log_redaction.py`:

```python
"""Redact sensitive keys from dicts before logging.

Used in webhook handlers and any place where external payloads might
contain credentials.
"""
from __future__ import annotations

from typing import Any

_SENSITIVE_KEYS = frozenset({
    "token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "access_token",
    "refresh_token",
    "cookie",
})

_REDACTED = "<REDACTED>"


def redact_sensitive(value: Any) -> Any:
    """Return a deep copy with sensitive keys replaced by '<REDACTED>'.

    - Keys are matched case-insensitively against _SENSITIVE_KEYS
    - Recurses into nested dicts and lists
    - Non-dict, non-list values pass through unchanged (None, str, int, etc.)
    - Does NOT mutate the input
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = _REDACTED
            else:
                out[k] = redact_sensitive(v)
        return out
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value]
    return value
```

- [ ] **Step 3.1.4: Rodar e passar**

```bash
pytest tests/test_log_redaction.py -v
```

Esperado: **TODOS PASSAM**.

### Task 3.2: Aplicar redaction em `main.py:267`

- [ ] **Step 3.2.1: Editar `main.py`**

Em [`orchestrator/main.py:267`](orchestrator/main.py#L267), localizar:

```python
logger.info(f"Webhook recebido: {json.dumps(body)[:500]}")
```

Substituir por:

```python
from orchestrator.utils.log_redaction import redact_sensitive
logger.info(f"Webhook recebido: {json.dumps(redact_sensitive(body))[:500]}")
```

(Mover o `import` pro topo do arquivo se ainda não está lá.)

- [ ] **Step 3.2.2: Buscar outros logs com payload externo**

```bash
grep -n "json.dumps\|body)" orchestrator/main.py orchestrator/services/gmail_service.py orchestrator/services/telegram_service.py
```

Para cada match que loga payload externo, aplicar `redact_sensitive`.

- [ ] **Step 3.2.3: Rodar suite completa**

```bash
pytest tests/ -x --tb=short
```

Esperado: **PASS**.

### Task 3.3: Commit Etapa 3

```bash
git add orchestrator/utils/log_redaction.py tests/test_log_redaction.py orchestrator/main.py
git commit -m "fix(security): redact sensitive keys in webhook payload logs

main.py:267 logava payload completo do webhook. Se o payload contem
token/authorization/api_key, ele iria parar no arquivo de log em
texto plano. Adiciona redact_sensitive() que substitui por <REDACTED>.

Cobertura: 8 chaves sensiveis, dicts aninhados, listas, case-insensitive.
"
```

---

## Etapa 4 — Refactor de `process_email()` em 6 métodos

**Files:**
- Modify (grande): `orchestrator/handlers/email_processor.py` (quebra função pública em 6 métodos privados)
- Test: rodar suite existente — não pode quebrar

### Task 4.1: Mapear o conteúdo atual de `process_email()`

- [ ] **Step 4.1.1: Ler a função inteira (linhas 56-638)**

```bash
# Conferir tamanho atual
wc -l orchestrator/handlers/email_processor.py
```

- [ ] **Step 4.1.2: Identificar blocos lógicos e anotar com comentários**

Inserir comentários `# === BLOCO N: nome ===` antes de cada uma das 6 fases (sem mudar código ainda):

```python
# === BLOCO 1: fetch_and_parse ===
# busca Gmail + extrai body + anexos PDF

# === BLOCO 2: build_context ===
# thread context + sender profile + emails similares

# === BLOCO 3: classify_and_summarize ===
# chama LLM camadas 1+2 (classificação + resumo)

# === BLOCO 4: decide_action ===
# chama LLM camada 3 (ação) — JÁ COM demote_rascunho_if_non_replyable da Etapa 2

# === BLOCO 5: execute_action ===
# arquivar / criar_task / rascunho (delega para _execute_action que já existe)

# === BLOCO 6: persist_and_notify ===
# DB + Qdrant + Telegram + learning engine
```

- [ ] **Step 4.1.3: Commit intermediário (apenas comentários)**

```bash
git add orchestrator/handlers/email_processor.py
git commit -m "refactor: annotate process_email blocks (no behavior change)"
```

### Task 4.2: Extrair `_fetch_and_parse`

- [ ] **Step 4.2.1: Criar método privado**

Mover o BLOCO 1 para um método novo `async def _fetch_and_parse(self, email_id: str, account: str) -> Optional[Dict[str, Any]]:` retornando o dict `email` enriquecido. Em `process_email`, substituir o bloco por `email = await self._fetch_and_parse(email_id, account)`.

- [ ] **Step 4.2.2: Rodar suite**

```bash
pytest tests/ -x --tb=short
```

Esperado: **PASS** (nenhum teste deve quebrar — refactor sem mudança de comportamento).

- [ ] **Step 4.2.3: Commit**

```bash
git commit -am "refactor: extract _fetch_and_parse from process_email"
```

### Task 4.3: Extrair `_build_context`

Repetir o padrão da Task 4.2 para o BLOCO 2.

- [ ] **Step 4.3.1: Criar método** `async def _build_context(self, email: Dict[str, Any], account: str) -> Dict[str, Any]:`
- [ ] **Step 4.3.2: Rodar suite, esperar PASS**
- [ ] **Step 4.3.3: Commit:** `refactor: extract _build_context from process_email`

### Task 4.4: Extrair `_classify_and_summarize`

- [ ] **Step 4.4.1: Criar método** `async def _classify_and_summarize(self, email, context) -> Tuple[Dict, Dict]:` (retorna `(classification, summary)`)
- [ ] **Step 4.4.2: Rodar suite, esperar PASS**
- [ ] **Step 4.4.3: Commit:** `refactor: extract _classify_and_summarize`

### Task 4.5: Extrair `_decide_action`

- [ ] **Step 4.5.1: Criar método** `async def _decide_action(self, email, classification, summary, context) -> Dict[str, Any]:` (já incluindo a chamada de `demote_rascunho_if_non_replyable` adicionada na Etapa 2)
- [ ] **Step 4.5.2: Rodar suite, esperar PASS**
- [ ] **Step 4.5.3: Commit:** `refactor: extract _decide_action`

### Task 4.6: Extrair `_persist_and_notify`

(`_execute_action` já existe — não precisa extrair.)

- [ ] **Step 4.6.1: Criar método** `async def _persist_and_notify(self, email, classification, summary, action, account, no_reply_detected) -> None:` agrupando DB, Qdrant, Telegram, learning
- [ ] **Step 4.6.2: Rodar suite, esperar PASS**
- [ ] **Step 4.6.3: Commit:** `refactor: extract _persist_and_notify`

### Task 4.7: Verificar tamanho final de `process_email`

- [ ] **Step 4.7.1: Confirmar < 60 linhas**

A função pública `process_email` deve agora chamar os 6 métodos em sequência, com tratamento de erro de alto nível. Conferir:

```bash
# Encontrar linhas da funcao
grep -n "async def process_email" orchestrator/handlers/email_processor.py
```

Esperado: corpo de `process_email` < 60 linhas.

- [ ] **Step 4.7.2: Squash dos commits 4.2 a 4.6 num único commit**

Como a Etapa 4 é um único commit no PR final, fazer interactive rebase para juntar:

```bash
git rebase -i HEAD~6
# (juntar os 6 commits de refactor em 1 com pick + squash)
```

Mensagem final do commit consolidado:

```
refactor: split process_email() into 6 private methods

process_email() tinha 582 linhas orquestrando 10+ etapas.
Quebrado em metodos privados, cada um com responsabilidade clara:

- _fetch_and_parse
- _build_context
- _classify_and_summarize
- _decide_action
- _execute_action (ja existia)
- _persist_and_notify

process_email() agora tem ~50 linhas de orquestracao + try/except
top-level. Sem mudanca de comportamento — toda a suite de testes
passa sem alteracao.
```

---

## Etapa 5 — Workers de background resilientes

**Files:**
- Create: `orchestrator/utils/worker.py`
- Create: `tests/test_resilient_worker.py`
- Modify: `orchestrator/main.py:125-170` (substituir os 3 workers por chamadas a `run_resilient_worker`)
- Modify: `orchestrator/services/metrics_service.py` (adicionar contadores de worker se não existirem)

### Task 5.1: Função `run_resilient_worker`

- [ ] **Step 5.1.1: Escrever testes**

Criar `tests/test_resilient_worker.py`:

```python
import asyncio
import pytest


@pytest.mark.asyncio
async def test_runs_iterations_periodically():
    from orchestrator.utils.worker import run_resilient_worker
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    task = asyncio.create_task(
        run_resilient_worker("test", tick, interval=0.05, iteration_timeout=1.0)
    )
    await asyncio.sleep(0.18)  # ~3 iterations
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert counter["n"] >= 2


@pytest.mark.asyncio
async def test_backoff_grows_on_repeated_errors():
    from orchestrator.utils.worker import run_resilient_worker
    timestamps = []

    async def fail():
        timestamps.append(asyncio.get_event_loop().time())
        raise RuntimeError("boom")

    task = asyncio.create_task(
        run_resilient_worker(
            "test", fail, interval=0.01,
            iteration_timeout=1.0, max_backoff=0.5
        )
    )
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Esperamos que o gap entre falhas cresça (backoff exponencial)
    assert len(timestamps) >= 2
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    # Gap mais recente deve ser maior que o primeiro (backoff cresceu)
    assert gaps[-1] > gaps[0]


@pytest.mark.asyncio
async def test_iteration_timeout_aborts_hung_function():
    from orchestrator.utils.worker import run_resilient_worker
    completed = {"n": 0}

    async def hang():
        await asyncio.sleep(10)  # vai estourar o timeout
        completed["n"] += 1

    task = asyncio.create_task(
        run_resilient_worker(
            "test", hang, interval=0.01, iteration_timeout=0.05
        )
    )
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert completed["n"] == 0  # nunca completou — sempre cortado
```

- [ ] **Step 5.1.2: Rodar e ver falhar**

```bash
pytest tests/test_resilient_worker.py -v
```

Esperado: **FAIL** (módulo inexistente).

- [ ] **Step 5.1.3: Implementar**

Criar `orchestrator/utils/worker.py`:

```python
"""Resilient async worker loop with backoff, timeout, request_id and metrics."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextvars import copy_context
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


async def run_resilient_worker(
    name: str,
    fn: Callable[[], Awaitable[None]],
    *,
    interval: float,
    iteration_timeout: float,
    max_backoff: float = 300.0,
    backoff_reset_after: int = 3,
    request_id_var=None,  # ContextVar opcional para correlation
    metrics=None,         # objeto com .inc(name, labels) opcional
) -> None:
    """Run `fn` em loop com:

    - sleep(interval) entre iterações OK
    - backoff exponencial em erro (1s -> 2s -> 4s -> ... -> max_backoff)
    - reset do backoff após N iterações OK consecutivas
    - timeout per-iteration (asyncio.wait_for)
    - request_id novo por iteração injetado em ContextVar (se fornecido)
    - métricas {name, status} se metrics fornecido
    """
    backoff = 1.0
    consecutive_ok = 0
    while True:
        if request_id_var is not None:
            request_id_var.set(str(uuid.uuid4()))

        try:
            await asyncio.wait_for(fn(), timeout=iteration_timeout)
            consecutive_ok += 1
            if consecutive_ok >= backoff_reset_after:
                backoff = 1.0
            if metrics is not None:
                metrics.inc("worker_iteration_total", labels={"name": name, "status": "ok"})
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info(f"Worker {name} cancelled")
            raise
        except asyncio.TimeoutError:
            consecutive_ok = 0
            logger.error(f"Worker {name} iteration timed out after {iteration_timeout}s")
            if metrics is not None:
                metrics.inc("worker_iteration_total", labels={"name": name, "status": "timeout"})
            await asyncio.sleep(min(backoff, max_backoff))
            backoff = min(backoff * 2, max_backoff)
        except Exception as e:
            consecutive_ok = 0
            logger.error(f"Worker {name} error (backoff={backoff:.1f}s): {e}", exc_info=True)
            if metrics is not None:
                metrics.inc("worker_iteration_total", labels={"name": name, "status": "error"})
            await asyncio.sleep(min(backoff, max_backoff))
            backoff = min(backoff * 2, max_backoff)
```

- [ ] **Step 5.1.4: Rodar e passar**

```bash
pytest tests/test_resilient_worker.py -v
```

Esperado: **PASS** (3 testes).

### Task 5.2: Substituir os 3 workers em `main.py`

- [ ] **Step 5.2.1: Refatorar `retry_worker`, `maintenance_worker`, `cleanup_pending_worker`**

Em `orchestrator/main.py:125-170`, substituir cada bloco `async def X_worker(): while True: try: ... except: ... await asyncio.sleep(N)` por uma função `async def _do_X():` que executa **uma iteração só** e retorna, e depois usar `run_resilient_worker`:

```python
from orchestrator.utils.worker import run_resilient_worker

async def _do_retry():
    jobs = await job_queue.get_pending(limit=5)
    for job in jobs:
        try:
            if job["job_type"] == "process_email":
                payload = json.loads(job["payload"]) if isinstance(job["payload"], str) else job["payload"]
                result = await processor.process_email(payload["email_id"], payload["account"], _is_retry=True)
                if result.get("status") == "error":
                    raise RuntimeError(result.get("error", "process_email returned error"))
            await job_queue.mark_completed(job["id"])
        except Exception as e:
            is_dead = await job_queue.mark_failed(job["id"], str(e))
            if is_dead:
                await alerts.alert("job_dead", f"Job #{job['id']} ({job['job_type']}) died: {e}")

async def _do_maintenance():
    result = await metrics.cleanup(retention_days=_settings.metrics_retention_days)
    logger.info(f"Metrics cleanup: {result}")

async def _do_cleanup_pending():
    count = await db.cleanup_expired_actions()
    if count > 0:
        logger.info(f"Cleaned {count} expired pending actions")

retry_task = asyncio.create_task(
    run_resilient_worker(
        "retry", _do_retry,
        interval=60, iteration_timeout=60, metrics=metrics,
    )
)
maint_task = asyncio.create_task(
    run_resilient_worker(
        "maintenance", _do_maintenance,
        interval=86400, iteration_timeout=120, metrics=metrics,
    )
)
cleanup_task = asyncio.create_task(
    run_resilient_worker(
        "cleanup_pending", _do_cleanup_pending,
        interval=60, iteration_timeout=180, metrics=metrics,
    )
)
```

- [ ] **Step 5.2.2: Rodar suite**

```bash
pytest tests/ -x --tb=short
```

Esperado: **PASS**.

### Task 5.3: Commit Etapa 5

```bash
git add orchestrator/utils/worker.py tests/test_resilient_worker.py orchestrator/main.py
git commit -m "feat(reliability): resilient background workers with backoff and timeout

Os 3 workers (retry, maintenance, cleanup_pending) agora usam
run_resilient_worker:

- Backoff exponencial em erro (1s -> 300s teto)
- Reset apos 3 iteracoes OK consecutivas
- Timeout per-iteration explicito (60/120/180s)
- Metricas worker_iteration_total{name, status}
- Reset de request_id por iteracao (correlacao com fluxo principal)

Em producao, se o DB cair por 5min, os workers recuam em vez de
queimar CPU em tight loop.
"
```

---

## Etapa 6 — Erros tipados (Retryable vs Fatal)

**Files:**
- Create: `orchestrator/errors.py`
- Create: `tests/test_errors.py`
- Modify: `orchestrator/services/job_queue.py` (mark_retry vs mark_failed por tipo)
- Modify: `orchestrator/handlers/email_processor.py` (catch + reraise como tipo correto)
- Modify: `orchestrator/services/llm_service.py` (converter exceções)
- Modify: `orchestrator/services/gmail_service.py` (idem)

### Task 6.1: Definir hierarquia de erros

- [ ] **Step 6.1.1: Escrever testes**

Criar `tests/test_errors.py`:

```python
import pytest


def test_retryable_is_exception():
    from orchestrator.errors import RetryableError
    e = RetryableError("transient")
    assert isinstance(e, Exception)
    assert str(e) == "transient"


def test_fatal_is_exception():
    from orchestrator.errors import FatalError
    e = FatalError("bad data")
    assert isinstance(e, Exception)


def test_retryable_and_fatal_are_distinct():
    from orchestrator.errors import RetryableError, FatalError
    assert not issubclass(RetryableError, FatalError)
    assert not issubclass(FatalError, RetryableError)


def test_classify_exception_known_retryable():
    from orchestrator.errors import classify_exception, RetryableError
    import httpx
    assert isinstance(classify_exception(httpx.TimeoutException("t")), RetryableError)
    assert isinstance(classify_exception(httpx.ConnectError("c")), RetryableError)


def test_classify_exception_known_fatal():
    import json
    from orchestrator.errors import classify_exception, FatalError
    assert isinstance(classify_exception(json.JSONDecodeError("x", "y", 0)), FatalError)
    assert isinstance(classify_exception(KeyError("missing")), FatalError)


def test_classify_passes_through_already_typed():
    from orchestrator.errors import classify_exception, RetryableError, FatalError
    rt = RetryableError("x")
    ft = FatalError("y")
    assert classify_exception(rt) is rt
    assert classify_exception(ft) is ft
```

- [ ] **Step 6.1.2: Rodar e ver falhar**

```bash
pytest tests/test_errors.py -v
```

Esperado: **FAIL**.

- [ ] **Step 6.1.3: Implementar**

Criar `orchestrator/errors.py`:

```python
"""Typed errors so the job queue knows whether to retry or fail-fast.

Usage:
    try:
        await some_external_call()
    except Exception as e:
        raise classify_exception(e) from e
"""
from __future__ import annotations

import asyncio
import json


class RetryableError(Exception):
    """Erro transitorio — job queue deve retentar.

    Exemplos: timeout de rede, 5xx, rate limit, DB temporariamente off.
    """


class FatalError(Exception):
    """Erro permanente — retry nao vai resolver, marcar job como failed.

    Exemplos: JSON malformado, schema violado, KeyError, 4xx (exceto 429).
    """


def classify_exception(exc: BaseException) -> Exception:
    """Mapeia uma exceção genérica para Retryable ou Fatal.

    Se ja for um dos tipos, devolve sem wrap. Se nao reconhecer, default
    para FatalError (conservador — evita retry em loop de bug desconhecido).
    """
    # Já tipada
    if isinstance(exc, (RetryableError, FatalError)):
        return exc  # type: ignore[return-value]

    # Network / IO transientes
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
            return RetryableError(str(exc))
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status >= 500 or status == 429:
                return RetryableError(f"HTTP {status}: {exc}")
            return FatalError(f"HTTP {status}: {exc}")
    except ImportError:
        pass

    # Gmail API
    try:
        from googleapiclient.errors import HttpError as GApiError
        if isinstance(exc, GApiError):
            status = getattr(exc, "status_code", None) or (exc.resp.status if hasattr(exc, "resp") else None)
            if status and (int(status) >= 500 or int(status) == 429):
                return RetryableError(f"Gmail API {status}: {exc}")
            return FatalError(f"Gmail API {status}: {exc}")
    except ImportError:
        pass

    # asyncpg
    try:
        import asyncpg
        if isinstance(exc, asyncpg.PostgresConnectionError):
            return RetryableError(f"Postgres connection: {exc}")
        if isinstance(exc, (asyncpg.UniqueViolationError, asyncpg.ForeignKeyViolationError)):
            return FatalError(f"Postgres constraint: {exc}")
    except ImportError:
        pass

    # asyncio
    if isinstance(exc, asyncio.TimeoutError):
        return RetryableError("asyncio timeout")

    # Programming errors / data errors
    if isinstance(exc, (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError, ValueError)):
        return FatalError(f"{type(exc).__name__}: {exc}")

    # Default conservador: Fatal (evita queima de quota em bug desconhecido)
    return FatalError(f"Unclassified: {type(exc).__name__}: {exc}")
```

- [ ] **Step 6.1.4: Rodar e passar**

```bash
pytest tests/test_errors.py -v
```

Esperado: **PASS** (6 testes).

### Task 6.2: Job queue respeita os tipos

- [ ] **Step 6.2.1: Escrever teste de comportamento**

Em `tests/test_job_queue.py` (anexar ou criar):

```python
@pytest.mark.asyncio
async def test_fatal_error_marks_job_failed_no_retry(mock_pool):
    from orchestrator.services.job_queue import JobQueue
    from orchestrator.errors import FatalError

    pool, conn = mock_pool
    queue = JobQueue(pool=pool)

    # Simular: job falha com FatalError → mark_failed direto, sem retry counter
    await queue.handle_failure(job_id=1, exc=FatalError("bad"))

    # Verificar que SQL chamou status='failed' direto
    sql = conn.execute.call_args.args[0].lower()
    assert "status" in sql and "failed" in sql
```

(A interface exata depende do `JobQueue` atual — adaptar.)

- [ ] **Step 6.2.2: Adicionar método `handle_failure`**

Em `orchestrator/services/job_queue.py`:

```python
async def handle_failure(self, job_id: int, exc: Exception) -> bool:
    """Decide entre retry e failed baseado no tipo de exceção.

    Retorna True se job foi marcado como dead (excedeu retries OU é fatal).
    """
    from orchestrator.errors import FatalError, classify_exception

    typed = classify_exception(exc)
    if isinstance(typed, FatalError):
        await self.mark_failed_permanently(job_id, str(typed))
        return True
    return await self.mark_failed(job_id, str(typed))  # incrementa retry, retorna is_dead
```

(Onde `mark_failed_permanently` é uma versão de `mark_failed` que pula o contador de retries e vai direto pra `status='failed'`.)

- [ ] **Step 6.2.3: Atualizar callers em `main.py:139`**

Substituir:

```python
is_dead = await job_queue.mark_failed(job["id"], str(e))
```

por:

```python
is_dead = await job_queue.handle_failure(job["id"], e)
```

- [ ] **Step 6.2.4: Rodar suite**

```bash
pytest tests/ -x --tb=short
```

### Task 6.3: Commit Etapa 6

```bash
git add orchestrator/errors.py tests/test_errors.py tests/test_job_queue.py \
        orchestrator/services/job_queue.py orchestrator/main.py
git commit -m "feat(reliability): typed errors — Retryable vs Fatal

Antes: 'except Exception' generico re-langava para job queue, que
contava retry para qualquer erro. Bug de programacao (KeyError,
JSONDecodeError) causava retry indefinido, queimando quota LLM e
poluindo a tabela jobs.

Agora: classify_exception() mapeia para RetryableError ou FatalError
baseado em tipo (httpx, googleapiclient, asyncpg, json, asyncio).
Job queue respeita o tipo:
- RetryableError -> mark_failed (incrementa retry counter)
- FatalError -> mark_failed_permanently (status='failed' imediato)

Default conservador para erros nao reconhecidos: Fatal.
"
```

---

## Validação final do PR

- [ ] **Step F.1: Suite completa verde**

```bash
pytest tests/ -v --tb=short
```

Esperado: 100% PASS.

- [ ] **Step F.2: Lint / type check (se configurado)**

```bash
# Se houver:
ruff check orchestrator/
mypy orchestrator/ 2>&1 | head -20
```

- [ ] **Step F.3: Verificar histórico**

```bash
git log master..HEAD --oneline
```

Esperado: 6 commits, um por etapa, mensagens claras.

- [ ] **Step F.4: Aplicar migration localmente para validar**

```bash
psql $DATABASE_URL -f sql/migrations/009_decisions_no_reply_detected.sql
psql $DATABASE_URL -c "\d decisions" | grep no_reply
```

Esperado: coluna `no_reply_detected` existe com default false.

- [ ] **Step F.5: Smoke test manual (opcional, recomendado)**

Subir o orchestrator local, mandar um webhook de teste para um email que vem de `noreply@github.com`:

```bash
# (instruções específicas dependem do setup local)
# verificar nos logs:
# - "[<id>] Acao=rascunho — texto enviado ao Telegram, sem draft no Gmail" não aparece
# - "no-reply detected" / categoria não-respondível aparece
# - Telegram recebe notificação SEM rascunho
# - Gmail conta NÃO ganha draft novo
```

- [ ] **Step F.6: Push e abrir PR**

```bash
git push -u origin feat/code-quality-improvements
gh pr create --title "feat: code-quality improvements (6 etapas)" --body "$(cat <<'EOF'
## Summary
Consolida 6 melhorias em 1 PR (1 commit por etapa, todas testadas):

1. Remove criação de rascunho no Gmail
2. Detecta emails não-respondíveis (no-reply, newsletter, transacional)
3. Redaction de payload sensível em logs do webhook
4. Refactor de `process_email()` (582 → ~50 linhas, 6 métodos privados)
5. Workers de background com backoff/timeout/métricas
6. Erros tipados (Retryable vs Fatal) no job queue

Spec: `docs/superpowers/specs/2026-04-27-code-quality-improvements-design.md`

## Test plan
- [ ] Suite completa passa (`pytest tests/ -v`)
- [ ] Migration 009 roda limpa em DB existente
- [ ] Smoke test: email de `noreply@*` não gera draft no Gmail
- [ ] Smoke test: email com categoria `newsletter` gera notificação sem rascunho
- [ ] Webhook recebido: log mostra `<REDACTED>` no lugar de tokens
- [ ] Worker simulado com erro: backoff visível em log

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notas finais

- **Cada etapa = 1 commit no PR final.** Se durante a Etapa 4 (refactor) você gerou commits intermediários por método, faça `git rebase -i` para squash em 1 só antes do push (Step 4.7.2).
- **Suite de testes deve passar após CADA commit** — se quebrar entre etapas, parar e diagnosticar antes de seguir.
- **Migration 009 precisa rodar antes do deploy.** Em ambiente de prod, aplicar o SQL manualmente ou via tool de migração existente (verificar como migrations são aplicadas no projeto).
- **Rollback granular:** se algo quebrar em produção, `git revert <sha-da-etapa>` reverte só aquela etapa sem mexer nas outras.
- Se durante a execução surgir uma decisão técnica não coberta no spec, parar e perguntar — não improvise.
