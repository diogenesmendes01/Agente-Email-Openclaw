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
    
    def __init__(self):
        self.host = os.getenv("QDRANT_HOST", "localhost")
        self.port = int(os.getenv("QDRANT_PORT", "6333"))
        self.client = None
        self._connected = False
        
        try:
            self.client = QdrantClient(host=self.host, port=self.port)
            self._connected = True
            self._ensure_collections()
            logger.info(f"QdrantService conectado em {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"Qdrant não disponível: {e}. Funcionando sem memória.")
    
    def is_connected(self) -> bool:
        return self._connected
    
    def _ensure_collections(self):
        """Cria collections se não existirem"""
        collections = [self.COLLECTION_EMAILS, self.COLLECTION_THREADS, self.COLLECTION_PROFILES]
        
        for col in collections:
            try:
                self.client.get_collection(col)
            except UnexpectedResponse:
                logger.info(f"Criando collection: {col}")
                self.client.create_collection(
                    collection_name=col,
                    vectors_config=models.VectorParams(
                        size=1536,  # OpenAI text-embedding-3-small
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
                            "classification": metadata.get("classification", ""),
                            "priority": metadata.get("priority", ""),
                            "category": metadata.get("category", ""),
                            "action": metadata.get("action", ""),
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
    
    async def update_feedback(self, email_id: str, feedback: str) -> bool:
        """
        Atualiza feedback de um email
        
        Args:
            email_id: ID do email
            feedback: "👍 Correto" ou "👎 Incorreto"
        
        Returns:
            True se sucesso
        """
        if not self._connected:
            return False
        
        try:
            # Buscar ponto atual
            point = self.client.retrieve(
                collection_name=self.COLLECTION_EMAILS,
                ids=[email_id],
                with_payload=True,
                with_vectors=True
            )
            
            if not point:
                return False
            
            # Atualizar payload
            self.client.upsert(
                collection_name=self.COLLECTION_EMAILS,
                points=[
                    models.PointStruct(
                        id=email_id,
                        vector=point[0].vector,
                        payload={
                            **point[0].payload,
                            "feedback": feedback
                        }
                    )
                ]
            )
            
            logger.info(f"Feedback atualizado: {email_id} -> {feedback}")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao atualizar feedback: {e}")
            return False
    
    async def get_sender_profile(self, from_email: str, account: str) -> Dict[str, Any]:
        """
        Busca perfil de um remetente
        
        Args:
            from_email: Email do remetente
            account: Conta do usuário
        
        Returns:
            Perfil com estatísticas de comunicação
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
            important = sum(1 for e in emails if e.payload.get("classification") in ["Importante", "Crítico"])
            correct = sum(1 for e in emails if e.payload.get("feedback") == "👍 Correto")
            
            return {
                "count": total,
                "important_count": important,
                "important_rate": important / total if total > 0 else 0,
                "correct_rate": correct / total if total > 0 else 0,
                "last_email": emails[0].payload.get("timestamp") if emails else None
            }
            
        except Exception as e:
            logger.error(f"Erro ao buscar perfil do remetente: {e}")
            return {}