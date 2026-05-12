"""JSON-schema fragments for planner/direct tool steps (Perplexity strict mode)."""

from __future__ import annotations

from typing import Any

# One tool call item: every field required for strict JSON schema (use null when unused).
TOOL_CALL_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "enum": ["read_file", "list_dir", "glob_files"],
        },
        "path": {"type": ["string", "null"]},
        "pattern": {"type": ["string", "null"]},
        "max_bytes": {"type": ["integer", "null"]},
        "max_entries": {"type": ["integer", "null"]},
    },
    "required": ["name", "path", "pattern", "max_bytes", "max_entries"],
    "additionalProperties": False,
}

TOOLS_CAPABILITY_LINES = """Local tools (project directory only — not Google Workspace): you may use action **call_tools** with a **tool_calls** array.
Each item must set **name** and use **null** for unused fields:
- **read_file**: **path** (relative to workspace or absolute under it), optional **max_bytes** (null = default cap). The host returns a **numbered line preview**, not always the full file—summarize for the user unless they need the entire content.
- **list_dir**: **path** (directory; null or \".\" = workspace root), optional **max_entries** (null = default cap).
- **glob_files**: **pattern** (glob, e.g. \"**/*.py\"); optional **path** as subdirectory under workspace (null = root). Other fields null.
After **call_tools**, the user message will contain **<tool_results>** with stdout or error lines. Then reply with another JSON step.
For **final_answer**, set **tool_calls** to []. For **spawn_subagents** / **wait_subagents**, set **tool_calls** to []."""


DIRECT_TOOL_INSTRUCTIONS = """Each assistant turn must be exactly ONE JSON object (strict schema; no markdown outside JSON).
Actions:
- **final_answer**: **final_text** = user-visible reply; **tool_calls** = [].
- **call_tools**: **tool_calls** = non-empty array; **final_text** may be \"\" or a short status line.
Tool items: **name** is read_file | list_dir | glob_files; use **null** for unused fields (**path**, **pattern**, **max_bytes**, **max_entries**).
When you receive **<tool_results>**, read it and either issue more **call_tools** or **final_answer**."""


def build_agent_step_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "agent_step",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "final_answer",
                            "spawn_subagents",
                            "wait_subagents",
                            "call_tools",
                        ],
                    },
                    "final_text": {"type": "string"},
                    "subagent_tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "instruction": {"type": "string"},
                                "model": {"type": ["string", "null"]},
                            },
                            "required": ["instruction", "model"],
                            "additionalProperties": False,
                        },
                    },
                    "wait_ids": {"type": "array", "items": {"type": "string"}},
                    "tool_calls": {"type": "array", "items": TOOL_CALL_ITEM_SCHEMA},
                },
                "required": [
                    "action",
                    "final_text",
                    "subagent_tasks",
                    "wait_ids",
                    "tool_calls",
                ],
                "additionalProperties": False,
            },
        },
    }


def build_direct_step_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "direct_agent_step",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["final_answer", "call_tools"]},
                    "final_text": {"type": "string"},
                    "tool_calls": {"type": "array", "items": TOOL_CALL_ITEM_SCHEMA},
                },
                "required": ["action", "final_text", "tool_calls"],
                "additionalProperties": False,
            },
        },
    }
