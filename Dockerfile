FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Criar usuário não-root
RUN useradd -m -r -s /bin/false appuser

# Copiar código da aplicação (sem config.json — montado via volume)
COPY orchestrator/ orchestrator/
COPY telegram_poller.py .
COPY vip_manager.py .

# Criar arquivos de estado se não existirem
RUN echo '[]' > vip-list.json && echo '[]' > blacklist.json && echo '[]' > feedback.json && echo '{}' > pending_actions.json && echo '{}' > pending_replies.json

# Ajustar permissões
RUN chown -R appuser:appuser /app

ENV PYTHONPATH=/app
ENV EMAIL_AGENT_BASE_DIR=/app

# Rodar como usuário não-root
USER appuser

EXPOSE 8787
