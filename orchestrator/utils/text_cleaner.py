"""
Text Cleaner - Limpeza e normalização de texto de emails
"""

import re
from typing import List, Optional


class TextCleaner:
    """Utilitário para limpar texto de emails"""
    
    # Padrões de assinatura de email
    SIGNATURE_PATTERNS = [
        r'^--\s*$',
        r'^---+\s*$',
        r'^_{10,}$',
        r'^Sent from my',
        r'^Enviado do meu',
        r'^Get Outlook for',
        r'^Confidentiality Notice',
        r'^Esta mensagem',
        r'^This message contains',
        r'^Disclaimer:',
        r'^Aviso legal:',
    ]
    
    # Padrões de quote/citação
    QUOTE_PATTERNS = [
        r'^On .+ wrote:$',
        r'^Em .+ escreveu:$',
        r'^>.*$',
        r'^\*?From:.*$',
        r'^\*?De:.*$',
        r'^\*?To:.*$',
        r'^\*?Para:.*$',
        r'^\*?Subject:.*$',
        r'^\*?Assunto:.*$',
    ]
    
    def clean(self, text: str, max_length: int = 4000) -> str:
        """
        Limpa texto do email
        
        Args:
            text: Texto original
            max_length: Tamanho máximo (trunca se exceder)
        
        Returns:
            Texto limpo
        """
        if not text:
            return ""
        
        # 1. Normalizar quebras de linha
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # 2. Remover múltiplas linhas vazias
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 3. Remover caracteres de controle
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        
        # 4. Remover assinatura
        text = self._remove_signature(text)
        
        # 5. Remover quotes/citações (opcional)
        # text = self._remove_quotes(text)
        
        # 6. Remover URLs longas
        text = re.sub(r'https?://[^\s<>"{}|\\^`\[\]]{50,}', '[URL]', text)
        
        # 7. Remover emails no texto
        text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', text)
        
        # 8. Normalizar espaços
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'^\s+', '', text, flags=re.MULTILINE)
        
        # 9. Truncar se necessário
        if len(text) > max_length:
            text = text[:max_length] + "\n... [truncado]"
        
        return text.strip()
    
    def extract_preview(self, text: str, max_chars: int = 200) -> str:
        """
        Extrai preview curto do texto
        
        Args:
            text: Texto completo
            max_chars: Máximo de caracteres
        
        Returns:
            Preview truncado
        """
        if not text:
            return ""
        
        # Limpar primeiro
        clean = self.clean(text, max_length=max_chars * 2)
        
        # Pegar primeira "frase" ou parágrafo
        first_para = clean.split('\n\n')[0]
        
        if len(first_para) > max_chars:
            return first_para[:max_chars-3] + "..."
        
        return first_para
    
    def remove_newsletter_footer(self, text: str) -> str:
        """
        Remove footer de newsletters
        
        Args:
            text: Texto do email
        
        Returns:
            Texto sem footer
        """
        patterns = [
            r'\n\nYou received this email because.*$',
            r'\n\nVocê recebeu este email porque.*$',
            r'\n\nTo unsubscribe.*$',
            r'\n\nPara cancelar.*$',
            r'\n\nCopyright ©.*$',
            r'\n\n© \d{4}.*$',
        ]
        
        for pattern in patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)
        
        return text
    
    def _remove_signature(self, text: str) -> str:
        """Remove assinatura de email"""
        lines = text.split('\n')
        result = []
        skip = False
        
        for line in lines:
            # Verificar se é início de assinatura
            for pattern in self.SIGNATURE_PATTERNS:
                if re.match(pattern, line, re.IGNORECASE):
                    skip = True
                    break
            
            if not skip:
                result.append(line)
        
        return '\n'.join(result)
    
    def _remove_quotes(self, text: str) -> str:
        """Remove citações/quotes de emails anteriores"""
        lines = text.split('\n')
        result = []
        in_quote = False
        
        for line in lines:
            # Verificar se é linha de quote
            is_quote = False
            for pattern in self.QUOTE_PATTERNS:
                if re.match(pattern, line, re.IGNORECASE):
                    is_quote = True
                    in_quote = True
                    break
            
            if not is_quote:
                # Se estava em quote e encontrou linha não-quote,
                # pode ser fim do quote ou conteúdo novo
                if in_quote and line.strip():
                    in_quote = False
                result.append(line)
        
        return '\n'.join(result)
    
    def extract_urls(self, text: str) -> List[str]:
        """Extrai URLs do texto"""
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        return re.findall(url_pattern, text)
    
    def extract_emails(self, text: str) -> List[str]:
        """Extrai emails do texto"""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        return re.findall(email_pattern, text)
    
    def extract_dates(self, text: str) -> List[str]:
        """Extrai datas do texto"""
        date_patterns = [
            r'\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}',
            r'\d{1,2}\s+de\s+\w+\s+de\s+\d{4}',
            r'\w+\s+\d{1,2},?\s+\d{4}',
        ]
        
        dates = []
        for pattern in date_patterns:
            dates.extend(re.findall(pattern, text, re.IGNORECASE))
        
        return dates
    
    def extract_phone_numbers(self, text: str) -> List[str]:
        """Extrai números de telefone"""
        patterns = [
            r'\(\d{2}\)\s*\d{4,5}[-\s]?\d{4}',
            r'\d{2}\s*\d{4,5}[-\s]?\d{4}',
            r'\+?\d{1,3}[\s\-]?\d{2,4}[\s\-]?\d{4}',
        ]
        
        phones = []
        for pattern in patterns:
            phones.extend(re.findall(pattern, text))
        
        return phones
    
    def detect_language(self, text: str) -> str:
        """
        Detecta idioma do texto (simplificado)
        
        Returns:
            'pt', 'en', ou 'unknown'
        """
        pt_words = ['que', 'não', 'com', 'para', 'você', 'isso', 'uma', 'são', 'tem']
        en_words = ['the', 'is', 'are', 'you', 'this', 'that', 'with', 'have', 'for']
        
        text_lower = text.lower()
        
        pt_count = sum(1 for word in pt_words if f' {word} ' in f' {text_lower} ')
        en_count = sum(1 for word in en_words if f' {word} ' in f' {text_lower} ')
        
        if pt_count > en_count:
            return 'pt'
        elif en_count > pt_count:
            return 'en'
        else:
            return 'unknown'