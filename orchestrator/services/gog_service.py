"""
GOG Service - Integração com Google Workspace via GOG CLI
"""

import os
import json
import logging
import subprocess
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class GOGService:
    """Serviço para interagir com Gmail via GOG CLI"""
    
    def __init__(self):
        self.keyring_password = os.getenv("GOG_KEYRING_PASSWORD", "")
        self._ready = self._check_gog()
        
        if self._ready:
            logger.info("GOGService pronto")
        else:
            logger.warning("GOG não está disponível")
    
    def _check_gog(self) -> bool:
        """Verifica se GOG está instalado e configurado"""
        try:
            result = subprocess.run(
                ["gog", "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False
    
    def is_ready(self) -> bool:
        return self._ready
    
    async def get_email(self, email_id: str, account: str) -> Optional[Dict[str, Any]]:
        """
        Busca email completo pelo ID
        
        Args:
            email_id: ID do email no Gmail
            account: Email da conta
        
        Returns:
            Dict com email completo ou None se falhar
        """
        if not self._ready:
            logger.error("GOG não está pronto")
            return None
        
        try:
            env = os.environ.copy()
            env["GOG_KEYRING_PASSWORD"] = self.keyring_password
            env["GOG_ACCOUNT"] = account
            
            result = subprocess.run(
                ["gog", "gmail", "get", email_id, "--format", "full"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30
            )
            
            if result.returncode == 0:
                return self._parse_email(result.stdout)
            else:
                logger.error(f"Erro ao buscar email: {result.stderr}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.error("Timeout ao buscar email")
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar email: {e}")
            return None
    
    async def get_thread(self, thread_id: str, account: str) -> List[Dict[str, Any]]:
        """
        Busca todos os emails de uma thread
        
        Args:
            thread_id: ID da thread
            account: Email da conta
        
        Returns:
            Lista de emails da thread
        """
        if not self._ready:
            return []
        
        try:
            env = os.environ.copy()
            env["GOG_KEYRING_PASSWORD"] = self.keyring_password
            env["GOG_ACCOUNT"] = account
            
            result = subprocess.run(
                ["gog", "gmail", "thread", thread_id, "--format", "full"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30
            )
            
            if result.returncode == 0:
                # Parse da thread (múltiplos emails)
                return self._parse_thread(result.stdout)
            
            return []
            
        except Exception as e:
            logger.error(f"Erro ao buscar thread: {e}")
            return []
    
    async def archive_email(self, email_id: str, account: str) -> bool:
        """
        Arquiva um email (remove da inbox)
        
        Args:
            email_id: ID do email
            account: Email da conta
        
        Returns:
            True se sucesso
        """
        if not self._ready:
            return False
        
        try:
            env = os.environ.copy()
            env["GOG_KEYRING_PASSWORD"] = self.keyring_password
            env["GOG_ACCOUNT"] = account
            
            result = subprocess.run(
                ["gog", "gmail", "modify", email_id, "--remove-labels", "INBOX", "UNREAD"],
                capture_output=True,
                text=True,
                env=env,
                timeout=15
            )
            
            if result.returncode == 0:
                logger.info(f"Email arquivado: {email_id}")
                return True
            else:
                logger.error(f"Erro ao arquivar: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Erro ao arquivar email: {e}")
            return False
    
    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        account: str,
        thread_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Cria rascunho de resposta
        
        Args:
            to: Email do destinatário
            subject: Assunto
            body: Corpo do email
            account: Conta do remetente
            thread_id: ID da thread (para responder)
        
        Returns:
            ID do rascunho ou None
        """
        if not self._ready:
            return None
        
        try:
            env = os.environ.copy()
            env["GOG_KEYRING_PASSWORD"] = self.keyring_password
            env["GOG_ACCOUNT"] = account
            
            cmd = [
                "gog", "gmail", "draft", "create",
                "--to", to,
                "--subject", subject,
                "--body", body
            ]
            
            if thread_id:
                cmd.extend(["--thread-id", thread_id])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=30
            )
            
            if result.returncode == 0:
                # Extrair ID do rascunho da resposta
                draft_id = result.stdout.strip().split("\n")[-1] if result.stdout else None
                logger.info(f"Rascunho criado: {draft_id}")
                return draft_id
            
            return None
            
        except Exception as e:
            logger.error(f"Erro ao criar rascunho: {e}")
            return None
    
    async def move_to_label(self, email_id: str, label: str, account: str) -> bool:
        """
        Move email para uma label específica
        
        Args:
            email_id: ID do email
            label: Nome da label
            account: Email da conta
        
        Returns:
            True se sucesso
        """
        if not self._ready:
            return False
        
        try:
            env = os.environ.copy()
            env["GOG_KEYRING_PASSWORD"] = self.keyring_password
            env["GOG_ACCOUNT"] = account
            
            result = subprocess.run(
                ["gog", "gmail", "modify", email_id, "--add-labels", label],
                capture_output=True,
                text=True,
                env=env,
                timeout=15
            )
            
            return result.returncode == 0
            
        except Exception as e:
            logger.error(f"Erro ao mover para label: {e}")
            return False
    
    def _parse_email(self, raw_output: str) -> Dict[str, Any]:
        """Parse do output do GOG para dict estruturado"""
        # Usar EmailParser que já sabe parsear formato GOG
        from orchestrator.utils.email_parser import EmailParser
        parser = EmailParser()
        return parser.parse(raw_output)
    
    def _parse_thread(self, raw_output: str) -> List[Dict[str, Any]]:
        """Parse de thread com múltiplos emails"""
        # Simplificado - retorna lista vazia por enquanto
        # TODO: Implementar parse completo de thread
        return []