"""prompt_toolkit completer for slash commands (prefix match while typing)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter, merge_completers
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

# (command, help description) — single source for /help table and completion targets
SLASH_HELP: tuple[tuple[str, str], ...] = (
    ("/help", "Esta ayuda"),
    ("/quit", "Salir"),
    ("/exit", "Salir"),
    ("/setup", "Configuración (Perplexity + Telegram)"),
    ("/setup telegram", "Solo Telegram"),
    ("/clear", "Nueva sesión de orquestador"),
    ("/mode", "Modo actual"),
    ("/mode orchestrator", "Planificador + subagentes"),
    ("/mode direct", "Una llamada Sonar por mensaje"),
    ("/workspace", "Muestra la raíz del workspace"),
    ("/model", "Modelo Sonar activo o lista"),
    ("/model list", "Modelos Sonar permitidos"),
    ("/model reset", "Quitar override de sesión (CLI/TOML)"),
    ("/model sonar", "Modelo sonar"),
    ("/model sonar-pro", "Modelo sonar-pro"),
    ("/model sonar-deep-research", "Modelo sonar-deep-research"),
    ("/model sonar-reasoning-pro", "Modelo sonar-reasoning-pro"),
    ("/subagents", "Historial de subagentes"),
    ("/telegram", "Cómo arrancar el bot"),
    ("/telegram run", "Arrancar bot aquí (bloquea la terminal)"),
    ("/version", "Versión"),
    ("/set-workspace", "Cambiar workspace (Tab completa rutas a directorios)"),
)

SLASH_COMMANDS: tuple[str, ...] = tuple(sorted({row[0] for row in SLASH_HELP}))

# `/set-workspace ` + path fragment (command segment case-insensitive).
_SET_WORKSPACE_RE = re.compile(r"(?i)^\s*(/set-workspace)(\s+)(.*)$")


class SetWorkspacePathCompleter(Completer):
    """Directory path completions after ``/set-workspace `` (does not fire on the command token)."""

    def __init__(self) -> None:
        self._paths = PathCompleter(only_directories=True, expanduser=True, min_input_len=0)

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        m = _SET_WORKSPACE_RE.match(text)
        if not m:
            return
        path_fragment = m.group(3)
        if not path_fragment.strip():
            return
        sub = Document(path_fragment, cursor_position=len(path_fragment))
        yield from self._paths.get_completions(sub, complete_event)


def shell_prompt_completer() -> Completer:
    """Slash commands + path completion for ``/set-workspace``."""
    return merge_completers(
        [
            SlashCommandCompleter(),
            SetWorkspacePathCompleter(),
        ]
    )


class SlashCommandCompleter(Completer):
    """Offers commands whose prefix matches the line when it starts with `/`."""

    def __init__(self, commands: Sequence[str] | None = None) -> None:
        self._commands = sorted(set(commands or SLASH_COMMANDS))

    def get_completions(self, document, complete_event):  # noqa: ANN001
        text = document.text_before_cursor
        raw = text.lstrip()
        if not raw.startswith("/"):
            return
        for cmd in self._commands:
            if not cmd.startswith(raw):
                continue
            if cmd == raw:
                continue
            # Replace the slash segment (suffix of length len(raw)) with full command
            yield Completion(cmd, start_position=-len(raw))


def slash_prompt_style() -> Style:
    return Style.from_dict(
        {
            "slash.prompt": "bold ansicyan",
            "completion-menu.completion": "bg:#333333 #ffffff",
            "completion-menu.completion.current": "bg:#66d9ef #000000",
        }
    )


def slash_prompt_message() -> HTML:
    return HTML("<slash.prompt>›</slash.prompt> ")

