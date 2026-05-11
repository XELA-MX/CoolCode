"""Rich-based console UI: banner, menus, plans, confirmations."""

from __future__ import annotations

import json
import os
import textwrap
import time
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.theme import Theme

from perplex_agent.config import Settings
from perplex_agent.planning import ExecutionPlan

CUSTOM_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "danger": "red bold",
        "muted": "dim",
        "accent": "bold bright_cyan",
        "title": "bold bright_white",
    }
)


def get_console() -> Console:
    return Console(theme=CUSTOM_THEME)


# Block letters "COOLCODE" (~72 cols), UTF-8 box drawing — looks sharp on modern terminals.
_COOLCODE_ASCII: tuple[str, ...] = (
    r"  ██████╗ ██████╗  ██████╗ ██╗         ██████╗ ██████╗ ██████╗ ███████╗",
    r" ██╔════╝██╔═══██╗██╔═══██╗██║        ██╔════╝██╔═══██╗██╔══██╗██╔════╝",
    r" ██║     ██║   ██║██║   ██║██║        ██║     ██║   ██║██║  ██║█████╗  ",
    r" ██║     ██║   ██║██║   ██║██║        ██║     ██║   ██║██║  ██║██╔══╝  ",
    r" ╚██████╗╚██████╔╝╚██████╔╝███████╗   ╚██████╗╚██████╔╝██████╔╝███████╗",
    r"  ╚═════╝ ╚═════╝  ╚═════╝ ╚══════╝    ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
)


def _banner_line_style(index: int, total: int) -> str:
    """Sweep bright → dim so the logo feels like it 'lights up' top to bottom."""
    if total <= 1:
        return "bold bright_cyan"
    t = index / (total - 1)
    if t < 0.34:
        return "bold bright_cyan"
    if t < 0.67:
        return "cyan"
    return "dim cyan"


def print_banner(console: Console | None = None, *, animate: bool = True) -> None:
    c = console or get_console()
    animate = bool(animate and not os.environ.get("PERPLEX_AGENT_NO_BANNER_ANIM"))
    # We clear the console so we can print the banner in a clean way
    c.clear()
    lines = _COOLCODE_ASCII
    n = len(lines)
    if animate and c.is_terminal:
        for i, raw in enumerate(lines):
            c.print(f"[{_banner_line_style(i, n)}]{raw}[/]")
            time.sleep(0.045)
        time.sleep(0.06)
    else:
        block = "\n".join(f"[{_banner_line_style(i, n)}]{ln}[/]" for i, ln in enumerate(lines))
        c.print(block)

    c.print()  # air before the product panel
    title = "[accent]perplex-agent[/accent] · Perplexity Sonar + subagentes + Telegram"
    subtitle = "[muted]Made by: Emilio Peralta[/muted]"
    c.print(
        Panel.fit(
            Group(title, "", subtitle),
            box=box.ROUNDED,
            border_style="bright_cyan",
            padding=(1, 2),
        )
    )

    # Now we print a separator line by the console width
    c.print("─" * c.width)


def render_plan_panel(plan: ExecutionPlan, console: Console | None = None) -> None:
    c = console or get_console()
    mode_es = "Directo (1 llamada)" if plan.mode == "direct" else "Orquestador (varias llamadas, subagentes posibles)"
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    table.add_column("Clave", style="muted", width=22)
    table.add_column("Valor", style="white")
    table.add_row("Modo", mode_es)
    table.add_row("Subagentes", "Sí, posibles" if plan.may_spawn_subagents else "No previstos")
    table.add_row("API (estim.)", plan.estimated_api_calls)

    step_lines = (
        "\n".join(f"  [dim]{i + 1}.[/dim] {escape(s)}" for i, s in enumerate(plan.steps))
        or "  [dim](vacío)[/dim]"
    )
    body = Group(
        f"[title]{escape(plan.title)}[/title]\n",
        f"[info]{escape(plan.summary)}[/info]\n",
        table,
        "",
        "[muted]Pasos previstos[/muted]\n",
        step_lines,
    )
    c.print(
        Panel(
            body,
            title="[accent]Plan de ejecución[/accent]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )


def render_direct_preflight(
    message: str,
    model: str,
    *,
    stream: bool,
    console: Console | None = None,
) -> None:
    c = console or get_console()
    preview = textwrap.shorten(message.replace("\n", " "), width=280, placeholder="…")
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Campo", style="muted")
    table.add_column("Valor", style="white")
    table.add_row("Modelo", model)
    table.add_row("Streaming", "Sí" if stream else "No")
    table.add_row("Mensaje", preview)
    c.print(
        Panel(
            table,
            title="[accent]Acción: consulta directa[/accent]",
            border_style="green",
            box=box.ROUNDED,
        )
    )


def confirm_or_abort(
    prompt: str = "¿Ejecutar esta acción?",
    *,
    default: bool = False,
    console: Console | None = None,
) -> bool:
    c = console or get_console()
    return Confirm.ask(prompt, default=default, console=c)


def confirm_spawn_batch(
    tasks: list[tuple[str, str | None]],
    *,
    console: Console | None = None,
) -> bool:
    c = console or get_console()
    table = Table(title="Subagentes a lanzar", box=box.ROUNDED, border_style="yellow")
    table.add_column("#", style="dim", width=3)
    table.add_column("Modelo", style="cyan", width=14)
    table.add_column("Instrucción", style="white")
    for i, (instr, model) in enumerate(tasks, 1):
        table.add_row(str(i), model or "(default)", textwrap.shorten(instr, 120, placeholder="…"))
    c.print(Panel(table, border_style="yellow"))
    return Confirm.ask("¿Lanzar estos subagentes ahora?", default=True, console=c)


def render_subagents_table(rows: list[dict[str, Any]], path: str, console: Console | None = None) -> None:
    c = console or get_console()
    if not rows:
        c.print(Panel("[muted]Sin registros.[/muted]", title="Subagentes", border_style="dim"))
        return
    table = Table(box=box.ROUNDED, border_style="magenta", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True, max_width=14)
    table.add_column("Estado", style="yellow")
    table.add_column("Modelo", style="green")
    table.add_column("Instrucción", style="white", max_width=48)
    for row in rows:
        instr = str(row.get("instruction", ""))
        table.add_row(
            str(row.get("id", "?")),
            str(row.get("status", "?")),
            str(row.get("model", "?")),
            textwrap.shorten(instr, 90, placeholder="…"),
        )
    c.print(Panel(table, title="[accent]Historial de subagentes[/accent]", subtitle=path))


def print_subagents_history(
    settings: Settings, *, console: Console | None = None, limit: int = 40
) -> None:
    c = console or get_console()
    path = settings.subagent_state_file
    if not path.is_file():
        c.print(f"[dim]Sin archivo aún: {path}[/dim]")
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        c.print("[red]Formato inválido en state file.[/red]")
        return
    rows = [r for r in raw if isinstance(r, dict)]
    render_subagents_table(rows[-limit:], str(path), console=c)


def print_final_answer(text: str, console: Console | None = None) -> None:
    c = console or get_console()
    c.print()
    c.print(
        Panel(
            text,
            title="[accent]Respuesta[/accent]",
            border_style="green",
            box=box.ROUNDED,
        )
    )


def render_telegram_preflight(settings: Settings, console: Console | None = None) -> None:
    c = console or get_console()
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Campo", style="muted", width=22)
    table.add_column("Valor", style="white")
    table.add_row("Long polling", "Sí (bloquea esta terminal)")
    table.add_row(
        "Allowlist",
        ", ".join(str(x) for x in settings.telegram_allowed_user_ids) or "[warning]ninguno (abierto a todos)[/warning]",
    )
    table.add_row("Anunciar subagentes", "Sí" if settings.announce_subagents else "No")
    c.print(
        Panel(
            table,
            title="[accent]Iniciar bot de Telegram[/accent]",
            border_style="bright_blue",
            box=box.ROUNDED,
        )
    )


def prompt_multiline_message(console: Console | None = None) -> str | None:
    c = console or get_console()
    c.print(
        "[muted]Escribe tu mensaje (una línea). Para varias líneas, termina con \\ al final de la línea "
        "o pega todo seguido. Vacío cancela.[/muted]"
    )
    first = Prompt.ask("[accent]Mensaje[/accent]", default="", show_default=False, console=c)
    if not first.strip():
        return None
    if first.rstrip().endswith("\\"):
        lines = [first.rstrip()[:-1]]
        while True:
            extra = Prompt.ask("[dim]siguiente línea[/dim]", default="", show_default=False, console=c)
            if not extra:
                break
            if extra.rstrip().endswith("\\"):
                lines.append(extra.rstrip()[:-1])
            else:
                lines.append(extra)
                break
        return "\n".join(lines).strip()
    return first.strip()


