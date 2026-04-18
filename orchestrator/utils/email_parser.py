"""
Email Parser - Extrai e estrutura dados de emails
"""

import re
import base64
import email
from email import policy
from email.message import EmailMessage
from typing import Dict, Any, List, Optional
import html
import logging

logger = logging.getLogger(__name__)


# RFC 5322 angle-bracket form OR bare email. Prefers the bracketed form when
# both exist (e.g. `"Name" <user@example.com>` returns `user@example.com`).
_EMAIL_RE = re.compile(r'<([^<>@\s]+@[^<>@\s]+)>|([^\s<>,"]+@[^\s<>,"]+)')


def extract_email_address(value: str) -> str:
    """Extract a clean lowercase email from a header string.

    Handles:
        'Diogenes <me@domain.com>'       -> 'me@domain.com'
        'me@domain.com'                   -> 'me@domain.com'
        '"Name" <a@b.com>, <c@d.com>'     -> 'a@b.com' (first match)
        ''                                -> ''
        None                              -> ''
    """
    if not value:
        return ""
    m = _EMAIL_RE.search(str(value))
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip().lower()


def emails_match(a: str, b: str) -> bool:
    """Compare two possibly-formatted email strings by extracted address."""
    return bool(extract_email_address(a)) and extract_email_address(a) == extract_email_address(b)


class EmailParser:
    """Parser para extrair dados estruturados de emails"""
    
    def parse(self, raw_email: Any) -> Dict[str, Any]:
        """
        Parse de email para estrutura padronizada
        
        Args:
            raw_email: Email em formato string (do GOG)
        
        Returns:
            Dict com id, subject, from, to, body, etc.
        """
        # Se é string, parse como GOG output
        if isinstance(raw_email, str):
            return self._parse_gog_output(raw_email)
        
        # Se é dict
        if isinstance(raw_email, dict):
            return self._parse_dict(raw_email)
        
        return {}
    
    def _parse_gog_output(self, raw: str) -> Dict[str, Any]:
        """Parse do output do GOG CLI"""
        result = {
            "id": "",
            "threadId": "",
            "subject": "",
            "from": "",
            "from_name": "",
            "from_email": "",
            "to": "",
            "cc": "",
            "date": "",
            "body": "",
            "body_clean": "",
            "attachments": [],
            "labels": []
        }
        
        try:
            # GOG retorna formato:
            # key\tvalue
            # com headers no topo e body depois
            
            lines = raw.split("\n")
            header_section = True
            body_lines = []
            
            for line in lines:
                # Headers são key\tvalue
                if header_section and "\t" in line:
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip()
                        
                        # Mapear headers
                        if key == "id":
                            result["id"] = value
                        elif key == "thread_id":
                            result["threadId"] = value
                        elif key == "from":
                            result["from"] = value
                            result["from_name"], result["from_email"] = self._parse_from(value)
                        elif key == "to":
                            result["to"] = value
                        elif key == "cc":
                            result["cc"] = value
                        elif key == "subject":
                            result["subject"] = value
                        elif key == "date":
                            result["date"] = value
                        elif key == "label_ids":
                            result["labels"] = value.split(",") if value else []
                
                # Quando encontramos algo que não é header, é body
                elif line.strip():
                    header_section = False
                    body_lines.append(line)
            
            # Processar body
            body_raw = "\n".join(body_lines)
            
            # Se tem HTML, extrair texto
            if "<html" in body_raw.lower() or "<!doctype" in body_raw.lower():
                result["body"] = self._html_to_text(body_raw)
            else:
                result["body"] = body_raw
            
            logger.info(f"Email parseado: id={result['id']}, from={result['from_email']}")
            
        except Exception as e:
            logger.error(f"Erro ao parsear email GOG: {e}")
            result["body"] = raw
        
        return result
    
    def _parse_dict(self, data: Dict) -> Dict[str, Any]:
        """Parse quando o email vem como dict"""
        result = {
            "id": data.get("id", ""),
            "threadId": data.get("threadId", ""),
            "subject": "",
            "from": "",
            "from_name": "",
            "from_email": "",
            "to": "",
            "cc": "",
            "date": "",
            "body": "",
            "attachments": [],
            "labels": data.get("labelIds", [])
        }
        
        # Extrair headers
        payload = data.get("payload", {})
        headers = payload.get("headers", {})
        
        if isinstance(headers, dict):
            result["subject"] = headers.get("Subject", "")
            from_raw = headers.get("From", "")
            result["from"] = from_raw
            result["from_name"], result["from_email"] = self._parse_from(from_raw)
            result["to"] = headers.get("To", "")
            result["cc"] = headers.get("Cc", "")
            result["date"] = headers.get("Date", "")
        
        # Extrair body
        body_data = payload.get("body", {})
        if isinstance(body_data, dict):
            if "text" in body_data:
                result["body"] = body_data["text"]
            elif "data" in body_data:
                try:
                    decoded = base64.urlsafe_b64decode(body_data["data"])
                    result["body"] = decoded.decode("utf-8", errors="ignore")
                except:
                    result["body"] = ""
        
        return result
    
    def _parse_from(self, from_header: str) -> tuple:
        """Parse do header From para separar nome e email"""
        if not from_header:
            return "", ""
        
        # Formato: "Nome <email>" ou só email
        match = re.search(r'([^<]+)<([^>]+)>', from_header)
        if match:
            name = match.group(1).strip()
            email_addr = match.group(2).strip()
            return name, email_addr
        
        # Só email
        if "@" in from_header:
            return "", from_header.strip()
        
        return from_header.strip(), ""
    
    def _html_to_text(self, html_content: str) -> str:
        """Converte HTML para texto limpo"""
        # Remover scripts e styles
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Remover imagens
        html_content = re.sub(r'<img[^>]*>', '', html_content, flags=re.IGNORECASE)
        
        # Converter links
        html_content = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', r'\2 (\1)', html_content, flags=re.IGNORECASE)
        
        # Converter quebras
        html_content = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</p>', '\n\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'</div>', '\n', html_content, flags=re.IGNORECASE)
        html_content = re.sub(r'<li[^>]*>', '\n• ', html_content, flags=re.IGNORECASE)
        
        # Remover tags
        html_content = re.sub(r'<[^>]+>', '', html_content)
        
        # Decodar entidades
        html_content = html.unescape(html_content)
        
        # Limpar espaços
        html_content = re.sub(r'\n{3,}', '\n\n', html_content)
        html_content = re.sub(r' {2,}', ' ', html_content)
        
        # Truncar se muito longo
        if len(html_content) > 3000:
            html_content = html_content[:3000] + "\n... [truncado]"
        
        return html_content.strip()