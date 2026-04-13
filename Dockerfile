FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código da aplicação
COPY orchestrator/ orchestrator/
COPY telegram_poller.py .
COPY vip_manager.py .
COPY config.json .

# Criar arquivos de estado se não existirem
RUN echo '[]' > vip-list.json && echo '[]' > blacklist.json && echo '[]' > feedback.json && echo '{}' > pending_actions.json && echo '{}' > pending_replies.json

ENV PYTHONPATH=/app
ENV EMAIL_AGENT_BASE_DIR=/app

EXPOSE 8787
