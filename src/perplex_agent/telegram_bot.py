"""Telegram long-polling bot wired to the orchestrator."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from perplex_agent.config import Settings
from perplex_agent.orchestrator import Orchestrator
from perplex_agent.subagents import SubagentRecord, SubagentStatus
from perplex_agent.telegram_util import split_text

log = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Send any text message. I use Perplexity Sonar with optional subagents. "
            "Use /help for details."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "This bot delegates to a local Perplexity-backed orchestrator.\n"
            "Configure PERPLEXITY_API_KEY and optionally TELEGRAM_ALLOWED_USER_IDS."
        )


def _authorized(settings: Settings, user_id: int | None) -> bool:
    if not settings.telegram_allowed_user_ids:
        return True
    if user_id is None:
        return False
    return user_id in settings.telegram_allowed_user_ids


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    settings: Settings = context.application.bot_data["settings"]
    user = update.effective_user
    if not _authorized(settings, user.id if user else None):
        await message.reply_text("Unauthorized.")
        return

    text = message.text.strip()
    if not text:
        return

    async def on_sub(rec: SubagentRecord) -> None:
        if not settings.announce_subagents:
            return
        if rec.status not in (SubagentStatus.DONE, SubagentStatus.FAILED, SubagentStatus.CANCELLED):
            return
        line = f"Subagent {rec.id}: {rec.status.value}"
        if rec.error:
            line += f" ({rec.error})"
        elif rec.result:
            snippet = rec.result.replace("\n", " ")[:160]
            line += f" — {snippet}"
        for chunk in split_text(line):
            try:
                await message.reply_text(chunk)
            except Exception:  # noqa: BLE001
                log.exception("announce subagent failed")

    orch = Orchestrator(settings, on_subagent_complete=on_sub, tool_result_channel="telegram")
    try:
        result = await orch.run(text, stream=False)
    except Exception as e:  # noqa: BLE001
        log.exception("orchestrator failed")
        await message.reply_text(f"Error: {e}")
        return

    for part in split_text(result.final_text):
        await message.reply_text(part)


def _telegram_polling_main(*, for_background_thread: bool = False) -> None:
    """Build the app and block in PTB polling (must run without a foreign asyncio loop).

    On a non-main thread, asyncio cannot install signal handlers; pass ``stop_signals=None``
    so python-telegram-bot skips ``add_signal_handler`` (see PTB docs for :meth:`run_polling`).
    """
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    settings = Settings.load()
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN to run the Telegram bot.")
    if not settings.perplexity_api_key:
        raise SystemExit("Set PERPLEXITY_API_KEY.")

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Starting Telegram polling…")
    poll_kw: dict[str, Any] = {"allowed_updates": Update.ALL_TYPES}
    if for_background_thread:
        poll_kw["stop_signals"] = None
    application.run_polling(**poll_kw)


def run_polling_blocking(
    *,
    console: Any | None = None,
    force_daemon_thread: bool = False,
) -> None:
    """Start long polling on the current thread, or on a daemon thread if asyncio is already running."""
    use_thread = force_daemon_thread
    if not use_thread:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _telegram_polling_main()
            return
        use_thread = True

    c = console
    if c is not None:
        c.print(
            "[green]Bot de Telegram en segundo plano[/green] (hilo dedicado). "
            "Puedes seguir usando el chat; al salir de perplex-agent el bot se detiene. "
            "[dim]Ctrl+C solo afecta al hilo principal; cierra con /quit o Ctrl+D.[/dim]"
        )
    thread = threading.Thread(
        target=_telegram_polling_main,
        kwargs={"for_background_thread": True},
        name="perplex-agent-telegram",
        daemon=True,
    )
    thread.start()


def run_telegram_with_gate(
    *,
    assume_yes: bool,
    console: Any | None = None,
    force_daemon_thread: bool = False,
) -> None:
    """Confirm + start long-polling (blocking). Used from CLI and slash shell."""
    from rich.console import Console

    from perplex_agent.ui import confirm_or_abort, get_console, render_telegram_preflight

    c: Console = console or get_console()
    s = Settings.load()
    if not s.telegram_bot_token:
        c.print("[red]Falta TELEGRAM_BOT_TOKEN.[/red]")
        return
    if not s.perplexity_api_key:
        c.print("[red]Falta PERPLEXITY_API_KEY.[/red]")
        return
    if not assume_yes:
        render_telegram_preflight(s, console=c)
        if not confirm_or_abort(
            "¿Iniciar el bot ahora? (bloquea esta terminal)", default=False, console=c
        ):
            c.print("[yellow]Cancelado.[/yellow]")
            return
    run_polling_blocking(console=c, force_daemon_thread=force_daemon_thread)
