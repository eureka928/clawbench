# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Trajectory Sandbox is a deterministic, scenario-based evaluation framework for OpenClaw agents. It uses fixture-backed mock tools to replay realistic tool interactions, then scores the agent's tool-call sequence and response against a YAML rubric. All scoring is regex-based (no LLM judge), producing reproducible [0, 1] scores suitable for A/B testing AGENTS.md policy variants and RL optimization.

## Commands

### Setup
```bash
pip install -r requirements.txt        # Host-side deps (httpx, pyyaml)
cp .env.example .env                   # Add ANTHROPIC_API_KEY or OPENAI_API_KEY
```

### Run a scenario (full Docker stack)
```bash
python scripts/setup_scenario.py <scenario> <variant>   # e.g. client_escalation optimized
docker compose up -d --build
python scripts/run_episode.py --scenario <scenario> --wait
```

### Run mock tools server standalone (no API key needed)
```bash
FIXTURES_PATH=./fixtures SCENARIO=client_escalation python -m trajectory_sandbox.mock_tools.server
```

### Testing (4 layers)
```bash
# Layer 1: Handler unit tests (no server, in-process)
python scripts/test_handlers.py
python scripts/test_handlers.py --scenario client_escalation

# Layer 2: Scoring validation (in-process)
python scripts/test_scoring.py

# Layer 3: Mock server HTTP tests (requires running mock server)
python scripts/test_mock_tools.py

# Layer 4: Full Docker integration
./scripts/test_full.sh

# Quick (skip Docker):
./scripts/test_full.sh --quick

# Docker only:
./scripts/test_full.sh --docker-only
```

### Batch run (all scenarios x variants)
```bash
python scripts/run_batch.py --start --wait --stop
python scripts/run_batch.py --wait --only morning_brief
```

## Architecture

```
run_episode.py / run_batch.py
        │
   HTTP POST to OpenClaw Gateway (:18790)
        │                    │
        │             Tool calls proxied to
        │                    │
        │          Mock Tools Server (:3001)
        │          (FastAPI, fixture-backed)
        │                    │
        │              fixtures/{scenario}/
        │              (inbox.json, slack_messages.json,
        │               tasks.json, calendar.json, etc.)
        │
   Collect tool_calls + response
        │
   scoring.py → evaluate against scenario YAML rubric
        │
   results/ (JSON + markdown)
```

**Two Docker services** (`docker-compose.yml`):
- `mock-tools` — Python FastAPI server (port 3001), serves fixture data via pattern-matched tool handlers
- `openclaw-gateway` — Node.js LLM gateway (port 18790), runs the agent loop with real LLM calls

### Key source layout

- **`trajectory_sandbox/mock_tools/server.py`** — The mock tools FastAPI server. All tool handlers live here. `handle_exec()` uses regex pattern matching against CLI commands (himalaya for email, curl for Notion/Google Calendar, gh for GitHub). `handle_slack()` dispatches on the `action` parameter. Tools log every call to JSONL.
- **`trajectory_sandbox/harness/`** — Episode runner (`episode.py`), scenario Pydantic models (`scenario.py`), HTTP clients (`client.py`), workspace file setup (`workspace.py`).
- **`trajectory_sandbox/scoring.py`** — Regex-based scoring engine. Check types: `tool_called`, `tool_not_called`, `tool_count_max`, `tool_count_min`, `tool_called_before`, `response_contains`, `response_excludes`. Scores are categorized (safety, correctness, efficiency, structure).
- **`trajectory_sandbox/cli.py`** — Typer CLI entry point (`sandbox` command).
- **`scenarios/*.yaml`** — Scenario definitions with tool lists, prompts, variants, and scoring rubrics.
- **`fixtures/{scenario}/`** — Deterministic fixture data per scenario. Each has inbox/slack/tasks/calendar JSON, AGENTS.md variants (baseline/optimized), USER.md, and optional memory files.
- **`openclaw-plugin/`** — TypeScript plugin registering 25 tool schemas with OpenClaw, proxying calls to the mock server.
- **`scripts/`** — Setup, episode runners, batch runner, and all 4 test layers.

### Design principles

- **Policy/reward separation**: AGENTS.md (policy) varies across variants; the scoring rubric (reward) stays fixed per scenario, enabling fair A/B comparison.
- **Deterministic fixtures**: All mock tool responses come from JSON fixtures. Same inputs always produce same outputs.
- **Irreversibility flagging**: Destructive tool calls (email.send, calendar.create) return `"warning": "IRREVERSIBLE"` for scoring safety checks.
- **Trajectory-aware scoring**: Evaluates the tool call sequence, not just the final text response.

## Scenarios

| Scenario | Difficulty | Key tools |
|---|---|---|
| `client_escalation` | Hard | exec, slack, memory, web_search, read |
| `inbox_to_action` | Hard | exec, slack, memory, web_search, read |
| `morning_brief` | Medium | exec, slack, memory, read |
| `team_standup` | Medium | exec, slack, memory, read |
| `inbox_triage` | Easy | exec, slack |

## Tech stack

- Python 3.11+ (FastAPI, Pydantic, httpx, typer, rich, uvicorn)
- TypeScript/Node.js (OpenClaw plugin)
- Docker Compose (two-service orchestration)
- pytest + pytest-asyncio + ruff (dev dependencies)
