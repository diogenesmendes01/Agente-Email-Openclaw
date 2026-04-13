"""
Qdrant Service - Vector Database para memória de emails
"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

logger = logging.getLogger(__name__)


class QdrantService:
    """Serviço para interagir com Qdrant Vector DB"""
    
    COLLECTION_EMAILS = "emails"
    COLLECTION_THREADS = "threads"
    COLLECTION_PROFILES = "profiles"
    COLLECTION_LEARNED_RULES = "learned_rules"
    
    def __init__(self):
        self.host = os.getenv("QDRANT_HOST", "localhost")
        self.port = int(os.getenv("QDRANT_PORT", "6333"))
        self.api_key = os.getenv("QDRANT_API_KEY", "").strip() or None
        self.https = os.getenv("QDRANT_HTTPS", "false").strip().lower() in {"1", "true", "yes"}
        self.client = None
        self._connected = False
        
        try:
            self.client = QdrantClient(
                host=self.host,
                port=self.port,
                api_key=self.api_key,
                https=self.https,
            )
            self._connected = True
            self._ensure_collections()
            logger.info(f"QdrantService conectado em {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"Qdrant não disponível: {e}. Funcionando sem memória.")
    
    def is_connected(self) -> bool:
        return self._connected
    
    def _ensure_collections(self):
        """Cria collections se não existirem"""
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
    
    async def store_email(self, email_id: str, embedding: List[float], metadata: Dict[str, Any]) -> bool:
        """
        Armazena embedding do email no Qdrant
        
        Args:
            email_id: ID único do email
            embedding: Vetor de embedding (1536 dims)
            metadata: Metadados (account, subject, from, classification, etc.)
        
        Returns:
            True se sucesso
        """
        if not self._connected:
            return False
        
        try:
            self.client.upsert(
                collection_name=self.COLLECTION_EMAILS,
                points=[
                    models.PointStruct(
                        id=email_id,
                        vector=embedding,
                        payload={
                            "email_id": email_id,
                            "account": metadata.get("account", ""),
                            "subject": metadata.get("subject", ""),
                            "from_email": metadata.get("from", ""),
                            "classification": metadata.get("classificacao", ""),
                            "priority": metadata.get("prioridade", ""),
                            "category": metadata.get("categoria", ""),
                            "action": metadata.get("acao", ""),
                            "feedback": metadata.get("feedback", "pendente"),
                            "timestamp": metadata.get("timestamp", datetime.utcnow().isoformat()),
                            "thread_id": metadata.get("thread_id", ""),
                            "resumo": metadata.get("resumo", "")
                        }
                    )
                ]
            )
            logger.info(f"Email armazenado: {email_id}")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao armazenar email no Qdrant: {e}")
            return False
    
    async def search_similar(
        self,
        embedding: List[float],
        account: str,
        limit: int = 5,
        score_threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Busca emails similares por embedding
        
        Args:
            embedding: Vetor de embedding para buscar
            account: Filtrar por conta
            limit: Máximo de resultados
            score_threshold: Score mínimo de similaridade
        
        Returns:
            Lista de emails similares com scores
        """
        if not self._connected:
            return []
        
        try:
            results = self.client.search(
                collection_name=self.COLLECTION_EMAILS,
                query_vector=embedding,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="account",
                            match=models.MatchValue(value=account)
                        )
                    ]
                ),
                limit=limit,
                score_threshold=score_threshold
            )
            
            return [
                {
                    "email_id": hit.id,
                    "score": hit.score,
                    "payload": hit.payload
                }
                for hit in results
            ]
            
        except Exception as e:
            logger.error(f"Erro ao buscar emails similares: {e}")
            return []
    
    async def get_thread_context(self, thread_id: str) -> List[Dict[str, Any]]:
        """
        Busca contexto de uma thread (emails anteriores)
        
        Args:
            thread_id: ID da thread
        
        Returns:
            Lista de emails da thread
        """
        if not self._connected:
            return []
        
        try:
            results = self.client.scroll(
                collection_name=self.COLLECTION_EMAILS,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="thread_id",
                            match=models.MatchValue(value=thread_id)
                        )
                    ]
                ),
                limit=20,
                with_payload=True
            )
            
            return [
                {
                    "email_id": point.id,
                    **point.payload
                }
                for point in results[0]
            ]
            
        except Exception as e:
            logger.error(f"Erro ao buscar contexto da thread: {e}")
            return []
    
    async def update_feedback(
        self,
        email_id: str,
        feedback: str,
        original_priority: str = None,
        corrected_priority: str = None,
        original_category: str = None,
        corrected_category: str = None
    ) -> bool:
        """Atualiza feedback de um email com dados estruturados de correção."""
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
    
    async def get_sender_profile(self, from_email: str, account: str) -> Dict[str, Any]:
        """Busca perfil de um remetente com padrões de correção."""
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

    async def store_rules(self, rules: List[Dict[str, Any]]) -> bool:
        """Stores learned rules in the learned_rules collection."""
        if not self._connected or not rules:
            return False
        try:
            import hashlib
            points = []
            for rule in rules:
                id_str = f"{rule['rule_type']}:{rule['match']}:{rule['account']}"
                point_id = hashlib.md5(id_str.encode()).hexdigest()
                points.append(models.PointStruct(
                    id=point_id,
                    vector=[0.0],
                    payload={**rule, "last_updated": datetime.utcnow().isoformat()}
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
                limit=200,
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
