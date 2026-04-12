"""
GOG Service - Integração com Google Workspace via GOG CLI
Usa asyncio.create_subprocess_exec para não bloquear o event loop.
"""

import os
import json
import asyncio
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
        """Verifica se GOG está instalado (sync, executado apenas no init)"""
        try:
            result = subprocess.run(
                ["gog", "version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def is_ready(self) -> bool:
        return self._ready

    async def _run_gog(self, cmd: List[str], account: str, timeout: float = 30) -> Optional[str]:
        """Executa comando GOG de forma assíncrona (não bloqueia o event loop)"""
        env = os.environ.copy()
        env["GOG_KEYRING_PASSWORD"] = self.keyring_password
        env["GOG_ACCOUNT"] = account

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            if proc.returncode == 0:
                return stdout.decode("utf-8", errors="replace")
            else:
                logger.error(f"GOG erro (cmd={cmd[1:3]}): {stderr.decode('utf-8', errors='replace')}")
                return None
        except asyncio.TimeoutError:
            logger.error(f"Timeout ({timeout}s) ao executar GOG: {cmd[1:3]}")
            proc.kill()
            return None
        except Exception as e:
            logger.error(f"Erro ao executar GOG: {e}")
            return None

    async def get_email(self, email_id: str, account: str) -> Optional[Dict[str, Any]]:
        """Busca email completo pelo ID"""
        if not self._ready:
            logger.error("GOG não está pronto")
            return None

        output = await self._run_gog(
            ["gog", "gmail", "get", email_id, "--format", "full"],
            account, timeout=30
        )
        if output:
            return self._parse_email(output)
        return None

    async def get_thread(self, thread_id: str, account: str) -> List[Dict[str, Any]]:
        """Busca todos os emails de uma thread"""
        if not self._ready:
            return []

        output = await self._run_gog(
            ["gog", "gmail", "thread", thread_id, "--format", "full"],
            account, timeout=30
        )
        if output:
            return self._parse_thread(output)
        return []

    async def archive_email(self, email_id: str, account: str) -> bool:
        """Arquiva um email (remove da inbox)"""
        if not self._ready:
            return False

        output = await self._run_gog(
            ["gog", "gmail", "modify", email_id, "--remove-labels", "INBOX", "UNREAD"],
            account, timeout=15
        )
        if output is not None:
            logger.info(f"Email arquivado: {email_id}")
            return True
        return False

    async def create_draft(
        self, to: str, subject: str, body: str,
        account: str, thread_id: Optional[str] = None
    ) -> Optional[str]:
        """Cria rascunho de resposta"""
        if not self._ready:
            return None

        cmd = [
            "gog", "gmail", "draft", "create",
            "--to", to, "--subject", subject, "--body", body
        ]
        if thread_id:
            cmd.extend(["--thread-id", thread_id])

        output = await self._run_gog(cmd, account, timeout=30)
        if output:
            draft_id = output.strip().split("\n")[-1]
            logger.info(f"Rascunho criado: {draft_id}")
            return draft_id
        return None

    async def move_to_label(self, email_id: str, label: str, account: str) -> bool:
        """Move email para uma label específica"""
        if not self._ready:
            return False

        output = await self._run_gog(
            ["gog", "gmail", "modify", email_id, "--add-labels", label],
            account, timeout=15
        )
        return output is not None

    def _parse_email(self, raw_output: str) -> Dict[str, Any]:
        """Parse do output do GOG para dict estruturado"""
        from orchestrator.utils.email_parser import EmailParser
        parser = EmailParser()
        return parser.parse(raw_output)

    def _parse_thread(self, raw_output: str) -> List[Dict[str, Any]]:
        """Parse de thread com múltiplos emails separados por delimitador"""
        from orchestrator.utils.email_parser import EmailParser
        parser = EmailParser()

        # GOG separa emails na thread com linha "---" ou similar
        # Tentar separar por padrões comuns
        parts = raw_output.split("\n---\n")
        if len(parts) <= 1:
            parts = raw_output.split("\n\n\n")

        emails = []
        for part in parts:
            part = part.strip()
            if part:
                parsed = parser.parse(part)
                if parsed.get("id") or parsed.get("body"):
                    emails.append(parsed)

        return emails