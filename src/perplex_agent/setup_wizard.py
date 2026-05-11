"""Interactive setup when API keys or Telegram config are missing (OpenClaw-style)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import tomllib
import tomli_w
from prompt_toolkit.shortcuts import prompt as ptk_prompt
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from perplex_agent.client import PerplexityAPIError, PerplexityClient
from perplex_agent.config import Settings


def _is_interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _markup_plain(message: str) -> str:
    """Plain text for TTY prompts (Rich markup confuses nothing on /dev/tty)."""
    try:
        return Text.from_markup(message).plain
    except Exception:
        return message


def _dev_tty_lines():
    """Real controlling TTY; avoids broken stdin after prompt_toolkit raw mode."""
    try:
        return open("/dev/tty", "r+", encoding="utf-8", errors="replace")
    except OSError:
        return None


def _wizard_confirm(console: Console, message: str, *, default: bool) -> bool:
    tty = _dev_tty_lines()
    if tty is None:
        return Confirm.ask(message, default=default, console=console)
    plain = _markup_plain(message)
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        tty.write(plain + suffix)
        tty.flush()
        line = (tty.readline() or "").strip().lower()
    finally:
        tty.close()
    if not line:
        return default
    if line[0] in ("y", "s", "1", "t"):  # s/sí
        return True
    if line[0] in ("n", "0", "f"):
        return False
    return default


def _read_secret_ptk(message_plain: str) -> str:
    """Password prompt in a nested PT run (own thread + loop); paste works; no getpass/Rich fight."""
    hint = f"{message_plain.rstrip()}: "
    try:
        return ptk_prompt(hint, is_password=True, in_thread=True).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


async def _wizard_secret(message: str) -> str:
    """Secrets off the main asyncio loop so paste and stdin stay sane after chat's prompt_async."""
    plain = _markup_plain(message)
    return await asyncio.to_thread(_read_secret_ptk, plain)


def _wizard_prompt_line(
    console: Console,
    message: str,
    *,
    default: str = "",
    show_default: bool = True,
) -> str:
    tty = _dev_tty_lines()
    if tty is None:
        return Prompt.ask(
            message,
            default=default,
            show_default=show_default,
            console=console,
        ).strip()
    plain = _markup_plain(message)
    try:
        if default != "" and show_default:
            tty.write(f"{plain} [{default}]: ")
        else:
            tty.write(f"{plain}: ")
        tty.flush()
        line = (tty.readline() or "").rstrip("\n")
        if not line.strip() and default != "":
            return default.strip()
        return line.strip()
    finally:
        tty.close()


def default_config_path() -> Path:
    return Path.home() / ".config" / "perplex-agent" / "config.toml"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for k, v in patch.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def read_user_config_file() -> dict[str, Any]:
    path = default_config_path()
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return raw if isinstance(raw, dict) else {}


def write_user_config_file(data: dict[str, Any]) -> Path:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        tomli_w.dump(data, f)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def patch_user_config(patch: dict[str, Any]) -> Path:
    current = read_user_config_file()
    merged = _deep_merge(current, patch)
    return write_user_config_file(merged)


async def _probe_perplexity_key(api_key: str) -> bool:
    try:
        client = PerplexityClient(api_key.strip(), timeout_s=20.0)
        await client.chat_completion(
            model="sonar",
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            stream=False,
            extra={"max_tokens": 8, "temperature": 0},
        )
        return True
    except (PerplexityAPIError, OSError, TimeoutError, asyncio.TimeoutError):
        return False
    except Exception:
        return False


async def run_setup_wizard(
    *,
    console: Console,
    perplexity: bool = True,
    telegram: bool = True,
    probe_key: bool = True,
) -> Path | None:
    """Prompt for missing settings and merge into ~/.config/perplex-agent/config.toml."""
    console.print(
        Panel(
            "[bold]Asistente de configuración[/bold]\n\n"
            "Se guardará en [cyan]~/.config/perplex-agent/config.toml[/cyan] "
            "(permisos 600). Puedes seguir usando variables de entorno; "
            "tienen prioridad sobre el archivo.",
            title="perplex-agent",
            border_style="cyan",
        )
    )
    patch: dict[str, Any] = {}
    saved = False

    if perplexity:
        if _wizard_confirm(
            console,
            "¿Configurar Perplexity (API key obligatoria para chat)?",
            default=True,
        ):
            key = await _wizard_secret("Pega tu [bold]PERPLEXITY_API_KEY[/bold]")
            if key:
                if probe_key:
                    with console.status("[cyan]Comprobando clave…[/cyan]", spinner="dots"):
                        ok = await _probe_perplexity_key(key)
                    if not ok:
                        if not _wizard_confirm(
                            console,
                            "[yellow]La comprobación falló[/yellow] (red, clave o corte). "
                            "¿Guardar igualmente?",
                            default=False,
                        ):
                            console.print("[dim]No se guardó la clave.[/dim]")
                        else:
                            patch.setdefault("perplexity", {})["api_key"] = key
                            saved = True
                    else:
                        console.print("[green]Clave aceptada por la API.[/green]")
                        patch.setdefault("perplexity", {})["api_key"] = key
                        saved = True
                else:
                    patch.setdefault("perplexity", {})["api_key"] = key
                    saved = True
            if _wizard_confirm(
                console,
                "¿Fijar modelo principal por defecto? (Enter = sonar-pro)",
                default=False,
            ):
                model = _wizard_prompt_line(console, "Modelo", default="sonar-pro").strip()
                if model:
                    patch.setdefault("perplexity", {})["model"] = model
                    saved = True

    if telegram:
        if _wizard_confirm(
            console,
            "¿Configurar Telegram (opcional, solo si usarás el bot)?",
            default=not perplexity,
        ):
            token = await _wizard_secret("Token del bot ([bold]TELEGRAM_BOT_TOKEN[/bold])")
            if token:
                patch.setdefault("telegram", {})["bot_token"] = token
                saved = True
            raw_ids = _wizard_prompt_line(
                console,
                "[dim]IDs de usuario permitidos (coma), vacío = cualquiera[/dim]",
                default="",
                show_default=False,
            ).strip()
            if raw_ids:
                ids: list[int] = []
                for part in raw_ids.split(","):
                    part = part.strip()
                    if part.isdigit():
                        ids.append(int(part))
                if ids:
                    patch.setdefault("telegram", {})["allowed_user_ids"] = ids
                    saved = True

    if not saved:
        console.print("[dim]Nada que guardar. Puedes exportar PERPLEXITY_API_KEY en tu shell.[/dim]")
        return None

    path = patch_user_config(patch)
    console.print(f"[green]Guardado:[/green] [bold]{path}[/bold]")
    console.print(
        "[dim]Recarga la configuración ejecutando de nuevo el comando "
        "(o abre una nueva terminal si exportabas env).[/dim]"
    )
    return path


async def ensure_perplexity_configured(console: Console) -> bool:
    """If missing API key and TTY, offer wizard. Returns True if key is available after."""
    s = Settings.load()
    if s.perplexity_api_key:
        return True
    if not _is_interactive():
        return False
    console.print("[yellow]Falta PERPLEXITY_API_KEY[/yellow] (env o [perplexity] api_key en TOML).")
    if not _wizard_confirm(console, "¿Abrir el asistente para configurarla ahora?", default=True):
        return False
    await run_setup_wizard(console=console, perplexity=True, telegram=False, probe_key=True)
    return bool(Settings.load().perplexity_api_key)


async def ensure_telegram_ready(console: Console) -> bool:
    """Ensure Perplexity + Telegram token; offer wizard for gaps."""
    if not await ensure_perplexity_configured(console):
        return False
    s = Settings.load()
    if s.telegram_bot_token:
        return True
    if not _is_interactive():
        return False
    console.print("[yellow]Falta TELEGRAM_BOT_TOKEN[/yellow].")
    if not _wizard_confirm(console, "¿Configurar Telegram ahora?", default=True):
        return False
    await run_setup_wizard(console=console, perplexity=False, telegram=True, probe_key=False)
    return bool(Settings.load().telegram_bot_token)
