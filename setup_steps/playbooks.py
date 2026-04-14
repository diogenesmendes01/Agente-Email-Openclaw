"""Step: import example playbooks via subprocess."""

import subprocess
import sys
from pathlib import Path

from setup_steps.common import (
    step_header, ask, ask_choice, confirm, success, error, warning, spinner,
)


def run(project_dir: Path, gmail_accounts: list[dict]) -> bool:
    """Import playbooks for corporate accounts."""
    step_header(7, "Playbooks")

    # Filter accounts with company profiles
    corporate = [a for a in gmail_accounts if a.get("company_id")]

    if not corporate:
        warning("Nenhuma conta corporativa configurada — playbooks não aplicáveis")
        print()
        print("    Você pode importar playbooks a qualquer momento com:")
        print("      python scripts/import_playbooks.py playbooks/seu_arquivo.yaml --account-id <ID>")
        return True

    if not confirm("Deseja importar playbooks de exemplo?"):
        print()
        print("    Você pode importar playbooks a qualquer momento com:")
        print("      python scripts/import_playbooks.py playbooks/seu_arquivo.yaml --account-id <ID>")
        return True

    # Choose YAML file
    example_yaml = project_dir / "playbooks" / "modelo.yaml.example"
    yaml_path_str = ask(
        "Caminho do arquivo YAML",
        default=str(example_yaml) if example_yaml.exists() else "",
    )
    yaml_path = Path(yaml_path_str)
    if not yaml_path.exists():
        error(f"Arquivo não encontrado: {yaml_path}")
        return False

    # Choose account to import for
    if len(corporate) == 1:
        target = corporate[0]
    else:
        options = [f"{a['email']} (ID: {a['account_id']})" for a in corporate]
        idx = ask_choice("Importar playbooks para qual conta?", options)
        target = corporate[idx]

    account_id = target["account_id"]
    with spinner("Importando playbooks..."):
        result = subprocess.run(
            [sys.executable, str(project_dir / "scripts" / "import_playbooks.py"),
             str(yaml_path), "--account-id", str(account_id)],
            capture_output=True, text=True,
        )

    if result.returncode == 0:
        success("Playbooks importados com sucesso")
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"    {line}")
        return True
    else:
        error(f"Falha ao importar: {result.stderr[:300]}")
        return False
