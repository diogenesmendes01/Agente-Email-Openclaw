"""Shared UI helpers for the setup wizard."""

import getpass
import sys

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    console = Console()
except ImportError:
    console = None


def ask(prompt: str, default: str = None) -> str:
    """Ask for text input with optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value if value else (default or "")


def ask_password(prompt: str) -> str:
    """Ask for password input (masked)."""
    return getpass.getpass(f"  {prompt}: ")


def confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no confirmation. Returns bool."""
    hint = "S/n" if default else "s/N"
    try:
        value = input(f"  {prompt} [{hint}]: ").strip().lower()
    except EOFError:
        value = ""
    if not value:
        return default
    return value in ("s", "sim", "y", "yes")


def ask_choice(prompt: str, choices: list[str]) -> int:
    """Numbered menu. Returns 0-based index of selected choice."""
    if console:
        console.print(f"\n  [bold]{prompt}[/bold]")
    else:
        print(f"\n  {prompt}")
    for i, choice in enumerate(choices, 1):
        if console:
            console.print(f"    [cyan][{i}][/cyan] {choice}")
        else:
            print(f"    [{i}] {choice}")
    while True:
        try:
            value = input("  Escolha: ").strip()
            idx = int(value) - 1
            if 0 <= idx < len(choices):
                return idx
        except (ValueError, EOFError):
            pass
        msg = f"  Digite um número entre 1 e {len(choices)}"
        if console:
            console.print(f"  [red]{msg}[/red]")
        else:
            print(msg)


def banner():
    """Print the wizard banner."""
    if console:
        text = Text("Agente Email - Setup Interativo", style="bold white")
        console.print(Panel(text, border_style="cyan", padding=(1, 4)))
    else:
        print("=" * 40)
        print("  Agente Email - Setup Interativo")
        print("=" * 40)


def step_header(number: int, title: str):
    """Print a step header."""
    if console:
        console.print(f"\n  [bold cyan]━━ Passo {number}: {title} ━━[/bold cyan]\n")
    else:
        print(f"\n  ━━ Passo {number}: {title} ━━\n")


def success(msg: str):
    """Print success message."""
    if console:
        console.print(f"  [green]✔[/green] {msg}")
    else:
        print(f"  ✔ {msg}")


def error(msg: str):
    """Print error message."""
    if console:
        console.print(f"  [red]✘[/red] {msg}")
    else:
        print(f"  ✘ {msg}")


def warning(msg: str):
    """Print warning message."""
    if console:
        console.print(f"  [yellow]⚠[/yellow] {msg}")
    else:
        print(f"  ⚠ {msg}")


def spinner(msg: str):
    """Return a rich spinner context manager, or a no-op fallback."""
    if console:
        return console.status(f"  {msg}", spinner="dots")

    class _NoOpSpinner:
        def __enter__(self):
            print(f"  {msg}...")
            return self
        def __exit__(self, *args):
            pass

    return _NoOpSpinner()
