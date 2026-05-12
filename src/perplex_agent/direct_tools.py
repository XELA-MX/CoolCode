"""Direct mode: Sonar JSON step loop with local workspace tools (non-streaming)."""

from __future__ import annotations

from typing import Any

from perplex_agent.client import PerplexityAPIError, PerplexityClient
from perplex_agent.config import Settings
from perplex_agent.orchestrator import _parse_agent_json
from perplex_agent.subagents import extract_message_content
from perplex_agent.tool_defs import build_direct_step_response_format
from perplex_agent.tool_runtime import run_tool_calls

DIRECT_AGENT_RESPONSE_FORMAT = build_direct_step_response_format()


async def run_direct_tool_session(
    client: PerplexityClient,
    settings: Settings,
    model: str,
    user_message: str,
    *,
    system_prompt: str,
    extra: dict[str, Any],
) -> str:
    """Run direct mode with JSON tool steps until final_answer or iteration cap."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    for _ in range(settings.max_direct_tool_iterations):
        try:
            data = await client.chat_completion(
                model=model,
                messages=messages,
                stream=False,
                response_format=DIRECT_AGENT_RESPONSE_FORMAT,
                extra=extra,
            )
        except PerplexityAPIError as e:
            if e.status_code != 422:
                raise
            data = await client.chat_completion(
                model=model,
                messages=messages,
                stream=False,
                response_format=None,
                extra=extra,
            )
        raw = extract_message_content(data)
        try:
            step = _parse_agent_json(raw)
        except ValueError:
            return f"(model did not return valid JSON)\n{raw[:2000]}"

        action = step.get("action")
        if action == "final_answer":
            return str(step.get("final_text", ""))

        if action == "call_tools":
            calls = step.get("tool_calls") or []
            if not isinstance(calls, list) or not calls:
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[system] call_tools requires a non-empty tool_calls array. "
                            "Use read_file, list_dir, or glob_files, or reply with final_answer."
                        ),
                    }
                )
                continue
            block = run_tool_calls(settings, calls, channel="shell")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": block})
            continue

        messages.append(
            {
                "role": "user",
                "content": f"[system] Invalid action {action!r}. Use final_answer or call_tools with JSON only.",
            }
        )

    return "Tool / direct loop stopped: max iterations reached without final_answer."
