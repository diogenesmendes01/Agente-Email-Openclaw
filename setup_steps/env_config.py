"""Step: collect environment variables interactively and generate .env."""

import shutil
import uuid
from pathlib import Path
from urllib.parse import quote as url_quote

from setup_steps.common import (
    step_header, ask, ask_password, confirm, success, warning,
)


def build_database_url(host: str, port: str, dbname: str, user: str, password: str) -> str:
    """Build PostgreSQL connection URL with properly encoded password."""
    encoded_pw = url_quote(password, safe="")
    return f"postgresql://{user}:{encoded_pw}@{host}:{port}/{dbname}"


def parse_existing_env(env_path: Path) -> dict:
    """Load existing .env values as a dict. Returns {} if file doesn't exist."""
    if not env_path.exists():
        return {}
    values = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def write_env_file(env_path: Path, data: dict):
    """Write .env file. Creates backup if file already exists."""
    if env_path.exists():
        backup = env_path.parent / ".env.backup"
        shutil.copy2(env_path, backup)
        warning(f"Backup salvo em {backup.name}")

    sections = {
        "Database": ["DATABASE_URL", "POSTGRES_PASSWORD"],
        "OpenRouter (LLM)": ["OPENROUTER_API_KEY"],
        "OpenAI (Embeddings)": ["OPENAI_API_KEY"],
        "LLM": ["LLM_MODEL", "LLM_VISION_MODEL"],
        "Telegram": [
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_ALLOWED_USER_IDS",
            "TELEGRAM_ALERT_USER_ID",
        ],
        "Gmail Pub/Sub": ["GMAIL_PUBSUB_TOPIC"],
        "Tailscale": ["FUNNEL_BASE_URL"],
        "Qdrant": ["QDRANT_HOST", "QDRANT_PORT"],
        "Observabilidade": [
            "LEARNING_INTERVAL", "METRICS_RETENTION_DAYS",
            "ALERT_THROTTLE_MINUTES", "JOB_MAX_ATTEMPTS",
        ],
    }

    lines = [
        "# ============================================================",
        "# Email Agent — Gerado pelo Setup Wizard",
        "# ============================================================",
        "",
    ]

    written_keys = set()
    for section_name, keys in sections.items():
        lines.append(f"# --- {section_name} ---")
        for key in keys:
            if key in data:
                lines.append(f"{key}={data[key]}")
                written_keys.add(key)
        lines.append("")

    gmail_keys = sorted([k for k in data if k.startswith("GMAIL_ACCOUNT_")])
    if gmail_keys:
        lines.append("# --- Gmail Accounts ---")
        for gk in gmail_keys:
            num = gk.split("_")[-1]
            lines.append(f"{gk}={data[gk]}")
            token_key = f"GMAIL_HOOK_TOKEN_{num}"
            if token_key in data:
                lines.append(f"{token_key}={data[token_key]}")
                written_keys.add(token_key)
            written_keys.add(gk)
        lines.append("")

    remaining = {k: v for k, v in data.items() if k not in written_keys}
    if remaining:
        lines.append("# --- Outros ---")
        for k, v in remaining.items():
            lines.append(f"{k}={v}")
        lines.append("")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(project_dir: Path, existing: dict = None) -> dict:
    """Collect env vars interactively. Returns the complete env dict."""
    step_header(2, "Variáveis de Ambiente (.env)")

    defaults = existing or parse_existing_env(project_dir / ".env")

    env = {}

    # --- PostgreSQL ---
    print()
    success("PostgreSQL")
    db_host = ask("Host", default=defaults.get("_DB_HOST", "localhost"))
    db_port = ask("Porta", default=defaults.get("_DB_PORT", "5432"))
    db_name = ask("Nome do banco", default=defaults.get("_DB_NAME", "emailagent"))
    db_user = ask("Usuário", default=defaults.get("_DB_USER", "emailagent"))
    db_pass = ask_password("Senha")
    env["DATABASE_URL"] = build_database_url(db_host, db_port, db_name, db_user, db_pass)
    env["POSTGRES_PASSWORD"] = db_pass
    env["_DB_HOST"] = db_host
    env["_DB_PORT"] = db_port
    env["_DB_NAME"] = db_name
    env["_DB_USER"] = db_user

    # --- LLM ---
    print()
    success("LLM / API Keys")
    env["OPENROUTER_API_KEY"] = ask_password("OpenRouter API Key") or defaults.get("OPENROUTER_API_KEY", "")
    env["OPENAI_API_KEY"] = ask_password("OpenAI API Key (embeddings)") or defaults.get("OPENAI_API_KEY", "")
    env["LLM_MODEL"] = ask("Modelo LLM", default=defaults.get("LLM_MODEL", "google/gemini-2.5-flash"))
    env["LLM_VISION_MODEL"] = ask("Modelo Vision", default=defaults.get("LLM_VISION_MODEL", "google/gemini-2.5-flash"))

    # --- Telegram ---
    print()
    success("Telegram")
    env["TELEGRAM_BOT_TOKEN"] = ask_password("Token do Bot") or defaults.get("TELEGRAM_BOT_TOKEN", "")
    env["TELEGRAM_CHAT_ID"] = ask("Chat ID", default=defaults.get("TELEGRAM_CHAT_ID", ""))
    env["TELEGRAM_WEBHOOK_SECRET"] = ask(
        "Webhook Secret", default=defaults.get("TELEGRAM_WEBHOOK_SECRET", uuid.uuid4().hex[:16])
    )
    env["TELEGRAM_ALLOWED_USER_IDS"] = ask(
        "IDs de usuários permitidos (separados por vírgula)",
        default=defaults.get("TELEGRAM_ALLOWED_USER_IDS", ""),
    )
    env["TELEGRAM_ALERT_USER_ID"] = ask(
        "ID do usuário para alertas",
        default=defaults.get("TELEGRAM_ALERT_USER_ID", ""),
    )

    # --- Gmail Pub/Sub ---
    print()
    success("Gmail Pub/Sub")
    env["GMAIL_PUBSUB_TOPIC"] = ask(
        "Pub/Sub Topic (ex: projects/meu-proj/topics/gmail-watch)",
        default=defaults.get("GMAIL_PUBSUB_TOPIC", ""),
    )

    # --- Network ---
    print()
    success("Rede")
    env["FUNNEL_BASE_URL"] = ask("Funnel Base URL (Tailscale)", default=defaults.get("FUNNEL_BASE_URL", ""))

    # --- Qdrant ---
    print()
    success("Qdrant")
    env["QDRANT_HOST"] = ask("Host", default=defaults.get("QDRANT_HOST", "localhost"))
    env["QDRANT_PORT"] = ask("Porta", default=defaults.get("QDRANT_PORT", "6333"))

    # --- Optional ---
    print()
    if confirm("Configurar opções avançadas?", default=False):
        env["LEARNING_INTERVAL"] = ask("Learning Interval", default=defaults.get("LEARNING_INTERVAL", "50"))
        env["METRICS_RETENTION_DAYS"] = ask("Metrics Retention (dias)", default=defaults.get("METRICS_RETENTION_DAYS", "90"))
        env["ALERT_THROTTLE_MINUTES"] = ask("Alert Throttle (minutos)", default=defaults.get("ALERT_THROTTLE_MINUTES", "15"))
        env["JOB_MAX_ATTEMPTS"] = ask("Job Max Attempts", default=defaults.get("JOB_MAX_ATTEMPTS", "5"))
    else:
        for key, default_val in [
            ("LEARNING_INTERVAL", "50"), ("METRICS_RETENTION_DAYS", "90"),
            ("ALERT_THROTTLE_MINUTES", "15"), ("JOB_MAX_ATTEMPTS", "5"),
        ]:
            env[key] = defaults.get(key, default_val)

    # Preserve existing Gmail accounts
    for key, val in defaults.items():
        if key.startswith("GMAIL_ACCOUNT_") or key.startswith("GMAIL_HOOK_TOKEN_"):
            env[key] = val

    # Write .env
    env_path = project_dir / ".env"
    write_env_file(env_path, env)
    success(f".env gerado em {env_path}")

    return env
