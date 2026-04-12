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
            status_code=404, reason_phrase="Not found", content=b"", headers={}
        )
        qdrant._ensure_collections()
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
