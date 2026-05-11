"""Telegram long-polling bot wired to the orchestrator."""

from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from typing import Any

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

    orch = Orchestrator(settings, on_subagent_complete=on_sub)
    try:
        result = await orch.run(text, stream=False)
    except Exception as e:  # noqa: BLE001
        log.exception("orchestrator failed")
        await message.reply_text(f"Error: {e}")
        return

    for part in split_text(result.final_text):
        await message.reply_text(part)


def run_polling_blocking() -> None:
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
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def run_telegram_with_gate(
    *,
    assume_yes: bool,
    console: Any | None = None,
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
    run_polling_blocking()
