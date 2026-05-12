"""Typer CLI entrypoint with Rich UI, execution plans, and confirmations."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.panel import Panel

from perplex_agent.client import PerplexityClient
from perplex_agent.config import Settings
from perplex_agent.direct_tools import run_direct_tool_session
from perplex_agent.orchestrator import Orchestrator, direct_system_for_settings
from perplex_agent.planning import fallback_plan, propose_execution_plan
from perplex_agent.setup_wizard import (
    ensure_perplexity_configured,
    ensure_telegram_ready,
    run_setup_wizard,
)
from perplex_agent.slash_shell import ShellOptions, run_slash_shell
from perplex_agent.subagents import extract_message_content
from perplex_agent.telegram_bot import run_telegram_with_gate
from perplex_agent.ui import (
    confirm_or_abort,
    confirm_spawn_batch,
    get_console,
    print_final_answer,
    render_direct_preflight,
    render_plan_panel,
    print_subagents_history,
)

app = typer.Typer(help="Perplexity Sonar CLI agent with subagents and Telegram.")

subagents_app = typer.Typer(help="Inspect persisted subagent runs.")
telegram_app = typer.Typer(help="Run the Telegram bot.")


def _run_interactive_shell(workspace: Path | None) -> None:
    opts = ShellOptions(workspace=workspace)
    try:
        settings = Settings.load(workspace_override=workspace)
    except ValueError as e:
        get_console().print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    asyncio.run(run_slash_shell(settings, opts))


def _effective_yes(settings: Settings, yes_flag: bool) -> bool:
    return bool(yes_flag or settings.assume_yes)


@app.callback(invoke_without_command=True)
def _root_callback(
    ctx: typer.Context,
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-C",
        metavar="DIR",
        help="Raíz del workspace (también PERPLEX_AGENT_WORKSPACE o [workspace] en TOML).",
    ),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = workspace
    if ctx.invoked_subcommand is not None:
        return
    _run_interactive_shell(workspace)


@app.command("chat")
def chat_cmd(
    ctx: typer.Context,
    message: str | None = typer.Argument(default=None, show_default=False),
    repl: bool = typer.Option(
        False,
        "--repl",
        "-i",
        help="Mismo chat interactivo (por defecto ya no hace falta si no pasas mensaje).",
    ),
    stream: bool = typer.Option(False, "--stream", help="Stream tokens (direct mode only)."),
    direct: bool = typer.Option(False, "--direct", help="Single Sonar completion without orchestrator."),
    model: str | None = typer.Option(None, "--model", "-m", help="Override Perplexity model."),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-C",
        metavar="DIR",
        help="Raíz del workspace para esta sesión o mensaje.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip plans and confirmations."),
    no_plan: bool = typer.Option(
        False,
        "--no-plan",
        help="Orchestrator one-shot: skip API planning; local summary + confirm.",
    ),
    confirm_spawns: bool = typer.Option(
        False,
        "--confirm-spawns",
        help="Ask before each subagent batch (orchestrator only).",
    ),
    confirm_each: bool = typer.Option(
        False,
        "--confirm-each",
        help="Shell: confirmación antes de cada mensaje (orquestador).",
    ),
    plan_each: bool = typer.Option(
        False,
        "--plan-each",
        help="Shell: plan API antes de cada mensaje (orquestador).",
    ),
) -> None:
    ctx.ensure_object(dict)
    effective_ws: Path | None = workspace if workspace is not None else ctx.obj.get("workspace")

    console = get_console()
    try:
        settings = Settings.load(workspace_override=effective_ws)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    assume = _effective_yes(settings, yes)

    if repl or message is None:
        opts = ShellOptions(
            stream=stream,
            start_direct=direct,
            model=model,
            assume_yes=assume,
            confirm_spawns=confirm_spawns,
            confirm_each=confirm_each or plan_each,
            plan_each=plan_each,
            workspace=effective_ws,
        )
        asyncio.run(run_slash_shell(settings, opts))
        return

    if not asyncio.run(ensure_perplexity_configured(console)):
        console.print(
            "[red]Se necesita PERPLEXITY_API_KEY.[/red] "
            "Configúrala con [cyan]perplex-agent setup[/cyan], [cyan]/setup[/cyan] en el chat, "
            "o exporta la variable."
        )
        raise typer.Exit(1)
    settings = Settings.load(workspace_override=effective_ws)
    asyncio.run(
        _run_chat_flow(
            settings,
            message,
            direct=direct,
            stream=stream,
            model=model,
            no_plan=no_plan,
            confirm_spawns=confirm_spawns,
            assume_yes=assume,
            console=get_console(),
        )
    )


async def _run_chat_flow(
    settings: Settings,
    message: str,
    *,
    direct: bool,
    stream: bool,
    model: str | None,
    no_plan: bool,
    confirm_spawns: bool,
    assume_yes: bool,
    console: Any,
) -> None:
    m = model or settings.perplexity_model
    if not assume_yes:
        if direct:
            render_direct_preflight(message, m, stream=stream, console=console)
            if not confirm_or_abort("¿Enviar esta consulta?", default=True, console=console):
                console.print("[yellow]Cancelado.[/yellow]")
                return
        else:
            if no_plan:
                plan = fallback_plan(message, direct=False, model=m)
            else:
                with console.status("[cyan]Generando plan de ejecución…[/cyan]", spinner="dots"):
                    plan = await propose_execution_plan(
                        settings, message, direct=False, model_for_run=m
                    )
            render_plan_panel(plan, console=console)
            if not confirm_or_abort("¿Ejecutar este plan?", default=False, console=console):
                console.print("[yellow]Cancelado.[/yellow]")
                return

    if direct:
        client = PerplexityClient(settings.perplexity_api_key, timeout_s=settings.request_timeout_s)
        messages = [
            {"role": "system", "content": direct_system_for_settings(settings)},
            {"role": "user", "content": message},
        ]
        dextra = settings.extra_for_direct()
        if settings.direct_tools_enabled and not stream:
            answer = await run_direct_tool_session(
                client,
                settings,
                m,
                message,
                system_prompt=direct_system_for_settings(settings),
                extra=dextra,
            )
            print_final_answer(answer, console=console)
        elif stream:

            async def _stream() -> None:
                async for chunk in client.chat_completion_stream_text(
                    model=m, messages=messages, extra=dextra
                ):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                sys.stdout.write("\n")

            await _stream()
        else:
            data = await client.chat_completion(
                model=m, messages=messages, stream=False, extra=dextra
            )
            print_final_answer(extract_message_content(data), console=console)
        return

    if stream:
        console.print(
            "[dim]--stream solo aplica a --direct; el orquestador corre sin stream.[/dim]"
        )

    async def _spawn_gate(tasks: list[tuple[str, str | None]]) -> bool:
        if not confirm_spawns:
            return True
        return confirm_spawn_batch(tasks, console=console)

    orch = Orchestrator(settings)
    result = await orch.run(
        message,
        stream=False,
        before_spawn_batch=_spawn_gate if confirm_spawns else None,
    )
    print_final_answer(result.final_text, console=console)


@subagents_app.command("list")
def subagents_list(
    limit: int = typer.Option(40, "--limit", "-n", help="Max rows to show."),
    plain: bool = typer.Option(False, "--plain", help="Minimal output (no Rich table)."),
) -> None:
    settings = Settings.load()
    path = settings.subagent_state_file
    if plain or not path.is_file():
        if not path.is_file():
            typer.echo(f"No state file yet. Expected: {path}")
            raise typer.Exit(0)
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            typer.echo("Invalid state file format.", err=True)
            raise typer.Exit(1)
        for row in rows[-limit:]:
            if not isinstance(row, dict):
                continue
            rid = row.get("id", "?")
            status = row.get("status", "?")
            mod = row.get("model", "?")
            typer.echo(f"{rid}\t{status}\t{mod}")
            instr = str(row.get("instruction", ""))[:200]
            typer.echo(f"  {instr}")
        typer.echo(f"(file: {path})")
        return
    print_subagents_history(settings, console=get_console(), limit=limit)


@app.command("setup")
def setup_cmd(
    telegram_only: bool = typer.Option(
        False, "--telegram-only", help="Solo token / allowlist de Telegram."
    ),
) -> None:
    """Asistente interactivo: guarda en ~/.config/perplex-agent/config.toml."""
    console = get_console()
    asyncio.run(
        run_setup_wizard(
            console=console,
            perplexity=not telegram_only,
            telegram=True,
            probe_key=not telegram_only,
        )
    )


@telegram_app.command("run")
def telegram_run_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    console = get_console()
    if not asyncio.run(ensure_telegram_ready(console)):
        raise typer.Exit(1)
    settings = Settings.load()
    run_telegram_with_gate(
        assume_yes=_effective_yes(settings, yes),
        console=console,
    )


app.add_typer(subagents_app, name="subagents")
app.add_typer(telegram_app, name="telegram")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
