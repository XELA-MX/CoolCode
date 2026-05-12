"""Turn-based orchestrator: JSON agent steps + Perplexity Sonar + subagents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from perplex_agent.client import PerplexityAPIError, PerplexityClient
from perplex_agent.config import Settings
from perplex_agent.history_compact import trim_planner_history
from perplex_agent.subagents import SubagentManager, SubagentRecord, extract_message_content
from perplex_agent.tool_defs import (
    DIRECT_TOOL_INSTRUCTIONS,
    TOOLS_CAPABILITY_LINES,
    build_agent_step_response_format,
)
from perplex_agent.tool_runtime import run_tool_calls

AGENT_STEP_RESPONSE_FORMAT: dict[str, Any] = build_agent_step_response_format()

ORCHESTRATOR_SYSTEM = """Sonar orchestrator. Each turn: output exactly ONE JSON object (schema). No prose outside JSON.

Actions: final_answer | spawn_subagents | wait_subagents | call_tools.

Environment (workspace):
- You operate under a **local workspace** (absolute path below). Paths the user gives are usually relative to that root unless they are absolute paths already inside the tree.
- **Word sense:** here **workspace** = **project root / working directory on disk** only. It is **not** Google Workspace, Microsoft 365, Notion, or any cloud product. **Never** mention Google Workspace (or similar) unless the user explicitly asks about that product by name.
- If the user asks which workspace you are in, answer with that local path only (briefly). Do not deny or "clarify" using unrelated product names they did not ask about.
- **call_tools** runs **read_file**, **list_dir**, and **glob_files** only inside that workspace (the host enforces it). You cannot run shell commands or write files.

If the user asks about **function calling**, **tools**, or **how this agent works**:
- This loop is **JSON-native**: each turn is one schema object. **call_tools** executes local read-only file tools; **spawn_subagents** / **wait_subagents** run parallel **Sonar** completions (remote), not local execution.
- Do not claim features that are not implemented (shell, write_file, network beyond Sonar).

Conversational vs research (critical):
- Pure social / meta: greetings, thanks, goodbye, ok, short chit-chat, or empty-ish prompts (e.g. "hola", "hi", "gracias", "buenas", "qué tal" alone). Use action final_answer only. final_text = brief natural reply in the user's language (1–3 short sentences). Do NOT treat as a web lookup, do NOT write an article, do NOT assume homonyms are the topic (Spanish "hola" = hello, not the magazine "¡Hola!"; "workspace" = local project folder, not Google Workspace). No citation markers [1] unless the user explicitly asked for sources or verifiable facts.
- Do not spawn_subagents for chit-chat or when the user did not ask for external information.
- When the user clearly wants facts, news, comparisons, how-tos, definitions, or multi-step research, you may use spawn_subagents as today.

Brevity (mandatory):
- final_text: answer only what was asked. No filler, no preambles, no "As an AI…", no restating the question, no duplicate paragraphs or repeated citations. One clear conclusion max. Prefer tight bullets or short paragraphs. Cite each URL at most once unless essential.
- Keep final_text proportional to the question; avoid encyclopedic length unless the user explicitly asks for exhaustive detail.
- spawn_subagents: only for clearly independent lookups; instructions minimal, self-contained, no pronouns without antecedent.
- wait_subagents: only for ids already shown after a spawn.

Do not invent subagent ids."""

SUBAGENT_SYSTEM = """Web-grounded subagent. Answer the instruction only.

If the instruction is clearly conversational (greeting/thanks only), reply in one or two sentences in that language without launching a topic article or citations.

If the instruction says "workspace" without naming a product, assume **local project directory**, not Google Workspace or other SaaS.

Style: direct, dense facts. No introduction, no recap of the instruction, no closing moral. No repeating the same point. At most one short paragraph of synthesis after bullets/facts. Cite sources inline once each. If uncertain, one short caveat only."""

DIRECT_ASSISTANT_SYSTEM = """You are perplex-agent: a helpful terminal assistant backed by Perplexity Sonar (optional web grounding).

Environment (workspace):
- You are tied to a **local workspace** (path below): the **project root / working directory on disk** for this CLI session.
- **Word sense:** **workspace** here is **only** that folder. It is **not** Google Workspace or any other product with a similar name. **Never** bring up Google Workspace unless the user explicitly asks about Google's product.
- If they ask which workspace you use, state the path below in one short sentence—no unrelated product disclaimers.
- When **direct tool mode** is enabled (see appended instructions), non-streaming runs use a JSON tool loop (**read_file**, **list_dir**, **glob_files**). With tools off, you answer in one shot; the user can paste files if needed.

If asked about **function calling** or **tools**:
- **Orchestrator** mode: JSON steps including **call_tools** (local read-only) plus **spawn_subagents** / **wait_subagents** (remote Sonar).
- **Direct** mode: either a single completion (tools off) or the same local tools via JSON steps (tools on, non-stream).

Behavior:
- Greetings, thanks, short social messages, or vague one-word pleasantries: answer naturally in the same language the user used. Keep it brief (1–3 sentences). Do not assume they want a dossier, biography, or article about a product/magazine/brand that merely sounds like what they typed (e.g. Spanish "hola" is hello, not the publication "¡Hola!"; "workspace" questions = local project path only, not Google Workspace).
- When they clearly ask for information, data, news, comparisons, code help, or step-by-step tasks, use Sonar grounding when appropriate and stay factual and concise.
- No fake citations or [1][2] blocks for pure chit-chat. Use citations only when giving web-backed factual answers that warrant them.

Tone: warm, clear, professional."""


def planner_system_for_settings(settings: Settings) -> str:
    """Orchestrator system prompt including workspace root for future tool use."""
    return (
        f"{ORCHESTRATOR_SYSTEM}\n\n"
        f"Workspace root on disk (authoritative): {settings.workspace_dir}\n"
        "Relative paths in user messages are under this directory unless an absolute path is given.\n\n"
        f"{TOOLS_CAPABILITY_LINES}"
    )


def direct_system_for_settings(settings: Settings) -> str:
    """Direct-mode system prompt with workspace context."""
    body = (
        f"{DIRECT_ASSISTANT_SYSTEM}\n\n"
        f"Workspace root on disk (authoritative): {settings.workspace_dir}"
    )
    if settings.direct_tools_enabled:
        body += "\n\n" + DIRECT_TOOL_INSTRUCTIONS
    return body


def _parse_agent_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}\s*$", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Invalid agent JSON: {raw[:400]}")


@dataclass
class OrchestratorResult:
    final_text: str
    history: list[dict[str, str]] = field(default_factory=list)
    last_subagent_ids: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        on_subagent_complete: Callable[[SubagentRecord], Coroutine[Any, Any, None]] | None = None,
        tool_result_channel: str = "shell",
    ) -> None:
        self._settings = settings
        self._tool_result_channel = tool_result_channel if tool_result_channel in (
            "shell",
            "telegram",
        ) else "shell"
        self._client = PerplexityClient(
            settings.perplexity_api_key,
            timeout_s=settings.request_timeout_s,
        )
        self._on_subagent_complete = on_subagent_complete
        self._manager = SubagentManager(
            self._client,
            default_model=settings.subagent_model,
            max_concurrent=settings.max_concurrent_subagents,
            subagent_timeout_s=settings.subagent_timeout_s,
            system_prompt=SUBAGENT_SYSTEM,
            on_complete=on_subagent_complete,
            state_file=settings.subagent_state_file,
            completion_extra=settings.extra_for_subagent(),
        )

    async def _planner_completion(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        extra = self._settings.extra_for_planner()
        try:
            return await self._client.chat_completion(
                model=self._settings.perplexity_model,
                messages=messages,
                stream=False,
                response_format=AGENT_STEP_RESPONSE_FORMAT,
                extra=extra,
            )
        except PerplexityAPIError as e:
            if e.status_code != 422:
                raise
            return await self._client.chat_completion(
                model=self._settings.perplexity_model,
                messages=messages,
                stream=False,
                response_format=None,
                extra=extra,
            )

    @property
    def subagent_manager(self) -> SubagentManager:
        return self._manager

    async def run(
        self,
        user_message: str,
        *,
        stream: bool = False,
        stream_writer: Callable[[str], None] | None = None,
        before_spawn_batch: Callable[[list[tuple[str, str | None]]], Coroutine[Any, Any, bool]]
        | None = None,
    ) -> OrchestratorResult:
        history: list[dict[str, str]] = [{"role": "user", "content": user_message}]
        last_ids: list[str] = []

        for _ in range(self._settings.max_orchestrator_iterations):
            trimmed = trim_planner_history(
                history,
                max_total_chars=self._settings.history_max_chars,
                inject_max_chars=self._settings.inject_subagent_max_chars,
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": planner_system_for_settings(self._settings)},
                *trimmed,
            ]
            planner_extra = self._settings.extra_for_planner()
            if stream and stream_writer is not None:
                buf: list[str] = []
                try:
                    async for chunk in self._client.chat_completion_stream_text(
                        model=self._settings.perplexity_model,
                        messages=messages,
                        response_format=AGENT_STEP_RESPONSE_FORMAT,
                        extra=planner_extra,
                    ):
                        buf.append(chunk)
                        stream_writer(chunk)
                except PerplexityAPIError:
                    data = await self._planner_completion(messages)
                    raw_fb = extract_message_content(data)
                    stream_writer(raw_fb)
                else:
                    data = {
                        "choices": [{"message": {"role": "assistant", "content": "".join(buf)}}]
                    }
            else:
                data = await self._planner_completion(messages)

            raw = extract_message_content(data)
            step = _parse_agent_json(raw)
            action = step.get("action")

            if action == "final_answer":
                text = str(step.get("final_text", ""))
                history.append({"role": "assistant", "content": text})
                return OrchestratorResult(final_text=text, history=history, last_subagent_ids=last_ids)

            if action == "spawn_subagents":
                tasks = step.get("subagent_tasks") or []
                to_spawn: list[tuple[str, str | None]] = []
                for t in tasks:
                    if not isinstance(t, dict):
                        continue
                    instr = str(t.get("instruction", "")).strip()
                    if not instr:
                        continue
                    m = t.get("model")
                    model = str(m) if isinstance(m, str) and m.strip() else None
                    to_spawn.append((instr, model))

                if to_spawn and before_spawn_batch is not None:
                    approved = await before_spawn_batch(to_spawn)
                    if not approved:
                        history.append(
                            {
                                "role": "user",
                                "content": (
                                    "[system] The user declined launching subagents for this batch. "
                                    "Reply with final_answer using only what you already know from the "
                                    "conversation (no new subagents)."
                                ),
                            }
                        )
                        continue

                new_ids: list[str] = []
                for instr, model in to_spawn:
                    sid = await self._manager.spawn(instr, model=model, depth=0)
                    new_ids.append(sid)
                last_ids = new_ids
                if new_ids:
                    history.append(
                        {
                            "role": "assistant",
                            "content": f"[orchestrator] Spawned subagents: {', '.join(new_ids)}",
                        }
                    )
                else:
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "[system] spawn_subagents had no valid tasks. "
                                "Use final_answer or provide non-empty instructions."
                            ),
                        }
                    )
                if self._settings.auto_wait_after_spawn and new_ids:
                    await self._manager.wait_ids(new_ids)
                    block = _format_subagent_block(self._manager, new_ids)
                    history.append({"role": "user", "content": block})
                continue

            if action == "wait_subagents":
                ids = [str(x) for x in (step.get("wait_ids") or []) if str(x).strip()]
                if ids:
                    await self._manager.wait_ids(ids)
                    block = _format_subagent_block(self._manager, ids)
                    history.append({"role": "user", "content": block})
                continue

            if action == "call_tools":
                history.append({"role": "assistant", "content": raw})
                calls = step.get("tool_calls") or []
                if not isinstance(calls, list) or not calls:
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "[system] call_tools requires a non-empty tool_calls array "
                                "with read_file, list_dir, or glob_files items (use null for unused fields), "
                                "or use final_answer with tool_calls []."
                            ),
                        }
                    )
                    continue
                block = run_tool_calls(
                    self._settings, calls, channel=self._tool_result_channel
                )
                history.append({"role": "user", "content": block})
                continue

            history.append(
                {
                    "role": "user",
                    "content": f"[system] Invalid action from planner: {action!r}. Reply with valid JSON.",
                }
            )

        # Fallback if loop exhausted
        fallback = (
            "The planner did not produce a final answer in time. "
            "Try a simpler question or raise max orchestrator iterations."
        )
        history.append({"role": "assistant", "content": fallback})
        return OrchestratorResult(
            final_text=fallback, history=history, last_subagent_ids=last_ids
        )


def _format_subagent_block(manager: SubagentManager, ids: list[str]) -> str:
    lines: list[str] = ["<subagent_results>"]
    for sid in ids:
        rec = manager.get(sid)
        if not rec:
            lines.append(f"## {sid}\n(missing record)")
            continue
        lines.append(f"## {sid} ({rec.status.value})")
        if rec.result:
            lines.append(rec.result)
        elif rec.error:
            lines.append(f"Error: {rec.error}")
        else:
            lines.append("(no output)")
    lines.append("</subagent_results>")
    return "\n".join(lines)
