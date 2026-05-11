"""Load settings from environment and optional TOML files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib


def _clamp_tokens(n: int, lo: int = 256, hi: int = 32000) -> int:
    return max(lo, min(hi, int(n)))


def _clamp_temp(t: float) -> float:
    return max(0.0, min(2.0, float(t)))


def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _load_toml_files() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    paths = [
        Path.cwd() / "perplex-agent.toml",
        Path.home() / ".config" / "perplex-agent" / "config.toml",
    ]
    for p in paths:
        if not p.is_file():
            continue
        with p.open("rb") as f:
            data = tomllib.load(f)
        if isinstance(data, dict):
            _deep_merge(merged, data)
    return merged


@dataclass
class Settings:
    perplexity_api_key: str
    perplexity_model: str = "sonar-pro"
    subagent_model: str = "sonar"
    request_timeout_s: float = 120.0
    max_concurrent_subagents: int = 4
    max_subagent_depth: int = 1
    max_orchestrator_iterations: int = 12
    auto_wait_after_spawn: bool = True
    subagent_timeout_s: float = 180.0
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: list[int] = field(default_factory=list)
    announce_subagents: bool = True
    subagent_state_file: Path = field(
        default_factory=lambda: Path.home() / ".cache" / "perplex-agent" / "subagents-state.json"
    )
    planning_model: str = "sonar"
    assume_yes: bool = False
    # Completion budgets (output tokens; lower = cheaper, forces brevity)
    planner_max_tokens: int = 1536
    subagent_max_tokens: int = 3072
    planning_max_tokens: int = 512
    direct_max_tokens: int = 4096
    planner_temperature: float = 0.35
    subagent_temperature: float = 0.25
    planning_temperature: float = 0.2
    direct_temperature: float = 0.35
    history_max_chars: int = 28000
    inject_subagent_max_chars: int = 5200

    def extra_for_planner(self) -> dict[str, Any]:
        return {
            "max_tokens": _clamp_tokens(self.planner_max_tokens),
            "temperature": _clamp_temp(self.planner_temperature),
        }

    def extra_for_subagent(self) -> dict[str, Any]:
        return {
            "max_tokens": _clamp_tokens(self.subagent_max_tokens),
            "temperature": _clamp_temp(self.subagent_temperature),
        }

    def extra_for_planning(self) -> dict[str, Any]:
        return {
            "max_tokens": _clamp_tokens(self.planning_max_tokens),
            "temperature": _clamp_temp(self.planning_temperature),
        }

    def extra_for_direct(self) -> dict[str, Any]:
        return {
            "max_tokens": _clamp_tokens(self.direct_max_tokens),
            "temperature": _clamp_temp(self.direct_temperature),
        }

    @classmethod
    def load(cls) -> Settings:
        file_cfg = _load_toml_files()
        p = file_cfg.get("perplexity", {}) or {}
        s = file_cfg.get("subagents", {}) or {}
        t = file_cfg.get("telegram", {}) or {}
        o = file_cfg.get("orchestrator", {}) or {}
        cli_cfg = file_cfg.get("cli", {}) or {}
        tok = file_cfg.get("tokens", {}) or {}

        api_key = os.environ.get("PERPLEXITY_API_KEY") or p.get("api_key") or ""
        if not isinstance(api_key, str):
            api_key = str(api_key)

        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or t.get("bot_token")
        if tg_token is not None and not isinstance(tg_token, str):
            tg_token = str(tg_token)

        allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS")
        if allowed:
            tg_ids = _parse_int_list(allowed)
        else:
            raw_ids = t.get("allowed_user_ids")
            if isinstance(raw_ids, list):
                tg_ids = [int(x) for x in raw_ids]
            else:
                tg_ids = []

        def env_int(name: str, fallback: int) -> int:
            v = os.environ.get(name)
            if v is None or v == "":
                return fallback
            return int(v)

        def env_float(name: str, fallback: float) -> float:
            v = os.environ.get(name)
            if v is None or v == "":
                return fallback
            return float(v)

        def env_bool(name: str, fallback: bool) -> bool:
            v = os.environ.get(name)
            if v is None or v == "":
                return fallback
            return v.lower() in ("1", "true", "yes", "on")

        state_default = Path.home() / ".cache" / "perplex-agent" / "subagents-state.json"
        state_raw = os.environ.get("PERPLEX_AGENT_STATE_FILE") or s.get("state_file")
        if state_raw:
            state_path = Path(str(state_raw)).expanduser()
        else:
            state_path = state_default

        return cls(
            perplexity_api_key=api_key,
            perplexity_model=os.environ.get("PERPLEXITY_MODEL") or p.get("model") or "sonar-pro",
            subagent_model=os.environ.get("SUBAGENT_MODEL") or s.get("model") or "sonar",
            request_timeout_s=env_float("REQUEST_TIMEOUT_S", float(p.get("timeout_s", 120.0))),
            max_concurrent_subagents=env_int(
                "MAX_CONCURRENT_SUBAGENTS", int(s.get("max_concurrent", 4))
            ),
            max_subagent_depth=env_int("MAX_SUBAGENT_DEPTH", int(s.get("max_depth", 1))),
            max_orchestrator_iterations=env_int(
                "ORCHESTRATOR_MAX_ITERATIONS", int(o.get("max_iterations", 12))
            ),
            auto_wait_after_spawn=bool(s.get("auto_wait_after_spawn", True))
            if "SUBAGENT_AUTO_WAIT" not in os.environ
            else env_bool("SUBAGENT_AUTO_WAIT", True),
            subagent_timeout_s=env_float("SUBAGENT_TIMEOUT_S", float(s.get("timeout_s", 180.0))),
            telegram_bot_token=tg_token,
            telegram_allowed_user_ids=tg_ids,
            announce_subagents=bool(t.get("announce_subagents", True)),
            subagent_state_file=state_path,
            planning_model=os.environ.get("PLANNING_MODEL")
            or str(cli_cfg.get("planning_model", "sonar")),
            assume_yes=env_bool("PERPLEX_AGENT_ASSUME_YES", bool(cli_cfg.get("assume_yes", False))),
            planner_max_tokens=env_int(
                "PLANNER_MAX_TOKENS", int(tok.get("planner_max_tokens", 1536))
            ),
            subagent_max_tokens=env_int(
                "SUBAGENT_MAX_TOKENS", int(tok.get("subagent_max_tokens", 3072))
            ),
            planning_max_tokens=env_int(
                "PLANNING_MAX_TOKENS", int(tok.get("planning_max_tokens", 512))
            ),
            direct_max_tokens=env_int(
                "DIRECT_MAX_TOKENS", int(tok.get("direct_max_tokens", 4096))
            ),
            planner_temperature=env_float(
                "PLANNER_TEMPERATURE", float(tok.get("planner_temperature", 0.35))
            ),
            subagent_temperature=env_float(
                "SUBAGENT_TEMPERATURE", float(tok.get("subagent_temperature", 0.25))
            ),
            planning_temperature=env_float(
                "PLANNING_TEMPERATURE", float(tok.get("planning_temperature", 0.2))
            ),
            direct_temperature=env_float(
                "DIRECT_TEMPERATURE", float(tok.get("direct_temperature", 0.35))
            ),
            history_max_chars=env_int(
                "PLANNER_HISTORY_MAX_CHARS", int(tok.get("history_max_chars", 28000))
            ),
            inject_subagent_max_chars=env_int(
                "INJECT_SUBAGENT_MAX_CHARS", int(tok.get("inject_subagent_max_chars", 5200))
            ),
        )


# Sonar chat models for POST /v1/sonar (docs.perplexity.ai). Extend when the API adds IDs.
SONAR_CHAT_MODELS: tuple[tuple[str, str], ...] = (
    ("sonar", "Rápido, grounding web"),
    ("sonar-pro", "Pro search, consultas complejas"),
    ("sonar-deep-research", "Investigación profunda, muchas fuentes"),
    ("sonar-reasoning-pro", "Razonamiento multi‑paso con web"),
)
SONAR_CHAT_MODEL_IDS: frozenset[str] = frozenset(mid for mid, _ in SONAR_CHAT_MODELS)


def resolve_sonar_chat_model(name: str) -> str | None:
    """Return canonical model id if it is in the allowed Sonar list, else None."""
    key = (name or "").strip().lower()
    return key if key in SONAR_CHAT_MODEL_IDS else None
