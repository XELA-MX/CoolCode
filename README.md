# perplex-agent

CLI orchestrator for the [Perplexity Sonar API](https://docs.perplexity.ai/api-reference/sonar-post) (`POST https://api.perplexity.ai/v1/sonar`), with **parallel subagents** (isolated Sonar calls), an interactive **slash-command shell** (Rich + prompt_toolkit), and an optional **Telegram** bot that reuses the same core.

**Python 3.11+**

---

## Table of contents

- [Features](#features)
- [Quick start](#quick-start)
- [Install](#install)
- [Running the app](#running-the-app)
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [Interactive chat & slash commands](#interactive-chat--slash-commands)
- [Sonar models](#sonar-models)
- [Telegram bot](#telegram-bot)
- [How it works](#how-it-works)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## Features

- **Orchestrator mode**: planner returns structured JSON (`final_answer`, `spawn_subagents`, `wait_subagents`); optional parallel web-grounded subagents.
- **Direct mode**: one Sonar completion per message (optional streaming).
- **Interactive shell**: autocomplete on `/` commands, optional banner animation, `/setup` wizard (TTY-safe prompts).
- **Plans & safety**: execution plan before one-shot `chat "…"` (skippable with `-y` or `PERPLEX_AGENT_ASSUME_YES`).
- **Persistence**: subagent run history on disk (inspect with `subagents list`).

---

## Quick start

```bash
git clone https://github.com/<you>/CoolCode.git
cd CoolCode

# Recommended: uv installs deps + creates .venv
uv sync

# API key (get one from Perplexity)
export PERPLEXITY_API_KEY="pplx-..."

# Interactive chat (default when no subcommand)
uv run perplex-agent
```

Inside the shell, use `/help`, `/setup` if you prefer saving the key to `~/.config/perplex-agent/config.toml`, or `/quit` to exit.

---

## Install

### With uv (recommended)

```bash
uv sync                    # runtime deps
uv sync --extra dev        # + pytest / pytest-asyncio
uv run perplex-agent --help
```

### With pip (editable)

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
pip install -e ".[dev]"     # optional
perplex-agent --help
```

The console script **`perplex-agent`** is declared in `pyproject.toml` and points to `perplex_agent.cli:app`.

---

## Running the app

| Goal | Command |
|------|---------|
| **Interactive chat** (slash shell) | `uv run perplex-agent` or `uv run perplex-agent chat` |
| **One-shot question** (plan + confirm, then orchestrator) | `uv run perplex-agent chat "Your question"` |
| **One-shot, local plan only** (no API plan step) | `uv run perplex-agent chat "…" --no-plan` |
| **One-shot, skip confirmations** | `uv run perplex-agent chat "…" -y` |
| **One-shot direct Sonar** (single API call) | `uv run perplex-agent chat "…" --direct` |
| **Setup wizard** (writes TOML, mode `600`) | `uv run perplex-agent setup` |
| **List persisted subagents** | `uv run perplex-agent subagents list` |
| **Telegram long-polling** | `uv run perplex-agent telegram run` |

**Shell session flags** (only with bare `perplex-agent` / `perplex-agent chat` with no message):

| Flag | Effect |
|------|--------|
| `--direct` | Start in direct mode |
| `--stream` | Stream tokens (direct mode only) |
| `-m` / `--model` | Override default Sonar model for the session |
| `-y` / `--yes` | Skip confirmations (same idea as `PERPLEX_AGENT_ASSUME_YES`) |
| `--confirm-spawns` | Ask before each subagent batch (orchestrator) |
| `--confirm-each` | Confirm before each orchestrator message |
| `--plan-each` | Fetch API plan before each orchestrator message |

Example:

```bash
uv run perplex-agent chat --direct --stream -m sonar-pro
```

---

## Configuration

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PERPLEXITY_API_KEY` | **Yes** for any LLM call | From [Perplexity](https://www.perplexity.ai/settings/api) |
| `PERPLEXITY_MODEL` | No | Default planner/main model (default `sonar-pro`) |
| `SUBAGENT_MODEL` | No | Model for each subagent (default `sonar`) |
| `PLANNING_MODEL` | No | Model for pre-flight JSON plan (default `sonar`) |
| `TELEGRAM_BOT_TOKEN` | For `telegram run` | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_ALLOWED_USER_IDS` | **Strongly recommended** | Comma-separated numeric user IDs; if unset, **anyone** can use the bot |
| `PERPLEX_AGENT_ASSUME_YES` | No | `1` / `true` / `yes` — skip confirmations (like `-y`) |
| `PERPLEX_AGENT_STATE_FILE` | No | Override path for subagent JSON state |
| `PERPLEX_AGENT_NO_BANNER_ANIM` | No | Set to disable the intro ASCII banner animation |

**Concurrency & timeouts**

| Variable | Default (typical) | Description |
|----------|-------------------|-------------|
| `MAX_CONCURRENT_SUBAGENTS` | `4` | Parallel subagents |
| `MAX_SUBAGENT_DEPTH` | `1` | Subagent nesting depth |
| `SUBAGENT_TIMEOUT_S` | `180` | Per-subagent timeout (seconds) |
| `REQUEST_TIMEOUT_S` | `120` | HTTP client timeout |
| `ORCHESTRATOR_MAX_ITERATIONS` | `12` | Max planner turns per user message |
| `SUBAGENT_AUTO_WAIT` | `true` | Auto-wait after spawn (also in TOML) |

**Token & sampling budgets** (map to Perplexity `max_tokens` / `temperature`; lower temperature → tighter answers)

| Variable | Default | Role |
|----------|---------|------|
| `PLANNER_MAX_TOKENS` | `1536` | Orchestrator JSON steps |
| `SUBAGENT_MAX_TOKENS` | `3072` | Each subagent completion |
| `PLANNING_MAX_TOKENS` | `512` | One-shot plan JSON |
| `DIRECT_MAX_TOKENS` | `4096` | Direct / `--direct` |
| `PLANNER_TEMPERATURE` | `0.35` | Planner sampling |
| `SUBAGENT_TEMPERATURE` | `0.25` | Subagent sampling |
| `PLANNING_TEMPERATURE` | `0.2` | Planning call |
| `DIRECT_TEMPERATURE` | `0.35` | Direct chat |
| `PLANNER_HISTORY_MAX_CHARS` | `28000` | Trimmed planner history window |
| `INJECT_SUBAGENT_MAX_CHARS` | `5200` | Max size of `<subagent_results>` injected into history |

Same keys can be set under `[tokens]` in TOML (see below).

### Config files (optional)

Search order:

1. `./perplex-agent.toml` (project directory)
2. `~/.config/perplex-agent/config.toml`

Environment variables **override** file values for keys that support both (e.g. `PERPLEXITY_API_KEY` wins over `[perplexity] api_key`).

Example:

```toml
[perplexity]
api_key = "pplx-..."   # prefer env in CI; file is chmod 600 when written by setup
model = "sonar-pro"

[subagents]
model = "sonar"
max_concurrent = 4
auto_wait_after_spawn = true

[telegram]
announce_subagents = true
# allowed_user_ids = [123456789]

[orchestrator]
max_iterations = 12

[tokens]
# planner_max_tokens = 1536
# history_max_chars = 28000

[cli]
# planning_model = "sonar"
# assume_yes = false
```

Run **`perplex-agent setup`** for an interactive wizard (TTY); non-interactive environments still need env vars or a prepared TOML.

---

## CLI reference

| Command | Description |
|---------|-------------|
| `perplex-agent` | Opens the interactive slash shell (same as `chat` with no message). |
| `perplex-agent chat` | Same as above when no `message` argument. |
| `perplex-agent chat "…"` | One-shot: plan (unless `--no-plan` / `-y`) → orchestrator → answer. |
| `perplex-agent chat "…" --direct` | One-shot single Sonar completion. |
| `perplex-agent setup` | Interactive config wizard (`--telegram-only` for Telegram only). |
| `perplex-agent subagents list` | Table of persisted subagent runs (`--plain` for TSV-like lines). |
| `perplex-agent telegram run` | Long-polling bot (`-y` skips some confirmations). |

Global help:

```bash
uv run perplex-agent --help
uv run perplex-agent chat --help
```

---

## Interactive chat & slash commands

The default UX is a **REPL-style chat**: type a message or a **slash command** (`/…`). Commands support **Tab completion** and suggestions while typing after `/`.

Common commands (Spanish UI strings; see `/help` for the full table):

| Command | Purpose |
|---------|---------|
| `/help` | Command table |
| `/quit` / `/exit` | Leave the shell |
| `/setup` | Configuration wizard (Perplexity ± Telegram) |
| `/clear` | New orchestrator session (drops cached planner client) |
| `/mode` | Show or set `orchestrator` vs `direct` |
| `/model` | Show allowed Sonar models; `/model list`; `/model sonar-pro`; `/model reset` |
| `/subagents` | Subagent history |
| `/telegram` | Help; `/telegram run` starts the bot in this terminal |
| `/version` | Package version |

**Notes**

- The shell uses **async** + **prompt_toolkit**; the setup wizard avoids mixing broken `stdin` with Rich by using `/dev/tty` / nested prompts where needed.
- If the API key is missing, you’ll be offered `/setup` when you send a normal message.

---

## Sonar models

`/model list` shows the allowlisted IDs used by the CLI (aligned with public Sonar chat models):

- `sonar`
- `sonar-pro`
- `sonar-deep-research`
- `sonar-reasoning-pro`

Session choice overrides the default from **`-m`** and from **`[perplexity] model`** in TOML until `/model reset` or a new process.

---

## Telegram bot

1. Create a bot with [@BotFather](https://t.me/BotFather); copy the token.
2. Set `TELEGRAM_BOT_TOKEN` and `PERPLEXITY_API_KEY` (or use TOML).
3. Set `TELEGRAM_ALLOWED_USER_IDS` to your numeric Telegram user ID (e.g. from `@userinfobot`).
4. Run:

```bash
uv run perplex-agent telegram run
```

Long replies are split into multiple messages. If `announce_subagents` is enabled, you get a short line when each subagent finishes.

---

## How it works

- **Orchestrator**: the main model must return **one JSON object per turn** (`response_format` JSON schema). Actions: `final_answer`, `spawn_subagents`, `wait_subagents`. If the API returns **422** on structured output, the client retries once **without** `response_format` and parses JSON from text.
- **Subagents**: each run is an isolated `POST /v1/sonar` completion with its own system + user messages (no shared planner history).
- **Direct mode**: system prompt + user message in a single call (good for simple Q&A; orchestrator for multi-step / parallel search).

---

## Development

```bash
uv sync --extra dev
uv run pytest -q
```

Tests live under `tests/`. The package source is under `src/perplex_agent/`.

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| `asyncio.run() cannot be called from a running event loop` | Use an up-to-date checkout; the shell uses `prompt_async` and async setup paths. |
| Paste / wizard input garbled after chat prompt | Wizard reads secrets via prompt_toolkit in a thread; run `uv sync` and retry. |
| `422` on planner | The client falls back to unstructured parsing once. |
| Telegram “Unauthorized” | Set `TELEGRAM_ALLOWED_USER_IDS` or allowlist in TOML. |

---

## Links

- [Perplexity API docs](https://docs.perplexity.ai/)
- [Sonar models overview](https://docs.perplexity.ai/getting-started/models)
