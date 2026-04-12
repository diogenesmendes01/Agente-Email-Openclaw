# Company Profile + Learning Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add company profile support (Notion) and a 3-layer learning engine (Qdrant) so the email agent understands business context and improves from user feedback.

**Architecture:** Manual data (company profiles, clients, domain rules) in Notion with TTL cache. Automatic data (learned rules, sender profiles, feedback) in Qdrant. Both are injected into LLM prompts via enriched context dict in `email_processor.py`.

**Tech Stack:** Python 3.11, FastAPI, httpx (async Notion API), qdrant-client, OpenAI embeddings, OpenRouter LLM

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `orchestrator/services/company_service.py` | CREATE | Fetches company profiles, clients, domain rules from Notion with TTL cache |
| `orchestrator/services/learning_engine.py` | CREATE | Analyzes feedback patterns, generates rules, stores in Qdrant |
| `orchestrator/services/qdrant_service.py` | MODIFY | Add `learned_rules` collection, structured feedback, paginated scroll, enhanced sender profile, learning counter |
| `orchestrator/services/llm_service.py` | MODIFY | Enrich all 3 prompts with company context, sender profile, learned rules, feedback examples. Prompt size management. |
| `orchestrator/handlers/email_processor.py` | MODIFY | Fetch company profile + sender profile + learned rules; pass to LLM; trigger learning every N emails |
| `telegram_poller.py` | MODIFY | Write structured feedback to Qdrant instead of only feedback.json |
| `orchestrator/main.py` | MODIFY | Initialize CompanyService, pass to EmailProcessor |
| `.env.example` | MODIFY | Add new env vars |
| `scripts/migrate_feedback.py` | CREATE | One-time migration of feedback.json to Qdrant structured format |
| `tests/test_company_service.py` | CREATE | Unit tests for CompanyService |
| `tests/test_learning_engine.py` | CREATE | Unit tests for LearningEngine |
| `tests/test_qdrant_extensions.py` | CREATE | Tests for new Qdrant methods |
| `tests/test_enriched_prompts.py` | CREATE | Tests for prompt enrichment in LLMService |
| `tests/__init__.py` | CREATE | Makes tests a Python package for pytest discovery |
| `tests/conftest.py` | CREATE | pytest-asyncio configuration |

---

## Task 0: Create test infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `scripts/` directory

- [ ] **Step 1: Create directories and test infrastructure**

```bash
mkdir -p tests scripts
touch tests/__init__.py
pip install pytest pytest-asyncio
```

Create `tests/conftest.py`:

```python
"""pytest configuration for async tests"""
import pytest

pytest_plugins = ['pytest_asyncio']
```

- [ ] **Step 2: Verify pytest discovers the test directory**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/ --collect-only 2>&1 | head -5`
Expected: "no tests ran" (empty collection, no errors)

- [ ] **Step 3: Commit**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "chore: add test infrastructure for pytest with async support"
```

---

## Task 1: Extend QdrantService - learned_rules collection and structured feedback

**Files:**
- Modify: `orchestrator/services/qdrant_service.py`
- Test: `tests/test_qdrant_extensions.py`

This task adds the foundational Qdrant methods that all other components depend on.

- [ ] **Step 1: Write tests for new Qdrant methods**

Create `tests/test_qdrant_extensions.py`:

```python
"""Tests for Qdrant extensions: learned_rules collection, structured feedback, enhanced sender profile"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from orchestrator.services.qdrant_service import QdrantService


@pytest.fixture
def qdrant():
    """QdrantService with mocked client"""
    with patch("orchestrator.services.qdrant_service.QdrantClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        # Mock get_collection to not raise (collections exist)
        mock_client.get_collection.return_value = True
        svc = QdrantService()
        svc._connected = True
        svc.client = mock_client
        yield svc


class TestLearnedRulesCollection:
    def test_ensure_collections_creates_learned_rules(self, qdrant):
        """learned_rules collection should be created with vector size 1"""
        from qdrant_client.http.exceptions import UnexpectedResponse
        qdrant.client.get_collection.side_effect = UnexpectedResponse(
            status_code=404, reason_phrase="Not found", content=b""
        )
        qdrant._ensure_collections()
        # Should attempt to create all collections including learned_rules
        create_calls = qdrant.client.create_collection.call_args_list
        collection_names = [call.kwargs.get("collection_name", call.args[0] if call.args else None)
                           for call in create_calls]
        assert "learned_rules" in collection_names


class TestStructuredFeedback:
    @pytest.mark.asyncio
    async def test_update_feedback_structured(self, qdrant):
        """update_feedback should accept structured correction data"""
        mock_point = MagicMock()
        mock_point.vector = [0.1] * 1536
        mock_point.payload = {"email_id": "abc123", "account": "test@test.com"}
        qdrant.client.retrieve.return_value = [mock_point]

        result = await qdrant.update_feedback(
            email_id="abc123",
            feedback="corrected",
            original_priority="Media",
            corrected_priority="Alta",
            original_category="outro",
            corrected_category="cliente"
        )
        assert result is True
        upsert_call = qdrant.client.upsert.call_args
        payload = upsert_call.kwargs["points"][0].payload
        assert payload["feedback"] == "corrected"
        assert payload["feedback_original_priority"] == "Media"
        assert payload["feedback_corrected_priority"] == "Alta"


class TestEnhancedSenderProfile:
    @pytest.mark.asyncio
    async def test_sender_profile_includes_correction_patterns(self, qdrant):
        """get_sender_profile should return correction direction patterns"""
        mock_points = []
        for i in range(5):
            p = MagicMock()
            p.payload = {
                "from_email": "joao@xyz.com",
                "account": "test@test.com",
                "priority": "Media" if i < 3 else "Alta",
                "feedback": "corrected" if i < 3 else "confirmed",
                "feedback_original_priority": "Media" if i < 3 else None,
                "feedback_corrected_priority": "Alta" if i < 3 else None,
                "timestamp": f"2026-04-0{i+1}T10:00:00"
            }
            mock_points.append(p)
        qdrant.client.scroll.return_value = (mock_points, None)

        profile = await qdrant.get_sender_profile("joao@xyz.com", "test@test.com")
        assert profile["count"] == 5
        assert "correction_patterns" in profile
        assert len(profile["correction_patterns"]) > 0


class TestLearnedRulesCRUD:
    @pytest.mark.asyncio
    async def test_store_rules(self, qdrant):
        """store_rules should upsert rules into learned_rules collection"""
        rules = [
            {
                "rule_type": "sender",
                "match": "joao@xyz.com",
                "account": "test@test.com",
                "action": "priority_override",
                "value": "Alta",
                "confidence": 0.85,
                "evidence_count": 5,
            }
        ]
        result = await qdrant.store_rules(rules)
        assert result is True
        qdrant.client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_learned_rules(self, qdrant):
        """get_learned_rules should return rules filtered by account"""
        mock_point = MagicMock()
        mock_point.payload = {
            "rule_type": "sender",
            "match": "joao@xyz.com",
            "action": "priority_override",
            "value": "Alta",
            "confidence": 0.85,
        }
        qdrant.client.scroll.return_value = ([mock_point], None)

        rules = await qdrant.get_learned_rules("test@test.com")
        assert len(rules) == 1
        assert rules[0]["rule_type"] == "sender"


class TestLearningCounter:
    @pytest.mark.asyncio
    async def test_get_learning_counter(self, qdrant):
        """get_learning_counter should read counter from special point"""
        mock_point = MagicMock()
        mock_point.payload = {"rule_type": "_counter", "count": 42}
        qdrant.client.scroll.return_value = ([mock_point], None)

        count = await qdrant.get_learning_counter("test@test.com")
        assert count == 42

    @pytest.mark.asyncio
    async def test_update_learning_counter(self, qdrant):
        """update_learning_counter should upsert counter point"""
        result = await qdrant.update_learning_counter("test@test.com", 50)
        assert result is True
        qdrant.client.upsert.assert_called_once()


class TestPaginatedScrollFeedback:
    @pytest.mark.asyncio
    async def test_get_corrected_emails_paginates(self, qdrant):
        """get_corrected_emails should paginate through all results"""
        # First page returns 2 points + offset, second page returns empty
        p1 = MagicMock()
        p1.payload = {"feedback": "corrected", "from_email": "a@b.com"}
        p2 = MagicMock()
        p2.payload = {"feedback": "corrected", "from_email": "c@d.com"}
        qdrant.client.scroll.side_effect = [
            ([p1, p2], "offset_token"),
            ([], None)
        ]

        results = await qdrant.get_corrected_emails("test@test.com")
        assert len(results) == 2
        assert qdrant.client.scroll.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_qdrant_extensions.py -v`
Expected: FAIL (missing methods)

- [ ] **Step 3: Add COLLECTION_LEARNED_RULES constant and update _ensure_collections**

In `orchestrator/services/qdrant_service.py`, add after line 18 (`COLLECTION_PROFILES = "profiles"`):

```python
COLLECTION_LEARNED_RULES = "learned_rules"
```

Update `_ensure_collections()` to also create `learned_rules` with vector size 1:

```python
def _ensure_collections(self):
    """Cria collections se não existirem"""
    # Standard collections with full embeddings
    standard_collections = [self.COLLECTION_EMAILS, self.COLLECTION_THREADS, self.COLLECTION_PROFILES]
    for col in standard_collections:
        try:
            self.client.get_collection(col)
        except UnexpectedResponse:
            logger.info(f"Criando collection: {col}")
            self.client.create_collection(
                collection_name=col,
                vectors_config=models.VectorParams(
                    size=1536,
                    distance=models.Distance.COSINE
                )
            )

    # Learned rules collection - vector size 1 (retrieved by filter, not similarity)
    try:
        self.client.get_collection(self.COLLECTION_LEARNED_RULES)
    except UnexpectedResponse:
        logger.info(f"Criando collection: {self.COLLECTION_LEARNED_RULES}")
        self.client.create_collection(
            collection_name=self.COLLECTION_LEARNED_RULES,
            vectors_config=models.VectorParams(
                size=1,
                distance=models.Distance.COSINE
            )
        )
```

- [ ] **Step 4: Update update_feedback() to accept structured correction data**

Replace the existing `update_feedback` method (lines ~178-218) with:

```python
async def update_feedback(
    self,
    email_id: str,
    feedback: str,
    original_priority: str = None,
    corrected_priority: str = None,
    original_category: str = None,
    corrected_category: str = None
) -> bool:
    """
    Atualiza feedback de um email com dados estruturados de correção.

    Args:
        email_id: ID do email
        feedback: "pendente" | "confirmed" | "corrected"
        original_priority: Prioridade original (se corrected)
        corrected_priority: Prioridade corrigida (se corrected)
        original_category: Categoria original (se corrected)
        corrected_category: Categoria corrigida (se corrected)
    """
    if not self._connected:
        return False

    try:
        point = self.client.retrieve(
            collection_name=self.COLLECTION_EMAILS,
            ids=[email_id],
            with_payload=True,
            with_vectors=True
        )

        if not point:
            return False

        updated_payload = {
            **point[0].payload,
            "feedback": feedback,
            "feedback_date": datetime.utcnow().strftime("%Y-%m-%d")
        }

        if feedback == "corrected":
            if original_priority:
                updated_payload["feedback_original_priority"] = original_priority
            if corrected_priority:
                updated_payload["feedback_corrected_priority"] = corrected_priority
            if original_category:
                updated_payload["feedback_original_category"] = original_category
            if corrected_category:
                updated_payload["feedback_corrected_category"] = corrected_category

        self.client.upsert(
            collection_name=self.COLLECTION_EMAILS,
            points=[
                models.PointStruct(
                    id=email_id,
                    vector=point[0].vector,
                    payload=updated_payload
                )
            ]
        )

        logger.info(f"Feedback atualizado: {email_id} -> {feedback}")
        return True

    except Exception as e:
        logger.error(f"Erro ao atualizar feedback: {e}")
        return False
```

- [ ] **Step 5: Add enhanced get_sender_profile() with correction patterns**

Replace the existing `get_sender_profile` method (lines ~220-265) with:

```python
async def get_sender_profile(self, from_email: str, account: str) -> Dict[str, Any]:
    """
    Busca perfil de um remetente com padrões de correção.

    Returns:
        Dict with count, important_rate, correct_rate, correction_patterns, last_email
    """
    if not self._connected:
        return {}

    try:
        results = self.client.scroll(
            collection_name=self.COLLECTION_EMAILS,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="from_email",
                        match=models.MatchValue(value=from_email)
                    ),
                    models.FieldCondition(
                        key="account",
                        match=models.MatchValue(value=account)
                    )
                ]
            ),
            limit=100,
            with_payload=True
        )

        if not results[0]:
            return {"count": 0, "important_rate": 0}

        emails = results[0]
        total = len(emails)
        important = sum(1 for e in emails if e.payload.get("priority") in ["Alta", "high", "Crítico"])
        confirmed = sum(1 for e in emails if e.payload.get("feedback") == "confirmed")
        corrected = sum(1 for e in emails if e.payload.get("feedback") == "corrected")

        # Build correction patterns
        pattern_counts = {}
        for e in emails:
            if e.payload.get("feedback") == "corrected":
                orig = e.payload.get("feedback_original_priority", "?")
                corr = e.payload.get("feedback_corrected_priority", "?")
                key = f"{orig}->{corr}"
                pattern_counts[key] = pattern_counts.get(key, 0) + 1

        correction_patterns = [
            {"from": k.split("->")[0], "to": k.split("->")[1], "count": v}
            for k, v in pattern_counts.items()
        ]

        total_with_feedback = confirmed + corrected
        correct_rate = confirmed / total_with_feedback if total_with_feedback > 0 else 0

        return {
            "count": total,
            "important_count": important,
            "important_rate": important / total if total > 0 else 0,
            "correct_rate": correct_rate,
            "correction_patterns": correction_patterns,
            "last_email": emails[0].payload.get("timestamp") if emails else None
        }

    except Exception as e:
        logger.error(f"Erro ao buscar perfil do remetente: {e}")
        return {}
```

- [ ] **Step 6: Add store_rules(), get_learned_rules(), learning counter, and paginated scroll methods**

Add these methods at the end of the `QdrantService` class:

```python
async def store_rules(self, rules: List[Dict[str, Any]]) -> bool:
    """Stores learned rules in the learned_rules collection."""
    if not self._connected or not rules:
        return False

    try:
        import hashlib
        points = []
        for rule in rules:
            # Deterministic ID from rule_type + match + account
            id_str = f"{rule['rule_type']}:{rule['match']}:{rule['account']}"
            point_id = hashlib.md5(id_str.encode()).hexdigest()

            points.append(models.PointStruct(
                id=point_id,
                vector=[0.0],  # Dummy vector - rules are retrieved by filter
                payload={
                    **rule,
                    "last_updated": datetime.utcnow().isoformat()
                }
            ))

        self.client.upsert(
            collection_name=self.COLLECTION_LEARNED_RULES,
            points=points
        )
        logger.info(f"{len(rules)} regras armazenadas")
        return True

    except Exception as e:
        logger.error(f"Erro ao armazenar regras: {e}")
        return False

async def get_learned_rules(
    self, account: str, min_confidence: float = 0.7
) -> List[Dict[str, Any]]:
    """Returns learned rules for an account with minimum confidence."""
    if not self._connected:
        return []

    try:
        results = self.client.scroll(
            collection_name=self.COLLECTION_LEARNED_RULES,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="account",
                        match=models.MatchValue(value=account)
                    )
                ],
                must_not=[
                    models.FieldCondition(
                        key="rule_type",
                        match=models.MatchValue(value="_counter")
                    )
                ]
            ),
            limit=200,  # Fetch more than 10 so cleanup can see all rules
            with_payload=True
        )

        rules = [
            p.payload for p in results[0]
            if p.payload.get("confidence", 0) >= min_confidence
        ]
        return rules

    except Exception as e:
        logger.error(f"Erro ao buscar regras aprendidas: {e}")
        return []

async def delete_rules(self, rule_ids: List[str]) -> bool:
    """Deletes rules by their IDs."""
    if not self._connected or not rule_ids:
        return False

    try:
        self.client.delete(
            collection_name=self.COLLECTION_LEARNED_RULES,
            points_selector=models.PointIdsList(points=rule_ids)
        )
        logger.info(f"{len(rule_ids)} regras removidas")
        return True
    except Exception as e:
        logger.error(f"Erro ao remover regras: {e}")
        return False

async def get_learning_counter(self, account: str) -> int:
    """Reads the email processing counter from Qdrant."""
    if not self._connected:
        return 0

    try:
        results = self.client.scroll(
            collection_name=self.COLLECTION_LEARNED_RULES,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="rule_type",
                        match=models.MatchValue(value="_counter")
                    ),
                    models.FieldCondition(
                        key="account",
                        match=models.MatchValue(value=account)
                    )
                ]
            ),
            limit=1,
            with_payload=True
        )
        if results[0]:
            return results[0][0].payload.get("count", 0)
        return 0
    except Exception as e:
        logger.error(f"Erro ao ler counter: {e}")
        return 0

async def update_learning_counter(self, account: str, count: int) -> bool:
    """Persists the email processing counter in Qdrant."""
    if not self._connected:
        return False

    try:
        import hashlib
        point_id = hashlib.md5(f"_counter:{account}".encode()).hexdigest()

        self.client.upsert(
            collection_name=self.COLLECTION_LEARNED_RULES,
            points=[models.PointStruct(
                id=point_id,
                vector=[0.0],
                payload={
                    "rule_type": "_counter",
                    "account": account,
                    "count": count,
                    "last_updated": datetime.utcnow().isoformat()
                }
            )]
        )
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar counter: {e}")
        return False

async def get_corrected_emails(self, account: str) -> List[Dict[str, Any]]:
    """Fetches all corrected emails using paginated scroll."""
    if not self._connected:
        return []

    try:
        all_results = []
        offset = None

        while True:
            results, next_offset = self.client.scroll(
                collection_name=self.COLLECTION_EMAILS,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="account",
                            match=models.MatchValue(value=account)
                        ),
                        models.FieldCondition(
                            key="feedback",
                            match=models.MatchValue(value="corrected")
                        )
                    ]
                ),
                limit=100,
                offset=offset,
                with_payload=True
            )

            all_results.extend([p.payload for p in results])

            if not next_offset or not results:
                break
            offset = next_offset

        return all_results

    except Exception as e:
        logger.error(f"Erro ao buscar emails corrigidos: {e}")
        return []
```

- [ ] **Step 7: Add get_confirmed_emails() method**

Add this method after `get_corrected_emails` in `qdrant_service.py`:

```python
async def get_confirmed_emails(self, account: str) -> List[Dict[str, Any]]:
    """Fetches all confirmed-correct emails using paginated scroll."""
    if not self._connected:
        return []

    try:
        all_results = []
        offset = None

        while True:
            results, next_offset = self.client.scroll(
                collection_name=self.COLLECTION_EMAILS,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="account",
                            match=models.MatchValue(value=account)
                        ),
                        models.FieldCondition(
                            key="feedback",
                            match=models.MatchValue(value="confirmed")
                        )
                    ]
                ),
                limit=100,
                offset=offset,
                with_payload=True
            )

            all_results.extend([p.payload for p in results])

            if not next_offset or not results:
                break
            offset = next_offset

        return all_results

    except Exception as e:
        logger.error(f"Erro ao buscar emails confirmados: {e}")
        return []
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_qdrant_extensions.py -v`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add orchestrator/services/qdrant_service.py tests/test_qdrant_extensions.py
git commit -m "feat: extend QdrantService with learned_rules collection, structured feedback, enhanced sender profile"
```

---

## Task 2: Create CompanyService

**Files:**
- Create: `orchestrator/services/company_service.py`
- Test: `tests/test_company_service.py`

- [ ] **Step 1: Write tests for CompanyService**

Create `tests/test_company_service.py`:

```python
"""Tests for CompanyService - fetches company profiles, clients, domain rules from Notion"""
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from orchestrator.services.company_service import CompanyService


@pytest.fixture
def company_svc():
    with patch.dict("os.environ", {
        "NOTION_API_KEY": "test-key",
        "NOTION_DB_COMPANY_PROFILES": "db-profiles-id",
        "NOTION_DB_CLIENTES": "db-clientes-id",
        "NOTION_DB_DOMAIN_RULES": "db-rules-id",
    }):
        svc = CompanyService()
        yield svc


class TestGetProfile:
    @pytest.mark.asyncio
    async def test_returns_profile_for_known_account(self, company_svc):
        """Should return company profile with clients and domain rules"""
        mock_profile_response = {
            "results": [{
                "id": "page-1",
                "properties": {
                    "Nome": {"title": [{"text": {"content": "Mendes Consultoria"}}]},
                    "Conta Email": {"email": "diogenes@empresa.com"},
                    "Setor": {"select": {"name": "Tecnologia"}},
                    "Tom": {"select": {"name": "profissional"}},
                    "Assinatura": {"rich_text": [{"text": {"content": "Att, Diogenes"}}]},
                    "Idioma Padrao": {"select": {"name": "pt-BR"}},
                }
            }]
        }
        mock_clients_response = {
            "results": [{
                "properties": {
                    "Nome": {"title": [{"text": {"content": "XYZ Corp"}}]},
                    "Contatos": {"rich_text": [{"text": {"content": "joao@xyz.com, maria@xyz.com"}}]},
                    "Projeto Ativo": {"rich_text": [{"text": {"content": "Migracao Cloud"}}]},
                    "Prioridade": {"select": {"name": "Alta"}},
                    "Notas": {"rich_text": [{"text": {"content": "Prazo junho"}}]},
                    "Company Profile": {"relation": [{"id": "page-1"}]},
                }
            }]
        }
        mock_rules_response = {
            "results": [{
                "properties": {
                    "Dominio": {"title": [{"text": {"content": "@pagar.me"}}]},
                    "Categoria": {"select": {"name": "financeiro"}},
                    "Prioridade Minima": {"select": {"name": "Alta"}},
                    "Acao Padrao": {"select": {"name": "notificar"}},
                    "Company Profile": {"relation": [{"id": "page-1"}]},
                }
            }]
        }

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "db-profiles-id" in url:
                resp.json.return_value = mock_profile_response
            elif "db-clientes-id" in url:
                resp.json.return_value = mock_clients_response
            elif "db-rules-id" in url:
                resp.json.return_value = mock_rules_response
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            profile = await company_svc.get_profile("diogenes@empresa.com")

        assert profile["nome"] == "Mendes Consultoria"
        assert profile["tom"] == "profissional"
        assert len(profile["clientes"]) == 1
        assert profile["clientes"][0]["nome"] == "XYZ Corp"
        assert len(profile["domain_rules"]) == 1
        assert profile["domain_rules"][0]["dominio"] == "@pagar.me"

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_account(self, company_svc):
        """Should return empty dict if account not found"""
        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"results": []}
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            profile = await company_svc.get_profile("unknown@test.com")

        assert profile == {}

    @pytest.mark.asyncio
    async def test_cache_returns_same_result(self, company_svc):
        """Second call within TTL should return cached result without API call"""
        company_svc._cache["test@test.com"] = (time.time(), {"nome": "Cached"})
        profile = await company_svc.get_profile("test@test.com")
        assert profile["nome"] == "Cached"


class TestDomainMatching:
    def test_exact_domain_match(self, company_svc):
        rules = [{"dominio": "@pagar.me", "categoria": "financeiro"}]
        match = company_svc.match_domain_rule("user@pagar.me", rules)
        assert match is not None
        assert match["categoria"] == "financeiro"

    def test_subdomain_match(self, company_svc):
        rules = [{"dominio": "@pagar.me", "categoria": "financeiro"}]
        match = company_svc.match_domain_rule("user@sub.pagar.me", rules)
        assert match is not None

    def test_no_match(self, company_svc):
        rules = [{"dominio": "@pagar.me", "categoria": "financeiro"}]
        match = company_svc.match_domain_rule("user@google.com", rules)
        assert match is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_company_service.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement CompanyService**

Create `orchestrator/services/company_service.py`:

```python
"""
CompanyService - Fetches company profiles, clients, domain rules from Notion.
In-memory TTL cache to avoid Notion rate limits (3 req/sec).
"""

import os
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
import httpx

logger = logging.getLogger(__name__)

CACHE_TTL = 300  # 5 minutes


class CompanyService:
    """Fetches and caches company context from Notion databases."""

    def __init__(self):
        self.api_key = os.getenv("NOTION_API_KEY")
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.db_profiles = os.getenv("NOTION_DB_COMPANY_PROFILES", "")
        self.db_clientes = os.getenv("NOTION_DB_CLIENTES", "")
        self.db_domain_rules = os.getenv("NOTION_DB_DOMAIN_RULES", "")

        self._cache: Dict[str, Tuple[float, Dict]] = {}

    async def get_profile(self, account: str) -> Dict[str, Any]:
        """
        Returns unified company profile for an email account.
        Uses TTL cache to avoid repeated Notion API calls.
        """
        # Check cache
        if account in self._cache:
            cached_time, cached_profile = self._cache[account]
            if time.time() - cached_time < CACHE_TTL:
                return cached_profile

        if not self.api_key or not self.db_profiles:
            return {}

        try:
            profile = await self._fetch_profile(account)
            if profile:
                self._cache[account] = (time.time(), profile)
            return profile
        except Exception as e:
            logger.error(f"Erro ao buscar company profile: {e}")
            return {}

    async def _fetch_profile(self, account: str) -> Dict[str, Any]:
        """Fetches company profile, clients, and domain rules from Notion."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Fetch company profile
            resp = await client.post(
                f"{self.base_url}/databases/{self.db_profiles}/query",
                headers=self.headers,
                json={
                    "filter": {
                        "property": "Conta Email",
                        "email": {"equals": account}
                    }
                }
            )

            if resp.status_code != 200 or not resp.json().get("results"):
                return {}

            page = resp.json()["results"][0]
            page_id = page["id"]
            props = page["properties"]

            profile = {
                "nome": self._extract_title(props.get("Nome", {})),
                "setor": self._extract_select(props.get("Setor", {})),
                "tom": self._extract_select(props.get("Tom", {})),
                "assinatura": self._extract_rich_text(props.get("Assinatura", {})),
                "idioma": self._extract_select(props.get("Idioma Padrao", {})) or "pt-BR",
                "clientes": [],
                "domain_rules": [],
            }

            # 2. Fetch clients linked to this profile
            if self.db_clientes:
                profile["clientes"] = await self._fetch_clients(client, page_id)

            # 3. Fetch domain rules linked to this profile
            if self.db_domain_rules:
                profile["domain_rules"] = await self._fetch_domain_rules(client, page_id)

            return profile

    async def _fetch_clients(self, client: httpx.AsyncClient, profile_page_id: str) -> List[Dict]:
        """Fetches clients related to a company profile."""
        try:
            resp = await client.post(
                f"{self.base_url}/databases/{self.db_clientes}/query",
                headers=self.headers,
                json={
                    "filter": {
                        "property": "Company Profile",
                        "relation": {"contains": profile_page_id}
                    }
                }
            )
            if resp.status_code != 200:
                return []

            clients = []
            for page in resp.json().get("results", []):
                props = page["properties"]
                contatos_raw = self._extract_rich_text(props.get("Contatos", {}))
                contatos = [c.strip() for c in contatos_raw.split(",") if c.strip()] if contatos_raw else []
                clients.append({
                    "nome": self._extract_title(props.get("Nome", {})),
                    "contatos": contatos,
                    "projeto": self._extract_rich_text(props.get("Projeto Ativo", {})),
                    "prioridade": self._extract_select(props.get("Prioridade", {})),
                    "notas": self._extract_rich_text(props.get("Notas", {})),
                })
            return clients
        except Exception as e:
            logger.error(f"Erro ao buscar clientes: {e}")
            return []

    async def _fetch_domain_rules(self, client: httpx.AsyncClient, profile_page_id: str) -> List[Dict]:
        """Fetches domain rules related to a company profile."""
        try:
            resp = await client.post(
                f"{self.base_url}/databases/{self.db_domain_rules}/query",
                headers=self.headers,
                json={
                    "filter": {
                        "property": "Company Profile",
                        "relation": {"contains": profile_page_id}
                    }
                }
            )
            if resp.status_code != 200:
                return []

            rules = []
            for page in resp.json().get("results", []):
                props = page["properties"]
                rules.append({
                    "dominio": self._extract_title(props.get("Dominio", {})),
                    "categoria": self._extract_select(props.get("Categoria", {})),
                    "prioridade_minima": self._extract_select(props.get("Prioridade Minima", {})),
                    "acao_padrao": self._extract_select(props.get("Acao Padrao", {})),
                })
            return rules
        except Exception as e:
            logger.error(f"Erro ao buscar domain rules: {e}")
            return []

    def match_domain_rule(
        self, sender_email: str, domain_rules: List[Dict]
    ) -> Optional[Dict]:
        """
        Matches sender email against domain rules.
        Supports subdomain matching: user@sub.pagar.me matches @pagar.me
        """
        if not sender_email or "@" not in sender_email:
            return None

        sender_domain = sender_email.split("@")[1].lower()

        for rule in domain_rules:
            rule_domain = rule.get("dominio", "").lstrip("@").lower()
            if not rule_domain:
                continue
            # Exact match or subdomain match
            if sender_domain == rule_domain or sender_domain.endswith(f".{rule_domain}"):
                return rule

        return None

    def is_client_contact(
        self, sender_email: str, clients: List[Dict]
    ) -> Optional[Dict]:
        """Checks if sender is a known client contact. Returns client dict or None."""
        if not sender_email:
            return None
        sender_lower = sender_email.lower()
        for client in clients:
            for contato in client.get("contatos", []):
                if contato.lower() == sender_lower:
                    return client
        return None

    # --- Notion property extractors ---

    def _extract_title(self, prop: Dict) -> str:
        title_list = prop.get("title", [])
        return title_list[0]["text"]["content"] if title_list else ""

    def _extract_select(self, prop: Dict) -> Optional[str]:
        sel = prop.get("select")
        return sel["name"] if sel else None

    def _extract_rich_text(self, prop: Dict) -> str:
        rt_list = prop.get("rich_text", [])
        return rt_list[0]["text"]["content"] if rt_list else ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_company_service.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/services/company_service.py tests/test_company_service.py
git commit -m "feat: add CompanyService for Notion company profiles, clients, domain rules"
```

---

## Task 3: Create LearningEngine

**Files:**
- Create: `orchestrator/services/learning_engine.py`
- Test: `tests/test_learning_engine.py`

- [ ] **Step 1: Write tests for LearningEngine**

Create `tests/test_learning_engine.py`:

```python
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
        # delete_rules should have been called
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_learning_engine.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement LearningEngine**

Create `orchestrator/services/learning_engine.py`:

```python
"""
LearningEngine - Analyzes user feedback and generates automatic classification rules.

Runs every N emails (configurable via LEARNING_INTERVAL env var).
Stores rules in Qdrant learned_rules collection.
"""

import os
import logging
import hashlib
from typing import Dict, Any, List
from collections import Counter, defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

MIN_EVIDENCE = 3        # Minimum corrections to create a rule
MIN_CONFIDENCE = 0.7    # Minimum confidence to apply a rule
DELETE_THRESHOLD = 0.5   # Rules below this confidence are auto-deleted
MIN_WORD_LENGTH = 4

# Portuguese stopwords (common words to ignore in keyword extraction)
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
        """
        Main learning cycle. Fetches corrected emails, generates rules, stores them.
        Returns list of new/updated rules.
        """
        logger.info(f"[LearningEngine] Iniciando ciclo de aprendizado para {account}")

        corrected = await self.qdrant.get_corrected_emails(account)
        if not corrected:
            logger.info("[LearningEngine] Nenhum email corrigido encontrado")
            await self._cleanup_low_confidence_rules(account)
            return []

        # Also fetch confirmed emails for keyword filtering (< 20% threshold)
        confirmed = await self.qdrant.get_confirmed_emails(account)

        rules = []

        # Layer 1: Sender rules (priority + category)
        rules.extend(self._generate_sender_rules(corrected, account))

        # Layer 2: Domain rules (priority + category)
        rules.extend(self._generate_domain_rules(corrected, account))

        # Layer 3: Keyword rules (with <20% confirmed filter)
        rules.extend(self._generate_keyword_rules(corrected, confirmed, account))

        # Store rules
        if rules:
            await self.qdrant.store_rules(rules)
            logger.info(f"[LearningEngine] {len(rules)} regras geradas/atualizadas")

        # Cleanup low-confidence rules
        await self._cleanup_low_confidence_rules(account)

        # Notify via Telegram
        if rules and self.telegram and self.telegram._configured:
            summary = ", ".join(f"{r['rule_type']}:{r['match']}" for r in rules[:5])
            msg = f"🧠 Aprendi {len(rules)} regras novas:\n{summary}"
            try:
                await self.telegram._send_message(msg)
            except Exception as e:
                logger.error(f"Erro ao notificar aprendizado: {e}")

        return rules

    def _generate_sender_rules(
        self, corrected: List[Dict], account: str
    ) -> List[Dict[str, Any]]:
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

    def _generate_domain_rules(
        self, corrected: List[Dict], account: str
    ) -> List[Dict[str, Any]]:
        """Generate rules per sender domain (priority and category)."""
        by_domain = defaultdict(list)
        for email in corrected:
            sender = email.get("from_email", "")
            if sender and "@" in sender:
                domain = sender.split("@")[1]
                by_domain[domain].append(email)

        rules = []
        for domain, emails in by_domain.items():
            # Priority rules
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
            # Category rules
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

    def _generate_keyword_rules(
        self, corrected: List[Dict], confirmed: List[Dict], account: str
    ) -> List[Dict[str, Any]]:
        """Generate rules from subject keywords in corrected emails.
        Only includes words appearing in < 20% of confirmed-correct emails."""
        # Count keyword frequency in confirmed emails (for filtering)
        confirmed_keyword_counts = Counter()
        for email in confirmed:
            subject = email.get("subject", "")
            for word in self._extract_words(subject):
                confirmed_keyword_counts[word] += 1
        total_confirmed = max(len(confirmed), 1)

        # Count keyword frequency in corrected emails
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

            # Filter: word must appear in < 20% of confirmed emails
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
        """Count priority correction direction patterns (orig->corrected)."""
        directions = Counter()
        for e in emails:
            orig = e.get("feedback_original_priority")
            corr = e.get("feedback_corrected_priority")
            if orig and corr and orig != corr:
                directions[(orig, corr)] += 1
        return directions

    def _count_category_directions(self, emails: List[Dict]) -> Counter:
        """Count category correction direction patterns (orig->corrected)."""
        directions = Counter()
        for e in emails:
            orig = e.get("feedback_original_category")
            corr = e.get("feedback_corrected_category")
            if orig and corr and orig != corr:
                directions[(orig, corr)] += 1
        return directions

    def _extract_words(self, text: str) -> set:
        """Extract meaningful words from text, filtering stopwords."""
        import re
        words = re.findall(r'[a-záàâãéèêíïóôõúüç]+', text.lower())
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
                logger.info(f"[LearningEngine] {len(to_delete)} regras removidas (baixa confiança)")
        except Exception as e:
            logger.error(f"Erro ao limpar regras: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_learning_engine.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add orchestrator/services/learning_engine.py tests/test_learning_engine.py
git commit -m "feat: add LearningEngine for automatic rule generation from feedback"
```

---

## Task 4: Enrich LLM prompts with company context, feedback, sender profile, learned rules

**Files:**
- Modify: `orchestrator/services/llm_service.py`
- Test: `tests/test_enriched_prompts.py`

- [ ] **Step 1: Write tests for enriched prompts**

Create `tests/test_enriched_prompts.py`:

```python
"""Tests for enriched LLM prompts with company context, sender profile, learned rules"""
import pytest
from orchestrator.services.llm_service import LLMService


@pytest.fixture
def llm():
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
        assert "15 emails" in prompt or "15" in prompt
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
        assert "corrigiu" in prompt.lower() or "corrected" in prompt.lower() or "Media" in prompt


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_enriched_prompts.py -v`
Expected: FAIL (prompts don't include new context yet)

- [ ] **Step 3: Update _build_classifier_prompt() in llm_service.py**

Replace the `_build_classifier_prompt` method in `orchestrator/services/llm_service.py` with:

```python
def _build_classifier_prompt(self, email: Dict, context: Dict) -> str:
    """Constrói prompt de classificação com contexto enriquecido"""
    vips = context.get("vips", [])
    urgency_words = context.get("urgency_words", [])
    ignore_words = context.get("ignore_words", [])
    similar = context.get("similar_emails", [])
    thread_context = context.get("thread_context", [])
    company = context.get("company_profile", {})
    sender_profile = context.get("sender_profile", {})
    learned_rules = context.get("learned_rules", [])
    domain_rules = context.get("domain_rules", [])

    sections = []

    # Company context (~200 tokens)
    if company:
        sections.append(
            f"CONTEXTO DA EMPRESA:\n"
            f"Empresa: {company.get('nome', 'N/A')}\n"
            f"Setor: {company.get('setor', 'N/A')}\n"
            f"Tom: {company.get('tom', 'N/A')}"
        )

    # Domain rules (~150 tokens)
    if domain_rules:
        rules_text = "\n".join(
            f"- {r.get('dominio')}: categoria={r.get('categoria')}, "
            f"prioridade_min={r.get('prioridade_minima')}, acao={r.get('acao_padrao')}"
            for r in domain_rules[:5]
        )
        sections.append(f"REGRAS DE DOMINIO (manuais - PRIORIDADE MAXIMA, sempre seguir):\n{rules_text}")

    # Learned rules (~150 tokens) - filter out rules that conflict with manual domain rules
    if learned_rules:
        # Remove learned rules that conflict with manual domain rules
        manual_domains = {r.get("dominio", "").lstrip("@").lower() for r in domain_rules} if domain_rules else set()
        filtered_rules = [
            r for r in learned_rules
            if not (r.get("rule_type") == "domain" and r.get("match", "").lstrip("@").lower() in manual_domains)
        ]
        if filtered_rules:
            rules_text = "\n".join(
                f"- [{r.get('rule_type')}] {r.get('match')}: "
                f"{r.get('action')}={r.get('value')} (confianca: {r.get('confidence', 0):.0%})"
                for r in filtered_rules[:10]
            )
            sections.append(f"REGRAS APRENDIDAS (automaticas - usar quando nao houver regra manual):\n{rules_text}")

    # Sender profile (~100 tokens)
    if sender_profile and sender_profile.get("count", 0) > 0:
        sp = sender_profile
        profile_text = (
            f"PERFIL DO REMETENTE:\n"
            f"Emails anteriores: {sp.get('count', 0)}\n"
            f"Taxa importante: {sp.get('important_rate', 0):.0%}\n"
            f"Taxa acerto: {sp.get('correct_rate', 0):.0%}"
        )
        patterns = sp.get("correction_patterns", [])
        if patterns:
            corrections = ", ".join(
                f"{p['from']}->{p['to']} ({p['count']}x)" for p in patterns[:3]
            )
            profile_text += f"\nCorrecoes: {corrections}"
        if sp.get("is_client"):
            profile_text += f"\nCliente: {sp.get('client_name', 'Sim')} - Projeto: {sp.get('client_project', 'N/A')}"
        sections.append(profile_text)

    # Similar emails with feedback (~300 tokens, max 3)
    if similar:
        similar_text = "EMAILS SIMILARES (com feedback do usuario):\n"
        for i, s in enumerate(similar[:3]):
            p = s.get("payload", {})
            feedback = p.get("feedback", "pendente")
            line = f"{i+1}. De: {p.get('from_email', '?')} | Assunto: \"{p.get('subject', '?')}\""
            if feedback == "corrected":
                orig_p = p.get("feedback_original_priority", "?")
                corr_p = p.get("feedback_corrected_priority", "?")
                line += f"\n   Classificacao: {orig_p} -> Usuario corrigiu para: {corr_p}"
            elif feedback == "confirmed":
                line += "\n   Usuario confirmou classificacao"
            similar_text += line + "\n"
        sections.append(similar_text)

    # Thread context
    thread_text = ""
    if thread_context:
        thread_text = "\nEMAILS ANTERIORES DESTA THREAD:\n"
        for i, msg in enumerate(thread_context[-2:]):
            thread_text += f"--- Mensagem {i+1} ---\n"
            thread_text += f"De: {msg.get('from', 'Desconhecido')}\n"
            thread_text += f"Data: {msg.get('date', '')}\n"
            thread_text += f"Texto: {msg.get('body', '')[:300]}\n\n"

    enrichment = "\n\n".join(sections)

    return f"""Voce e um assistente de classificacao de emails. Analise o email e classifique.

{enrichment}

REMETENTES VIP (sempre importante):
{json.dumps(vips, ensure_ascii=False)}

PALAVRAS DE URGENCIA (aumentam prioridade):
{json.dumps(urgency_words, ensure_ascii=False)}

PALAVRAS PARA IGNORAR (provavelmente nao importante):
{json.dumps(ignore_words, ensure_ascii=False)}
{thread_text}
EMAIL ATUAL:
De: {email.get("from", "")}
Para: {email.get("to", "")}
Assunto: {email.get("subject", "")}
Corpo: {email.get("body", "")[:1500]}

Responda em JSON:
{{
    "importante": true/false,
    "prioridade": "Alta/Media/Baixa",
    "categoria": "cliente/financeiro/pessoal/trabalho/promocao/newsletter/outro",
    "confianca": 0.0-1.0,
    "razao": "explicacao breve",
    "entidades": {{
        "cliente": "nome se houver",
        "projeto": "nome se houver",
        "prazo": "data se mencionado",
        "protocolo": "numero se houver"
    }}
}}"""
```

- [ ] **Step 4: Update _build_action_prompt() to accept context parameter**

Update the method signature and body in `orchestrator/services/llm_service.py`:

```python
def _build_action_prompt(
    self, email: Dict, classification: Dict, summary: Dict,
    config: Dict, context: Dict = None
) -> str:
    """Constrói prompt de ação com contexto da empresa"""
    auto_reply = config.get("auto_reply", False)
    context = context or {}
    company = context.get("company_profile", {})
    sender_profile = context.get("sender_profile", {})

    # Company tone and signature
    company_section = ""
    if company:
        tom = company.get("tom", "profissional")
        assinatura = company.get("assinatura", "")
        idioma = company.get("idioma", "pt-BR")
        company_section = (
            f"\nCONTEXTO DA EMPRESA:\n"
            f"Tom: {tom}\n"
            f"Idioma: {idioma}\n"
        )
        if assinatura:
            company_section += f"Assinatura:\n{assinatura}\n"

    # Client context
    client_section = ""
    if sender_profile.get("is_client"):
        client_section = (
            f"\nCONTEXTO DO CLIENTE:\n"
            f"Cliente: {sender_profile.get('client_name', 'Desconhecido')}\n"
            f"Projeto: {sender_profile.get('client_project', 'N/A')}\n"
        )

    return f"""Decida a acao apropriada para este email.

EMAIL:
De: {email.get("from", "")}
Assunto: {email.get("subject", "")}

CLASSIFICACAO: {json.dumps(classification, ensure_ascii=False)}

RESUMO: {json.dumps(summary, ensure_ascii=False)}
{company_section}{client_section}
CONFIGURACAO:
- Resposta automatica: {"PERMITIDA" if auto_reply else "NAO PERMITIDA"}

ACOES POSSIVEIS:
1. "notificar" - Apenas notificar no Telegram
2. "arquivar" - Arquivar email (newsletter, promocao)
3. "criar_task" - Criar tarefa no Notion
4. "rascunho" - Criar rascunho de resposta (sem enviar)

IMPORTANTE: SEMPRE gere o campo "rascunho_resposta", independente da acao escolhida.
Excecao: NAO gere rascunho_resposta se a categoria for "spam" ou "newsletter".

O rascunho deve ser em {company.get('idioma', 'portugues')}, tom {company.get('tom', 'profissional')}.
{f'Use esta assinatura: {company.get("assinatura", "")}' if company.get('assinatura') else 'Termine com: Att, Diogenes Mendes'}

Responda em JSON:
{{
    "acao": "notificar/arquivar/criar_task/rascunho",
    "justificativa": "por que essa acao",
    "task": {{
        "titulo": "titulo da tarefa se criar_task",
        "prioridade": "Alta/Media/Baixa",
        "prazo": "YYYY-MM-DD se aplicavel"
    }},
    "rascunho_resposta": "texto do rascunho sempre que nao for spam/newsletter"
}}"""
```

- [ ] **Step 5: Update _build_summarizer_prompt() with company context**

Update the `_build_summarizer_prompt` method in `orchestrator/services/llm_service.py`:

```python
def _build_summarizer_prompt(self, email: Dict, classification: Dict, context: Dict = None) -> str:
    """Constrói prompt de resumo com contexto da empresa"""
    context = context or {}
    company = context.get("company_profile", {})

    company_section = ""
    if company:
        company_section = f"\nEmpresa: {company.get('nome', 'N/A')} ({company.get('setor', 'N/A')})\n"

    return f"""Resuma este email em portugues. Responda APENAS com JSON valido, sem texto adicional.
{company_section}
EMAIL:
De: {email.get("from", "")}
Assunto: {email.get("subject", "")}
Corpo: {email.get("body", "")[:1500]}

Responda em JSON:
{{\\"resumo\\": \\"resumo em 1-2 frases\\", \\"entidades\\": {{\\"cliente\\": \\"\\"}}, \\"sentimento\\": \\"neutro\\"}}"""
```

Update the `summarize_email` method to accept and forward context:

```python
async def summarize_email(
    self,
    email: Dict[str, Any],
    classification: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Gera resumo"""
    prompt = self._build_summarizer_prompt(email, classification, context)
    response = await self._call_llm(prompt, max_tokens=32768)

    if response:
        result = self._parse_summary(response.get("content", ""))
        result["reasoning_tokens"] = response.get("reasoning_tokens", 0)
        result["total_tokens"] = response.get("total_tokens", 0)
        return result

    return {"resumo": "Erro ao gerar resumo", "entidades": {}, "prazo": None}
```

- [ ] **Step 6: Add prompt size management utility**

Add this method to `LLMService` class in `orchestrator/services/llm_service.py`:

```python
MAX_PROMPT_TOKENS = 6000  # Leaves room for LLM response

def _estimate_tokens(self, text: str) -> int:
    """Rough token estimate: ~4 chars per token for mixed pt-BR/en"""
    return len(text) // 4

def _manage_prompt_size(self, prompt: str, email_body_start: int = None) -> str:
    """
    Truncates prompt if it exceeds MAX_PROMPT_TOKENS.
    Truncation priority (trim from bottom first):
    1. Company context + domain rules - always included
    2. Learned rules - always included
    3. Sender profile - always included
    4. Similar emails with feedback - trimmed first
    5. Email body - further truncated
    6. Thread context - dropped last
    """
    estimated = self._estimate_tokens(prompt)
    if estimated <= self.MAX_PROMPT_TOKENS:
        return prompt

    # Try removing thread context
    if "EMAILS ANTERIORES DESTA THREAD:" in prompt:
        thread_start = prompt.index("EMAILS ANTERIORES DESTA THREAD:")
        # Find the section end (next major section or EMAIL ATUAL)
        thread_end = prompt.find("EMAIL ATUAL:", thread_start)
        if thread_end > thread_start:
            prompt = prompt[:thread_start] + prompt[thread_end:]

    estimated = self._estimate_tokens(prompt)
    if estimated <= self.MAX_PROMPT_TOKENS:
        return prompt

    # Try truncating similar emails section
    if "EMAILS SIMILARES" in prompt:
        similar_start = prompt.index("EMAILS SIMILARES")
        similar_end = prompt.find("\n\n", similar_start + 50)
        if similar_end > similar_start:
            prompt = prompt[:similar_start] + prompt[similar_end:]

    estimated = self._estimate_tokens(prompt)
    if estimated <= self.MAX_PROMPT_TOKENS:
        return prompt

    # Last resort: truncate email body further
    if "Corpo:" in prompt:
        body_start = prompt.index("Corpo:") + 7
        body_end = prompt.find("\n\nResponda em JSON", body_start)
        if body_end > body_start:
            max_body = max(200, (self.MAX_PROMPT_TOKENS - self._estimate_tokens(prompt)) * 4 + (body_end - body_start))
            body = prompt[body_start:body_end]
            if len(body) > max_body:
                prompt = prompt[:body_start] + body[:max_body] + "..." + prompt[body_end:]

    return prompt
```

Then wrap all three `_build_*_prompt` methods' return values with `self._manage_prompt_size(prompt)`. Add at the end of `_build_classifier_prompt`, `_build_summarizer_prompt`, and `_build_action_prompt`:

```python
# At the end of each _build_*_prompt method, before return:
return self._manage_prompt_size(prompt)
```

Where `prompt` is the string that was previously being returned directly.

- [ ] **Step 8: Update decide_action() and summarize_email() to pass context**

In `llm_service.py`, update `decide_action` to accept and forward context:

```python
async def decide_action(
    self,
    email: Dict[str, Any],
    classification: Dict[str, Any],
    summary: Dict[str, Any],
    account_config: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Decide ação a tomar"""
    prompt = self._build_action_prompt(email, classification, summary, account_config, context)

    response = await self._call_llm(prompt, max_tokens=32768)

    if response:
        result = self._parse_action(response.get("content", ""))
        result["reasoning_tokens"] = response.get("reasoning_tokens", 0)
        result["total_tokens"] = response.get("total_tokens", 0)
        return result

    return {"acao": "notificar", "justificativa": "Erro ao decidir"}
```

Note: `summarize_email` was already updated in Step 5. Both `decide_action` and `summarize_email` have `context` as an optional parameter with default `None`, so existing callers (before Task 5 updates `email_processor.py`) won't break.

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/test_enriched_prompts.py -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add orchestrator/services/llm_service.py tests/test_enriched_prompts.py
git commit -m "feat: enrich LLM prompts with company context, sender profile, learned rules, feedback"
```

---

## Task 5: Update EmailProcessor to orchestrate all new components

**Files:**
- Modify: `orchestrator/handlers/email_processor.py`
- Modify: `orchestrator/main.py`

- [ ] **Step 1: Update EmailProcessor.__init__ to accept CompanyService and LearningEngine**

In `orchestrator/handlers/email_processor.py`, update the imports and constructor:

```python
# Add to imports at top:
from orchestrator.services.company_service import CompanyService
from orchestrator.services.learning_engine import LearningEngine

# Update __init__:
def __init__(
    self,
    notion: NotionService,
    qdrant: QdrantService,
    llm: LLMService,
    gog: GOGService,
    telegram: TelegramService,
    company: CompanyService = None,
    learning: LearningEngine = None
):
    self.notion = notion
    self.qdrant = qdrant
    self.llm = llm
    self.gog = gog
    self.telegram = telegram
    self.company = company
    self.learning = learning
    self.parser = EmailParser()
    self.cleaner = TextCleaner()
    self._learning_interval = int(os.getenv("LEARNING_INTERVAL", "50"))
    self._emails_processed = 0
    self._counter_loaded = False  # Lazy-load counter from Qdrant on first email
```

- [ ] **Step 2: Add company profile, sender profile, and learned rules to process_email context**

In `process_email()`, after the existing context building (after line ~123 `context["similar_emails"] = similar`), add:

```python
# Fetch company profile (cached)
if self.company:
    try:
        company_profile = await self.company.get_profile(account)
        context["company_profile"] = company_profile

        # Cross-reference sender with clients
        from_email = email.get("from_email", "") or email.get("from", "")
        if company_profile.get("clientes"):
            client = self.company.is_client_contact(from_email, company_profile["clientes"])
            if client:
                context["sender_profile_client"] = client

        # Domain rules from Notion
        if company_profile.get("domain_rules"):
            context["domain_rules"] = company_profile["domain_rules"]
            domain_match = self.company.match_domain_rule(from_email, company_profile["domain_rules"])
            if domain_match:
                context["domain_match"] = domain_match
    except Exception as e:
        logger.warning(f"[{email_id}] Erro ao buscar company profile: {e}")

# Fetch sender profile from Qdrant
if self.qdrant.is_connected():
    try:
        from_email = email.get("from_email", "") or email.get("from", "")
        sender_profile = await self.qdrant.get_sender_profile(from_email, account)
        # Enrich with client info
        if context.get("sender_profile_client"):
            client = context["sender_profile_client"]
            sender_profile["is_client"] = True
            sender_profile["client_name"] = client.get("nome", "")
            sender_profile["client_project"] = client.get("projeto", "")
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
```

- [ ] **Step 3: Pass context to decide_action and add learning trigger**

Update the `summarize_email` call (around line 139) and `decide_action` call (around line 144) to pass context:

```python
summary = await self.llm.summarize_email(email, classification, context)
# ...
action = await self.llm.decide_action(email, classification, summary, config, context)
```

After result status is set to "success" (before `return result` around line 197), add the learning trigger:

```python
# Lazy-load counter from Qdrant on first successful email
if not self._counter_loaded and self.qdrant.is_connected():
    try:
        self._emails_processed = await self.qdrant.get_learning_counter(account) or 0
        self._counter_loaded = True
    except Exception:
        pass

# Increment counter and trigger learning
self._emails_processed += 1
if self.learning and self._emails_processed % self._learning_interval == 0:
    try:
        logger.info(f"[{email_id}] Disparando ciclo de aprendizado (#{self._emails_processed})")
        await self.learning.analyze_and_learn(account)
        await self.qdrant.update_learning_counter(account, self._emails_processed)
    except Exception as e:
        logger.error(f"[{email_id}] Erro no learning engine: {e}")
```

- [ ] **Step 4: Update main.py to initialize new services**

In `orchestrator/main.py`, add imports and initialize:

```python
# Add import after existing service imports (line ~63):
from orchestrator.services.company_service import CompanyService
from orchestrator.services.learning_engine import LearningEngine

# Update initialization (after line ~70 `telegram = TelegramService()`):
company = CompanyService()
learning = LearningEngine(qdrant, telegram)
processor = EmailProcessor(notion, qdrant, llm, gog, telegram, company, learning)
```

Remove the old `processor = EmailProcessor(notion, qdrant, llm, gog, telegram)` line.

- [ ] **Step 5: Run full test suite**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/handlers/email_processor.py orchestrator/main.py
git commit -m "feat: integrate company profile, sender profile, learned rules, learning trigger into email pipeline"
```

---

## Task 6: Update telegram_poller.py to write structured feedback to Qdrant

**Files:**
- Modify: `telegram_poller.py`

- [ ] **Step 1: Add Qdrant import and initialization to telegram_poller.py**

At the top of `telegram_poller.py`, after the existing imports (around line 34), add:

```python
from orchestrator.services.qdrant_service import QdrantService

# Initialize Qdrant for structured feedback
_qdrant = QdrantService()
```

- [ ] **Step 2: Update save_feedback() to also write to Qdrant**

Replace the `save_feedback` function (around line 197) with an async version. Since `save_feedback` is called from `action_reclassify_complete` which is already async, we can make it async too:

```python
async def save_feedback(email_id: str, sender: str, original_urgency: str, corrected_urgency: str, keywords: list):
    """Salva feedback de reclassificação em feedback.json (backup) e Qdrant (primário)"""
    try:
        # Backup: feedback.json
        feedback_data = _load_json(FEEDBACK_FILE, [])
        feedback_data.append({
            "email_id": email_id,
            "from": sender,
            "original_urgency": original_urgency,
            "corrected_urgency": corrected_urgency,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "keywords": keywords
        })
        _atomic_write_json(FEEDBACK_FILE, feedback_data)

        # Primary: Qdrant structured feedback
        if _qdrant.is_connected():
            await _qdrant.update_feedback(
                email_id=email_id,
                feedback="corrected",
                original_priority=original_urgency,
                corrected_priority=corrected_urgency,
            )

        logger.info(f"Feedback salvo: {email_id[:15]} | {original_urgency} -> {corrected_urgency}")
    except Exception as e:
        logger.error(f"Erro ao salvar feedback: {e}")
```

Also update the caller in `action_reclassify_complete` (around line 551) to `await`:

```python
# Change: save_feedback(email_id, sender, original_urgency, new_urgency, keywords)
# To:
await save_feedback(email_id, sender, original_urgency, new_urgency, keywords)
```

- [ ] **Step 3: Commit**

```bash
git add telegram_poller.py
git commit -m "feat: write structured feedback to Qdrant from telegram_poller reclassifications"
```

---

## Task 7: Update .env.example and create migration script

**Files:**
- Modify: `.env.example`
- Create: `scripts/migrate_feedback.py`

- [ ] **Step 1: Add new env vars to .env.example**

Append to `.env.example` before the `# --- Opcional ---` section:

```
# --- Notion (Company Profiles) ---
# Database IDs for company context (create these in Notion)
# NOTION_DB_COMPANY_PROFILES=xxxxx
# NOTION_DB_CLIENTES=xxxxx
# NOTION_DB_DOMAIN_RULES=xxxxx

# --- Learning Engine ---
# How often to run learning cycle (default: every 50 emails)
# LEARNING_INTERVAL=50
```

- [ ] **Step 2: Create migration script**

Create `scripts/migrate_feedback.py`:

```python
#!/usr/bin/env python3
"""
One-time migration: reads feedback.json and backfills structured feedback data into Qdrant.

Usage: python scripts/migrate_feedback.py
"""

import os
import sys
import json
import asyncio
from pathlib import Path

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from orchestrator.services.qdrant_service import QdrantService


async def migrate():
    feedback_file = BASE_DIR / "feedback.json"
    if not feedback_file.exists():
        print("feedback.json not found, nothing to migrate.")
        return

    with open(feedback_file) as f:
        feedback_data = json.load(f)

    if not feedback_data:
        print("feedback.json is empty.")
        return

    qdrant = QdrantService()
    if not qdrant.is_connected():
        print("ERROR: Qdrant not connected. Start Qdrant and try again.")
        sys.exit(1)

    migrated = 0
    errors = 0
    for entry in feedback_data:
        email_id = entry.get("email_id", "")
        if not email_id:
            continue

        try:
            success = await qdrant.update_feedback(
                email_id=email_id,
                feedback="corrected",
                original_priority=entry.get("original_urgency", ""),
                corrected_priority=entry.get("corrected_urgency", ""),
            )
            if success:
                migrated += 1
            else:
                # Point might not exist in Qdrant (old email)
                errors += 1
        except Exception as e:
            print(f"Error migrating {email_id}: {e}")
            errors += 1

    print(f"Migration complete: {migrated} migrated, {errors} skipped/errors out of {len(feedback_data)} total.")


if __name__ == "__main__":
    asyncio.run(migrate())
```

- [ ] **Step 3: Commit**

```bash
git add .env.example scripts/migrate_feedback.py
git commit -m "feat: add env vars for company profiles and feedback migration script"
```

---

## Task 8: Final integration test and cleanup

- [ ] **Step 1: Run full test suite**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify imports work**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && python -c "from orchestrator.services.company_service import CompanyService; from orchestrator.services.learning_engine import LearningEngine; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 3: Verify main.py starts without errors**

Run: `cd "C:/Users/PC Di/Desktop/CODIGO/Agente-Email-Openclaw" && timeout 5 python -c "from orchestrator.main import app; print('FastAPI app created OK')" 2>&1 || true`
Expected: "FastAPI app created OK" (may warn about missing env vars, that's fine)

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete Company Profile + Learning Engine implementation"
```
