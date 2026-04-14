"""Step: install Python dependencies from requirements.txt."""

import subprocess
import sys
from pathlib import Path

from setup_steps.common import step_header, success, error, warning, confirm, spinner


def run(project_dir: Path):
    """Install requirements.txt with pip."""
    step_header(1, "Dependências Python")

    req_file = project_dir / "requirements.txt"
    if not req_file.exists():
        error(f"requirements.txt não encontrado em {project_dir}")
        return False

    with spinner("Instalando dependências..."):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
            capture_output=True, text=True,
        )

    if result.returncode == 0:
        success("Dependências instaladas com sucesso")
        return True
    else:
        error("Falha ao instalar dependências")
        print(f"    {result.stderr[:500]}")
        if confirm("Deseja continuar mesmo assim?", default=False):
            return True
        return False
