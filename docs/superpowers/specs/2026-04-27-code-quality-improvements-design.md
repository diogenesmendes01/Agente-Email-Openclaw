# Melhorias de Qualidade do Código — Design Spec

**Data:** 2026-04-27
**Status:** Aguardando aprovação do usuário
**Escopo:** Remover criação de rascunho no Gmail + detecção de emails não-respondíveis + 4 melhorias prioritárias de qualidade
**Branch base:** `master`

---

## 1. Contexto

Após varredura no código atual em `master`, identificamos:

1. Um comportamento que precisa ser desligado (criação de rascunho dentro da conta Gmail do usuário)
2. Necessidade de **detectar emails que não pedem/permitem resposta** e suprimir geração de rascunho nesses casos
3. Quatro alvos de **alto impacto** em segurança, arquitetura e confiabilidade

Cada item será entregue em **PR separado** para reduzir risco e facilitar revisão.

---

## 2. PR-1 — Remover criação de rascunho no Gmail

### 2.1. Problema
Hoje, quando o LLM decide ação `rascunho`, o sistema chama `gmail.create_draft()` e cria um draft **dentro da conta Gmail do usuário** (visível em "Rascunhos" do Gmail). Isso polui a caixa de rascunhos do usuário e duplica o conteúdo que já chega no Telegram.

### 2.2. Causa
- [`orchestrator/handlers/email_processor.py:492-501`](orchestrator/handlers/email_processor.py#L492) chama `self.gmail.create_draft(...)` no ramo `acao == "rascunho"`
- [`orchestrator/services/gmail_service.py:206-236`](orchestrator/services/gmail_service.py#L206) implementa o método que chama `service.users().drafts().create(...)`

### 2.3. Solução
1. Remover o ramo `elif acao == "rascunho":` em `email_processor.py` (linhas 492-501) — o texto do `rascunho_resposta` já é enviado ao Telegram pelo fluxo de notificação
2. Remover o método `create_draft()` de `gmail_service.py` (linhas 206-236)
3. Remover testes que dependam exclusivamente desse método (manter testes de notificação)
4. O fluxo de envio (`actions/reply.py` → `gmail.send_reply`) **não muda** — usuário ainda pode mandar a resposta via botão "✉️ Enviar" no Telegram

### 2.4. Critério de aceite
- [ ] `grep` por `create_draft` no `orchestrator/` retorna 0 ocorrências
- [ ] Email com `acao == "rascunho"` ainda gera notificação Telegram com o texto do rascunho
- [ ] Botão "✉️ Enviar" no Telegram ainda envia resposta corretamente
- [ ] Nenhum draft é criado na conta Gmail durante o teste end-to-end

### 2.5. Risco
**Baixo.** Mudança isolada em 2 arquivos. Sem migração, sem mudança de schema, sem alteração de API externa.

---

## 3. PR-2 — Detectar emails não-respondíveis e suprimir rascunho

### 3.1. Problema
Hoje o LLM pode escolher `acao = "rascunho"` para qualquer email, incluindo:
- **Tecnicamente irrespondíveis**: senders como `noreply@`, `no-reply@`, `donotreply@`, `mailer-daemon@`, `notifications@`, `alerts@` — responder não chega a ninguém
- **Sem contexto de resposta**: newsletters, promoções, recibos automáticos, confirmações de transação, status de envio, alertas de sistema

Resultado: rascunhos inúteis são gerados, queimando tokens de LLM e poluindo o Telegram.

### 3.2. Solução — defesa em camadas

**Camada A — Detecção determinística por sender (regex, pré-LLM)**
- Novo módulo `orchestrator/utils/reply_policy.py` com função `is_no_reply_sender(from_email: str) -> bool`
- Regex case-insensitive contra: `noreply`, `no-reply`, `no_reply`, `donotreply`, `do-not-reply`, `mailer-daemon`, `postmaster`, `bounce`, `notifications?`, `alerts?`, `news@`, `newsletter@`, `automated@`, `system@`, `info@.*\.(noreply|automated)`
- Se match → marca email como `no_reply_sender = True` no contexto, antes de chamar o LLM de ação

**Camada B — Categoria explícita no classifier**
- Adicionar categorias: `notificacao_automatica`, `transacional` (já existem `newsletter`, `promocao`)
- Atualizar o prompt do classifier (camada 2 em `llm_service.py`) para distinguir essas categorias

**Camada C — Prompt de ação ciente de irrespondibilidade**
- Se `no_reply_sender = True` OU `categoria` em `{newsletter, promocao, notificacao_automatica, transacional}`:
  - Substituir o prompt de ação por uma versão restrita: ações permitidas = `{"notificar", "arquivar", "criar_task"}` — `rascunho` é REMOVIDO da lista
  - Economia: também pode pular o prompt de ação inteiramente para no-reply senders e forçar `arquivar` direto (configurável)

**Camada D — Validação pós-LLM**
- Em `llm_validator.py`, adicionar regra: se sender é no-reply OU categoria é não-respondível, e LLM ainda retornou `acao = "rascunho"`, **rebaixa para `notificar`** e remove `rascunho_resposta`. Loga flag `rascunho_em_no_reply` na tabela `llm_quality_log`

### 3.3. Mudanças de código

| Arquivo | Mudança |
|---------|---------|
| `orchestrator/utils/reply_policy.py` (novo) | Regex + função `is_no_reply_sender` + lista de categorias não-respondíveis |
| `orchestrator/handlers/email_processor.py` | Chamar `is_no_reply_sender` antes do prompt de ação; passar flag pro prompt |
| `orchestrator/services/llm_service.py` | Prompt de ação condicional (com/sem opção rascunho); novas categorias |
| `orchestrator/services/llm_validator.py` | Regra de rebaixamento + flag `rascunho_em_no_reply` |
| `orchestrator/services/prompt_builder.py` | Suporte a variante "no-reply" no template de ação |
| Migration nova | (opcional) coluna `no_reply_detected BOOLEAN` em `decisions` para auditoria |

### 3.4. Critério de aceite
- [ ] Email de `noreply@github.com` nunca gera `acao = "rascunho"` (verificado por teste e2e)
- [ ] Email com `categoria = "newsletter"` nunca gera rascunho
- [ ] Função `is_no_reply_sender` cobre 12+ padrões com testes unitários
- [ ] Flag `rascunho_em_no_reply` aparece em `llm_quality_log` quando rebaixamento acontece
- [ ] Métrica `reply_policy_decisions_total{outcome}` (allowed / blocked_sender / blocked_category)

### 3.5. Configurabilidade
- Lista de regex de no-reply em arquivo de config (não hardcoded), permitindo ajuste sem deploy
- Flag por conta (futuro): `auto_archive_no_reply: bool` para arquivar automaticamente em vez de só notificar

### 3.6. Risco
**Médio-baixo.** Mudança comportamental do LLM, mas com fallback determinístico (camada A) que é independente do LLM. Mitigado por testes específicos para cada camada.

---

## 4. PR-3 — Redaction de payload sensível em logs do webhook

### 3.1. Problema
[`orchestrator/main.py:267`](orchestrator/main.py#L267) loga `json.dumps(body)[:500]` do webhook recebido. Se o payload contém token de query param, header `Authorization`, ou qualquer credencial, ela vai para o arquivo de log / stdout em texto plano. Risco de **credential leak** se logs forem compartilhados.

### 3.2. Solução
- Criar função `redact_sensitive(payload: dict) -> dict` em `orchestrator/utils/log_redaction.py`
- Lista de chaves sensíveis (case-insensitive): `token`, `authorization`, `password`, `secret`, `api_key`, `access_token`, `refresh_token`, `cookie`
- Substituir valor por `"<REDACTED>"` antes de logar
- Aplicar em **todos** os pontos onde body do webhook ou response externa é logado

### 3.3. Critério de aceite
- [ ] Função `redact_sensitive` cobre as 8+ chaves listadas
- [ ] Teste unitário cobre dict aninhado e lista
- [ ] Nenhum log no `main.py` ou `gmail_service.py` ou `telegram_service.py` registra payload sem redaction

### 3.4. Risco
**Baixo.** Função pura, fácil de testar.

---

## 5. PR-4 — Refactor de `process_email()` (582 linhas → ~6 métodos)

### 5.1. Problema
[`orchestrator/handlers/email_processor.py:56-638`](orchestrator/handlers/email_processor.py#L56) tem 582 linhas em uma única função. Orquestra 10+ etapas (fetch, parse, contexto, embedding, classificar, resumir, decidir ação, persistência, notificação, learning). Difícil de testar, difícil de debugar, alto risco de regressão.

### 5.2. Solução
Quebrar em métodos privados, mantendo a função pública `process_email()` como **orquestradora curta** (<60 linhas):

| Método | Responsabilidade |
|--------|------------------|
| `_fetch_and_parse(email_id, account)` | Buscar Gmail + extrair body + anexos |
| `_build_context(email)` | Thread context + sender profile + similares |
| `_classify_and_summarize(email, context)` | Chamadas LLM camadas 1+2 |
| `_decide_action(email, classification, summary)` | Chamada LLM camada 3 |
| `_execute_action(email, action, account)` | Arquivar / criar task / etc. |
| `_persist_and_notify(email, decision, account)` | DB + Qdrant + Telegram |

Cada método é testável isoladamente com mocks. Sem mudança de comportamento — refactor puro.

### 5.3. Critério de aceite
- [ ] `process_email()` tem <60 linhas
- [ ] Cada método novo tem teste unitário
- [ ] Suite de testes existente passa sem alteração
- [ ] Cobertura de `email_processor.py` não diminui

### 5.4. Risco
**Médio.** Refactor grande, mas sem mudança de comportamento. Mitigado por suite de testes existente + commits incrementais por método.

---

## 6. PR-5 — Workers de background resilientes

### 6.1. Problema
[`orchestrator/main.py:125-170`](orchestrator/main.py#L125) tem 3 workers (`retry_worker`, `maintenance_worker`, `cleanup_pending_worker`) com:
- `except Exception` que loga e continua tight loop
- Sem backoff em caso de erro persistente (ex: DB caiu)
- Sem timeout por iteração
- Sem `request_id` no contexto de log → impossível correlacionar com fluxo principal
- Sem métrica de "worker iterations" / "worker failures"

Em produção, se o DB ficar indisponível por 5 min, os 3 workers fazem milhares de iterações falhando, queimando CPU e poluindo logs.

### 6.2. Solução
1. Função utilitária `run_resilient_worker(name, fn, interval, max_backoff=300)` em `orchestrator/utils/worker.py`
2. Backoff exponencial em caso de erro (1s → 2s → 4s → ... → 300s)
3. Reset do backoff após N iterações bem-sucedidas
4. Cada worker gera novo `request_id` por iteração e injeta no ContextVar
5. Métricas: `worker_iteration_total{name, status}`, `worker_iteration_duration_seconds`
6. Timeout de iteração via `asyncio.wait_for(fn(), timeout=interval * 5)`

### 6.3. Critério de aceite
- [ ] Erro forçado em worker faz backoff visível em log
- [ ] Cada log de worker inclui `request_id`
- [ ] Métricas Prometheus expõem contagem de iterações com sucesso/falha
- [ ] Teste unitário simula falha persistente e verifica que backoff atinge teto

### 6.4. Risco
**Médio.** Mudança no lifecycle de processo. Mitigado por teste de integração + rollout gradual.

---

## 7. PR-6 — Discriminar erros retryable vs fatais

### 7.1. Problema
[`orchestrator/handlers/email_processor.py:446-464`](orchestrator/handlers/email_processor.py#L446) usa `except Exception as e` no final e re-lança para a job queue. Resultado: erro de programação (KeyError, JSON parse) faz o job entrar em retry indefinido, queimando quota de LLM e poluindo a tabela `jobs`.

### 7.2. Solução
1. Definir hierarquia de exceções em `orchestrator/errors.py`:
   - `RetryableError` (network, rate limit, timeout, 5xx) → job queue retenta
   - `FatalError` (parse, validation, auth) → marca job como `failed`, sem retry
2. Em pontos críticos (LLM, Gmail, DB), converter exceção genérica para o tipo correto
3. Job queue checa o tipo e decide entre `mark_failed` e `mark_retry`

### 7.3. Critério de aceite
- [ ] `JSONDecodeError` em resposta LLM marca job como `failed`, não como `retry`
- [ ] `httpx.TimeoutError` em chamada Gmail marca como `retry`
- [ ] Métrica `jobs_terminated_total{reason}` separa fatal vs retry vs success

### 7.4. Risco
**Médio-baixo.** Mudança comportamental clara. Mitigado por testes de cenários específicos.

---

## 8. Ordem de execução recomendada

```
PR-1 (rascunho do Gmail)       → 30 min, baixo risco, isolado
   ↓
PR-2 (detecção no-reply)       → comportamento de IA, médio-baixo risco
   ↓
PR-3 (log redaction)           → segurança ALTA, antes de mexer no resto
   ↓
PR-4 (refactor process_email)  → maior, destrava facilidade dos próximos
   ↓
PR-5 (workers resilientes)     → independente do PR-4
   ↓
PR-6 (erros tipados)           → depende parcialmente do PR-4 (pontos de catch ficam mais claros)
```

PR-1 e PR-2 podem ir lado a lado: ambos mexem no fluxo de rascunho mas em pontos diferentes. PR-1 remove o save no Gmail; PR-2 evita gerar rascunho desnecessário em primeiro lugar.

Cada PR tem seu próprio branch a partir do `master`, seu próprio plano de implementação, e fecha sozinho.

---

## 9. Fora de escopo

Para evitar bloat e manter foco:
- **Caching de embeddings** (item ALTO da varredura) — fica para PR futuro após PR-4 estabilizar
- **Refactor de `telegram_commands.py`** (1017 linhas) — escopo separado
- **Schema central de settings (Pydantic)** — DX, mas não urgente
- **Métricas detalhadas de learning engine** — depende de definição de SLO primeiro
- **Detecção de "thread já respondida"** como motivo de não-rascunho — já existe parcialmente em `llm_service.py:590`, sem mudanças neste spec

Esses ficam como TODOs em backlog, fora deste spec.

---

## 10. Próximos passos

1. Aprovação do usuário neste spec
2. Spec review automatizada (subagent)
3. Geração do plano de implementação detalhado (1 plano por PR, começando pelo PR-1)
4. Execução incremental com revisão a cada PR
