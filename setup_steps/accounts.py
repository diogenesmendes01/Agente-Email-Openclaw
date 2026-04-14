"""Step: create account rows and company profiles in PostgreSQL."""

from pathlib import Path

from setup_steps.common import (
    step_header, ask, ask_choice, confirm, success, error, warning, spinner,
)


def create_account(conn, email: str, hook_token_env: str, topic_id: int | None) -> int:
    """Insert account row, returning id. Uses ON CONFLICT to handle re-runs."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO accounts (email, hook_token_env, telegram_topic_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (email) DO UPDATE SET
                   hook_token_env = EXCLUDED.hook_token_env,
                   telegram_topic_id = COALESCE(EXCLUDED.telegram_topic_id, accounts.telegram_topic_id)
               RETURNING id""",
            (email, hook_token_env, topic_id),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]


def create_company_profile(conn, account_id: int, name: str, cnpj: str,
                           tone: str, signature: str, whatsapp_url: str | None) -> int:
    """Insert company profile, returning id. Upserts on account_id conflict."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO company_profiles (account_id, company_name, cnpj, tone, signature, whatsapp_url)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (account_id) DO UPDATE SET
                   company_name = EXCLUDED.company_name,
                   cnpj = COALESCE(EXCLUDED.cnpj, company_profiles.cnpj),
                   tone = COALESCE(EXCLUDED.tone, company_profiles.tone),
                   signature = COALESCE(EXCLUDED.signature, company_profiles.signature),
                   whatsapp_url = COALESCE(EXCLUDED.whatsapp_url, company_profiles.whatsapp_url),
                   updated_at = NOW()
               RETURNING id""",
            (account_id, name, cnpj, tone, signature, whatsapp_url),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0]


def run(project_dir: Path, env: dict, gmail_accounts: list[dict]) -> list[dict]:
    """Create DB entries for Gmail accounts. Returns enriched account list."""
    import psycopg2

    step_header(6, "Contas no Banco de Dados")

    database_url = env.get("DATABASE_URL", "")
    if not database_url:
        error("DATABASE_URL não disponível — pulando criação de contas")
        return gmail_accounts

    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
    except Exception as e:
        error(f"Falha ao conectar ao banco: {e}")
        return gmail_accounts

    try:
        for account in gmail_accounts:
            topic_id = None
            topic_id_str = ask(
                f"Telegram Topic ID para {account['email']} (deixe vazio se não usar tópicos)",
                default="",
            )
            if topic_id_str.strip():
                try:
                    topic_id = int(topic_id_str.strip())
                except ValueError:
                    warning("Valor inválido — salvando sem topic_id")

            with spinner(f"Criando conta {account['email']}..."):
                account_id = create_account(
                    conn, account["email"], account["hook_token_env"], topic_id,
                )
            account["account_id"] = account_id
            success(f"Conta #{account_id}: {account['email']}")

            if account.get("is_corporate"):
                print()
                success("Configuração do perfil empresarial:")
                company_name = ask("Nome da empresa")
                cnpj = ask("CNPJ (opcional)", default="")
                tone_idx = ask_choice("Tom de comunicação:", [
                    "Formal", "Informal", "Técnico", "Empático",
                ])
                tone = ["formal", "informal", "técnico", "empático"][tone_idx]
                signature = ask("Assinatura de email (uma linha)")
                whatsapp = ask("URL WhatsApp (opcional)", default="")

                profile_id = create_company_profile(
                    conn, account_id, company_name,
                    cnpj or None, tone, signature, whatsapp or None,
                )
                account["company_id"] = profile_id
                success(f"Perfil empresarial #{profile_id}: {company_name}")

        return gmail_accounts
    except Exception as e:
        error(f"Erro ao criar contas: {e}")
        return gmail_accounts
    finally:
        conn.close()
