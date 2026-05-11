"""Lightweight execution plan via Sonar (JSON) before costly orchestrator runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from perplex_agent.client import PerplexityAPIError, PerplexityClient
from perplex_agent.config import Settings
from perplex_agent.subagents import extract_message_content

PLAN_SYSTEM = """Emit a minimal execution plan JSON for the next CLI run. User message = goal.

If the message is only a greeting, thanks, or trivial chat, set may_spawn_subagents false, estimated_api_calls "1", steps that say the orchestrator will answer briefly in-chat (no web article).

Be terse: title ≤8 words, summary ≤2 sentences, ≤4 steps each ≤18 words, no fluff, no repetition.
Orchestrator mode may multi-call + parallel subagents. Match schema exactly."""

PLAN_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "execution_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 80},
                "summary": {"type": "string", "maxLength": 240},
                "steps": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "maxLength": 160},
                },
                "may_spawn_subagents": {"type": "boolean"},
                "estimated_api_calls": {"type": "string"},
            },
            "required": [
                "title",
                "summary",
                "steps",
                "may_spawn_subagents",
                "estimated_api_calls",
            ],
            "additionalProperties": False,
        },
    },
}


@dataclass
class ExecutionPlan:
    title: str
    summary: str
    steps: list[str]
    may_spawn_subagents: bool
    estimated_api_calls: str
    mode: str  # "orchestrator" | "direct"


def _parse_plan_json(raw: str) -> dict[str, Any]:
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
    raise ValueError(f"Invalid plan JSON: {raw[:400]}")


def fallback_plan(user_message: str, *, direct: bool, model: str) -> ExecutionPlan:
    if direct:
        return ExecutionPlan(
            title="Consulta directa",
            summary="Una sola llamada al modelo Sonar con tu mensaje.",
            steps=["Enviar mensaje al API de Perplexity", "Mostrar respuesta"],
            may_spawn_subagents=False,
            estimated_api_calls="1",
            mode="direct",
        )
    return ExecutionPlan(
        title="Orquestación con posibles subagentes",
        summary=(
            "El orquestador puede planificar en varias rondas y lanzar subagentes "
            "en paralelo para búsquedas web independientes."
        ),
        steps=[
            f"Modelo orquestador: {model}",
            "Analizar la petición con el modelo principal",
            "Opcional: lanzar subagentes paralelos (Sonar)",
            "Sintetizar respuesta final",
        ],
        may_spawn_subagents=True,
        estimated_api_calls=f"2–{max(3, 12)} (según complejidad)",
        mode="orchestrator",
    )


async def propose_execution_plan(
    settings: Settings,
    user_message: str,
    *,
    direct: bool,
    model_for_run: str,
) -> ExecutionPlan:
    if direct:
        return fallback_plan(user_message, direct=True, model=model_for_run)

    client = PerplexityClient(settings.perplexity_api_key, timeout_s=min(60.0, settings.request_timeout_s))
    user_block = (
        f"User message:\n{user_message}\n\n"
        f"Planned main model after approval: {model_for_run}\n"
        f"Default subagent model: {settings.subagent_model}\n"
        f"Max orchestrator iterations: {settings.max_orchestrator_iterations}\n"
    )
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": user_block},
    ]
    pextra = settings.extra_for_planning()
    try:
        data = await client.chat_completion(
            model=settings.planning_model,
            messages=messages,
            stream=False,
            response_format=PLAN_RESPONSE_FORMAT,
            extra=pextra,
        )
    except PerplexityAPIError as e:
        if e.status_code != 422:
            return fallback_plan(user_message, direct=False, model=model_for_run)
        data = await client.chat_completion(
            model=settings.planning_model,
            messages=messages,
            stream=False,
            response_format=None,
            extra=pextra,
        )

    raw = extract_message_content(data)
    try:
        obj = _parse_plan_json(raw)
    except ValueError:
        return fallback_plan(user_message, direct=False, model=model_for_run)

    steps = obj.get("steps") or []
    if not isinstance(steps, list):
        steps = []
    steps = [str(s).strip()[:160] for s in steps if str(s).strip()][:4]

    return ExecutionPlan(
        title=(str(obj.get("title", "Plan")).strip() or "Plan")[:80],
        summary=(str(obj.get("summary", "")).strip() or "—")[:240],
        steps=steps or ["(sin pasos)"],
        may_spawn_subagents=bool(obj.get("may_spawn_subagents")),
        estimated_api_calls=str(obj.get("estimated_api_calls", "?")).strip() or "?",
        mode="orchestrator",
    )
