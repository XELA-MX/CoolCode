"""Default UX: chat + slash commands (OpenClaw-style)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from typing import Literal

from prompt_toolkit import PromptSession

from rich import box
from rich.panel import Panel
from rich.table import Table

from perplex_agent import __version__
from perplex_agent.client import PerplexityClient
from perplex_agent.config import (
    SONAR_CHAT_MODELS,
    Settings,
    resolve_sonar_chat_model,
)
from perplex_agent.orchestrator import DIRECT_ASSISTANT_SYSTEM, Orchestrator
from perplex_agent.planning import fallback_plan, propose_execution_plan
from perplex_agent.setup_wizard import ensure_perplexity_configured, ensure_telegram_ready, run_setup_wizard
from perplex_agent.slash_complete import (
    SLASH_HELP,
    SlashCommandCompleter,
    slash_prompt_message,
    slash_prompt_style,
)
from perplex_agent.subagents import extract_message_content
from perplex_agent.telegram_bot import run_telegram_with_gate
from perplex_agent.ui import (
    confirm_or_abort,
    confirm_spawn_batch,
    get_console,
    print_banner,
    print_final_answer,
    print_subagents_history,
    render_direct_preflight,
    render_plan_panel,
)


def _help_table() -> Table:
    t = Table(
        title="Comandos /",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold",
    )
    t.add_column("Comando", style="bright_cyan", no_wrap=True)
    t.add_column("Descripción")
    i = 0
    while i < len(SLASH_HELP):
        cmd, desc = SLASH_HELP[i]
        if cmd == "/quit" and i + 1 < len(SLASH_HELP) and SLASH_HELP[i + 1][0] == "/exit":
            t.add_row("/quit, /exit", desc)
            i += 2
        else:
            t.add_row(cmd, desc)
            i += 1
    return t


def _effective_yes(settings: Settings, shell_flag: bool) -> bool:
    return bool(shell_flag or settings.assume_yes)


def _shell_model(state: "_ShellState", opts: ShellOptions, s: Settings) -> str:
    """Session /model wins, then CLI -m, then TOML/env default."""
    if state.session_model is not None:
        return state.session_model
    if opts.model is not None:
        return opts.model
    return s.perplexity_model


@dataclass
class ShellOptions:
    stream: bool = False
    start_direct: bool = False
    model: str | None = None
    assume_yes: bool = False
    confirm_spawns: bool = False
    confirm_each: bool = False
    plan_each: bool = False


@dataclass
class _ShellState:
    mode: Literal["orchestrator", "direct"] = "orchestrator"
    orchestrator: Orchestrator | None = None
    direct_client: PerplexityClient | None = None
    # If set, overrides CLI -m and config for this shell (see /model).
    session_model: str | None = None


async def run_slash_shell(settings: Settings, opts: ShellOptions) -> None:
    console = get_console()
    print_banner(console)
    console.print(
        Panel(
            "[bold]Chat[/bold] — mensaje libre o [cyan]/help[/cyan]. "
            "Comandos [cyan]/…[/cyan]: sugerencias al escribir y Tab. "
            "[cyan]/model[/cyan] lista o cambia el modelo Sonar. "
            "Salir: [cyan]/quit[/cyan] o Ctrl+D.",
            border_style="dim",
            box=box.SIMPLE,
        )
    )
    state = _ShellState(mode="direct" if opts.start_direct else "orchestrator")

    async def _spawn_gate(tasks: list[tuple[str, str | None]]) -> bool:
        if not opts.confirm_spawns:
            return True
        return confirm_spawn_batch(tasks, console=console)

    def _orch(s: Settings, planner_model: str) -> Orchestrator:
        s_run = replace(s, perplexity_model=planner_model)
        if state.orchestrator is None:
            state.orchestrator = Orchestrator(s_run)
        return state.orchestrator

    session = PromptSession(
        completer=SlashCommandCompleter(),
        complete_while_typing=True,
        style=slash_prompt_style(),
        message=slash_prompt_message(),
    )

    while True:
        try:
            line = (await session.prompt_async()).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not line:
            continue

        if line.startswith("/"):
            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            rest = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/quit", "/exit"):
                console.print("[dim]Hasta luego.[/dim]")
                break

            if cmd == "/help":
                console.print(_help_table())
                continue

            if cmd == "/version":
                console.print(f"[accent]perplex-agent[/accent] {__version__}")
                continue

            if cmd == "/setup":
                sub = rest.lower().split()
                tg_only = bool(sub and sub[0] == "telegram")
                await run_setup_wizard(
                    console=console,
                    perplexity=not tg_only,
                    telegram=True,
                    probe_key=not tg_only,
                )
                settings = Settings.load()
                continue

            if cmd == "/clear":
                state.orchestrator = None
                state.direct_client = None
                console.print("[green]Sesión reiniciada.[/green]")
                continue

            if cmd == "/model":
                arg = rest.strip().lower()
                s_cfg = Settings.load()
                cur = _shell_model(state, opts, s_cfg)
                if not arg or arg == "list":
                    t = Table(
                        title="Modelos Sonar (/v1/sonar)",
                        box=box.ROUNDED,
                        border_style="cyan",
                        show_header=True,
                        header_style="bold",
                    )
                    t.add_column("Modelo", style="bright_cyan", no_wrap=True)
                    t.add_column("Notas", style="white")
                    for mid, blurb in SONAR_CHAT_MODELS:
                        mark = "→ " if mid == cur else ""
                        t.add_row(f"{mark}{mid}", blurb)
                    console.print(t)
                    console.print(
                        f"Activo en esta sesión: [bold]{cur}[/bold]. "
                        "Cambia con [cyan]/model sonar-pro[/cyan] · "
                        "[cyan]/model reset[/cyan] quita el override de sesión."
                    )
                    continue
                if arg in ("reset", "default", "clear"):
                    state.session_model = None
                    state.orchestrator = None
                    state.direct_client = None
                    s2 = Settings.load()
                    nxt = _shell_model(state, opts, s2)
                    console.print(f"[green]Modelo:[/green] [cyan]{nxt}[/cyan] (CLI o TOML)")
                    continue
                token = arg.split()[0]
                picked = resolve_sonar_chat_model(token)
                if picked is None:
                    console.print(
                        f"[yellow]Modelo no permitido:[/yellow] {token!r}. "
                        "Usa [cyan]/model list[/cyan]."
                    )
                    continue
                state.session_model = picked
                state.orchestrator = None
                state.direct_client = None
                console.print(f"[green]Modelo de sesión:[/green] [cyan]{picked}[/cyan]")
                continue

            if cmd == "/mode":
                arg = rest.lower().strip()
                s_m = Settings.load()
                cur_m = _shell_model(state, opts, s_m)
                if not arg:
                    console.print(
                        f"Modo: [bold]{state.mode}[/bold] · modelo [cyan]{cur_m}[/cyan]"
                    )
                    continue
                if arg in ("direct", "d"):
                    state.mode = "direct"
                    console.print("[green]Modo: direct[/green]")
                    continue
                if arg in ("orchestrator", "orch", "o", "default"):
                    state.mode = "orchestrator"
                    state.orchestrator = None
                    console.print("[green]Modo: orchestrator[/green]")
                    continue
                console.print("[yellow]Uso:[/yellow] /mode direct  |  /mode orchestrator")
                continue

            if cmd == "/subagents":
                print_subagents_history(Settings.load(), console=console, limit=35)
                continue

            if cmd == "/telegram":
                if rest.lower().strip() == "run":
                    s = Settings.load()
                    if not await ensure_telegram_ready(console):
                        continue
                    s = Settings.load()
                    run_telegram_with_gate(
                        assume_yes=_effective_yes(s, opts.assume_yes),
                        console=console,
                    )
                    continue
                console.print(
                    Panel(
                        "Long-polling bloquea esta terminal.\n\n"
                        "[bold]Otra terminal:[/bold] [cyan]perplex-agent telegram run[/cyan]\n"
                        "[bold]Aquí:[/bold] [cyan]/telegram run[/cyan]",
                        title="Telegram",
                        border_style="blue",
                    )
                )
                continue

            console.print(f"[yellow]Comando desconocido:[/yellow] {cmd} — /help")
            continue

        # User message
        s = Settings.load()
        if not s.perplexity_api_key:
            if not await ensure_perplexity_configured(console):
                console.print("[dim]Usa [cyan]/setup[/cyan] o exporta PERPLEXITY_API_KEY.[/dim]")
            s = Settings.load()
            if not s.perplexity_api_key:
                continue

        m = _shell_model(state, opts, s)
        dextra = s.extra_for_direct()

        if state.mode == "direct":
            if state.direct_client is None:
                state.direct_client = PerplexityClient(
                    s.perplexity_api_key, timeout_s=s.request_timeout_s
                )
            if not opts.assume_yes:
                render_direct_preflight(line, m, stream=opts.stream, console=console)
                if not confirm_or_abort("¿Enviar?", default=True, console=console):
                    console.print("[dim]Cancelado.[/dim]")
                    continue
            messages = [
                {"role": "system", "content": DIRECT_ASSISTANT_SYSTEM},
                {"role": "user", "content": line},
            ]
            if opts.stream:
                async for chunk in state.direct_client.chat_completion_stream_text(
                    model=m, messages=messages, extra=dextra
                ):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                sys.stdout.write("\n")
            else:
                data = await state.direct_client.chat_completion(
                    model=m, messages=messages, stream=False, extra=dextra
                )
                print_final_answer(extract_message_content(data), console=console)
            continue

        if not opts.assume_yes and (opts.confirm_each or opts.plan_each):
            if opts.plan_each:
                with console.status("[cyan]Plan…[/cyan]", spinner="dots"):
                    plan = await propose_execution_plan(
                        s, line, direct=False, model_for_run=m
                    )
            else:
                plan = fallback_plan(line, direct=False, model=m)
            render_plan_panel(plan, console=console)
            if not confirm_or_abort("¿Ejecutar?", default=True, console=console):
                console.print("[dim]Omitido.[/dim]")
                continue

        orch = _orch(s, m)
        result = await orch.run(
            line,
            stream=False,
            before_spawn_batch=_spawn_gate if opts.confirm_spawns else None,
        )
        print_final_answer(result.final_text, console=console)
