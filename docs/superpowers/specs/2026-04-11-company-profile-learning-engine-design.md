# Company Profile + Learning Engine - Design Spec

## Problem

The email agent works well for personal emails but lacks context for business use:
- No company identity (name, tone, signature) for drafting replies
- No client database to recognize contacts and their projects
- No domain-level rules (e.g., @bank.com = financial, high priority)
- Feedback from reclassifications is saved but never used to improve future classifications

## Solution: Hybrid Architecture

Manual data (company profiles, clients, domain rules) lives in Notion for easy editing.
Automatic data (learned rules, sender profiles) lives in Qdrant where the ML data already exists.

## Architecture

```
                         +---------------------+
                         |  Notion (manual)    |
                         |  - Company Profiles |
                         |  - Clientes         |
                         |  - Domain Rules     |
                         +---------+-----------+
                                   |
Email arrives -> Orchestrator -----+
                                   |
                         +---------+-----------+
                         |  Qdrant (automatic) |
                         |  - Emails + feedback|
                         |  - Sender profiles  |
                         |  - Learned rules    |
                         +---------+-----------+
                                   |
                                   v
                    +-----------------------------+
                    |  LLM (enriched prompts)     |
                    |  + company context           |
                    |  + few-shot from feedback    |
                    |  + sender profile            |
                    |  + learned rules             |
                    +-----------------------------+
```

## Component 1: Company Profiles in Notion

### Database: "Company Profiles"

Each email account links to one company profile.

| Property | Type | Example |
|---|---|---|
| Nome | Title | "Mendes Consultoria" |
| Conta Email | Email | diogenes@empresa.com |
| Setor | Select | Tecnologia / Advocacia / Saude |
| Tom | Select | formal / profissional / casual |
| Assinatura | Rich Text | "Att, Diogenes Mendes\nMendes Consultoria" |
| Idioma Padrao | Select | pt-BR / en-US |

### Database: "Clientes"

Linked to company profile via Relation.

| Property | Type | Example |
|---|---|---|
| Nome | Title | "XYZ Corp" |
| Company Profile | Relation | -> Mendes Consultoria |
| Contatos | Rich Text | "joao@xyz.com, maria@xyz.com" |
| Projeto Ativo | Text | "Migracao Cloud" |
| Prioridade | Select | Alta / Media / Baixa |
| Notas | Rich Text | "Prazo final: junho 2026" |

### Database: "Domain Rules"

Manual rules per email domain.

| Property | Type | Example |
|---|---|---|
| Dominio | Title | "@pagar.me" |
| Company Profile | Relation | -> Mendes Consultoria |
| Categoria | Select | financeiro |
| Prioridade Minima | Select | Alta |
| Acao Padrao | Select | notificar / arquivar / criar_task |

### New service: CompanyService

File: `orchestrator/services/company_service.py`

Fetches all three databases and returns a unified dict:

```python
{
    "nome": "Mendes Consultoria",
    "setor": "Tecnologia",
    "tom": "profissional",
    "assinatura": "Att, Diogenes Mendes\n...",
    "idioma": "pt-BR",
    "clientes": [
        {
            "nome": "XYZ Corp",
            "contatos": ["joao@xyz.com", "maria@xyz.com"],
            "projeto": "Migracao Cloud",
            "prioridade": "alta"
        }
    ],
    "domain_rules": [
        {
            "dominio": "@pagar.me",
            "categoria": "financeiro",
            "prioridade_minima": "Alta",
            "acao_padrao": "notificar"
        }
    ]
}
```

**Caching**: In-memory TTL cache of 5 minutes per account. Company profiles change infrequently, and fetching 3 Notion databases per email risks hitting Notion rate limits (3 req/sec). Cache invalidates after TTL or on explicit refresh.

```python
_cache: Dict[str, Tuple[float, Dict]] = {}  # {account: (timestamp, profile)}
CACHE_TTL = 300  # 5 minutes
```

Env vars for database IDs:
- `NOTION_DB_COMPANY_PROFILES`
- `NOTION_DB_CLIENTES`
- `NOTION_DB_DOMAIN_RULES`

### Domain rule matching

- Domain extracted from sender via `sender.split("@")[1]` after `@`
- Subdomains match parent: `user@sub.pagar.me` matches rule `@pagar.me`
- **Precedence order** (first match wins):
  1. Manual Notion domain rules (explicit user configuration)
  2. Learned Qdrant rules (automatic, can be overridden by manual)
  3. Default classification from LLM

## Component 2: Learning Engine

### Feedback data unification

**Current state**: feedback is split across two systems:
- `telegram_poller.py` saves reclassifications to `feedback.json` with structured data (original_urgency, corrected_urgency)
- `qdrant_service.py` stores only simple strings ("pendente", "Correto", "Incorreto")

**Required change**: Update `qdrant_service.update_feedback()` to accept structured correction data. Update `telegram_poller.py` to write corrections to Qdrant instead of (or in addition to) `feedback.json`.

New Qdrant feedback payload fields on email points:

```python
{
    # existing fields...
    "feedback": "corrected",           # "pendente" | "confirmed" | "corrected"
    "feedback_original_priority": "Media",
    "feedback_corrected_priority": "Alta",
    "feedback_original_category": "outro",
    "feedback_corrected_category": "cliente",
    "feedback_date": "2026-04-11"
}
```

`feedback.json` remains as a backup log but is not the primary source for the learning engine.

### Layer 1: Few-shot learning from feedback (per email, in prompt)

Similar emails already come from Qdrant. Change: include their structured feedback in the classification prompt.

Prompt addition:
```
EMAILS SIMILARES (com feedback do usuario):
1. De: joao@xyz.com | Assunto: "Renovacao contrato"
   Sua classificacao: Media -> Usuario corrigiu para: Alta
2. De: maria@xyz.com | Assunto: "Prazo entrega"
   Sua classificacao: Alta -> Usuario confirmou
```

Implementation: modify `_build_classifier_prompt()` in `llm_service.py` to format similar emails with their feedback fields from Qdrant payload.

### Layer 2: Sender profiling (per email, in prompt)

`get_sender_profile()` already exists in `qdrant_service.py` but is never called.

**Changes needed to `get_sender_profile()`**:
- Return correction direction patterns (e.g., "3 corrections Media->Alta"), not just counts
- Return last N correction details for the prompt

Enhanced return:
```python
{
    "count": 15,
    "important_count": 12,
    "important_rate": 0.8,
    "correct_rate": 0.75,
    "correction_patterns": [
        {"from": "Media", "to": "Alta", "count": 3},
        {"from": "Baixa", "to": "Media", "count": 1}
    ],
    "last_email": "2026-04-08T...",
    "is_client": True,                    # cross-ref with company profile
    "client_name": "XYZ Corp",            # if is_client
    "client_project": "Migracao Cloud"    # if is_client
}
```

`email_processor.py` calls it and cross-references with company profile clients.

### Layer 3: Learned rules (every N emails, automatic)

New file: `orchestrator/services/learning_engine.py`

New Qdrant collection: `learned_rules` with **vector size 1** (minimal dummy vector `[0.0]` since rules are retrieved by filter, not by similarity search).

#### Rule format

```python
{
    "rule_type": "sender" | "domain" | "keyword",
    "match": "joao@xyz.com" | "@pagar.me" | "urgente",
    "account": "diogenes@empresa.com",
    "action": "priority_override" | "category_override" | "action_override",
    "value": "Alta",
    "confidence": 0.85,
    "evidence_count": 5,
    "created_at": "2026-04-11",
    "last_updated": "2026-04-11"
}
```

#### Learning trigger and counter persistence

Configurable interval via env var `LEARNING_INTERVAL` (default: 50).

The counter is persisted as a point in the `learned_rules` collection itself (special point with `rule_type: "_counter"`). On startup, `EmailProcessor` reads the counter from Qdrant. If Qdrant is unavailable, counter starts at 0 and re-syncs when Qdrant comes back.

```python
# In EmailProcessor.__init__
self._emails_processed = await self.qdrant.get_learning_counter(account) or 0
self._learning_interval = int(os.getenv("LEARNING_INTERVAL", "50"))
```

#### Learning algorithm

Steps:
1. **Fetch** emails with structured feedback from Qdrant using **paginated scroll** (loop with offset until no more results, batch size 100)
2. **Group** by sender email and by domain (extracted via `split("@")[1]`)
3. **Sender/domain rules**: for each group with >= 3 corrections in the same direction, create/update rule with `confidence = consistent_corrections / total_corrections`
4. **Keyword rules**: extract subject words from corrected emails. Filter with:
   - Minimum word length: 4 characters
   - Portuguese stopword list (de, para, que, com, uma, etc.)
   - Only words appearing in >= 3 corrected emails AND in < 20% of confirmed-correct emails
   - TF-IDF not needed at this scale; simple frequency filtering suffices
5. **Store** rules in `learned_rules` collection (upsert by `rule_type + match + account`)
6. **Auto-delete** rules where confidence dropped below 0.5
7. **Persist** updated counter
8. **Notify** via Telegram: "Aprendi N regras novas: [summary]"

#### Thresholds

- Minimum evidence to create rule: 3 corrections
- Minimum confidence to apply rule: 0.7 (70%)
- Rules with confidence < 0.5 are auto-deleted
- Rules are re-evaluated on each learning cycle

## Component 3: Enriched Prompts

### Prompt size management

Maximum total prompt size: **6000 tokens** (leaves room for LLM response within context window).

Truncation priority (if prompt exceeds limit, trim from bottom of list first):

1. Company context + domain rules (~200 tokens) - always included
2. Learned rules (~150 tokens, max 10 rules) - always included
3. Sender profile (~100 tokens) - always included
4. Similar emails with feedback (max 3 emails, ~300 tokens) - trimmed first if needed
5. Email body (already truncated to 1500 chars) - further truncated if needed
6. Thread context (already max 2 messages) - dropped if needed

### Classification prompt additions

Current prompt gets these new sections (in order):

1. **Company context** (from CompanyService)
2. **Domain rules** (manual, from Notion)
3. **Learned rules** (automatic, from Qdrant)
4. **Sender profile** (from Qdrant + client cross-reference)
5. **Similar emails with feedback** (from Qdrant, already fetched)

### Action/draft prompt additions

1. **Company tone and signature** for draft generation
2. **Client context** if sender is a known client contact
3. **Idioma padrao** of the company

### Method signature

All new context is added to the existing `context` dict passed to LLM methods. No signature changes needed:

```python
context = {
    # existing
    "vips": [...],
    "urgency_words": [...],
    "ignore_words": [...],
    "similar_emails": [...],
    "thread_context": [...],
    # new
    "company_profile": {...},
    "sender_profile": {...},
    "learned_rules": [...],
    "domain_rules": [...]
}
```

## Modified files

| File | Changes |
|---|---|
| `orchestrator/services/company_service.py` | NEW - fetches company profile, clients, domain rules from Notion with TTL cache |
| `orchestrator/services/learning_engine.py` | NEW - analyzes feedback, generates rules, stores in Qdrant |
| `orchestrator/services/qdrant_service.py` | Add `learned_rules` collection (vector size 1), `get_learned_rules()`, `store_rules()`, `get_learning_counter()`, paginated scroll, enhanced `update_feedback()` with structured data |
| `orchestrator/services/llm_service.py` | Enrich all 3 prompts with company context, feedback, sender profile, learned rules. Prompt size management. |
| `orchestrator/handlers/email_processor.py` | Fetch company profile, sender profile, learned rules; pass to LLM; trigger learning every N emails |
| `telegram_poller.py` | Update reclassification to write structured feedback to Qdrant |
| `.env.example` | Add NOTION_DB_COMPANY_PROFILES, NOTION_DB_CLIENTES, NOTION_DB_DOMAIN_RULES, LEARNING_INTERVAL |

## Error handling

- If Notion company profile not found: proceed without company context (same as today)
- If Qdrant learned_rules collection empty: proceed without rules (no degradation)
- If learning engine fails: log error, don't block email processing
- If sender profile fetch fails: proceed without it
- If CompanyService cache fails: fetch fresh from Notion (no cache)
- If learning counter lost: restart from 0, next cycle recalculates all rules from full history

## Migration plan

Existing data transition:
1. New `learned_rules` collection is created empty on first run (auto by `_ensure_collections`)
2. Existing emails in Qdrant have `feedback: "pendente"` - this is fine, learning engine ignores them
3. Existing `feedback.json` entries: a one-time migration script reads `feedback.json` and calls the new `update_feedback()` for each entry to backfill structured data into Qdrant. Script: `scripts/migrate_feedback.py`
4. After migration, `feedback.json` continues as backup log but is not primary source

## Data flow summary

```
Email arrives
  |
  +-> CompanyService.get_profile(account)        [Notion, cached 5min]
  +-> QdrantService.get_sender_profile(sender)   [Qdrant]
  +-> QdrantService.get_learned_rules(account)   [Qdrant]
  +-> QdrantService.search_similar(embedding)    [Qdrant, already exists]
  |
  v
LLM.classify(email, context + company + sender_profile + rules + similar_with_feedback)
  |
  v
LLM.summarize(email, classification)
  |
  v
LLM.decide_action(email, classification, summary, config + company)
  |
  v
Process result, save to Notion + Qdrant
  |
  +-> emails_processed counter++
  +-> if counter % N == 0: LearningEngine.analyze_and_learn(account)
```
