#!/usr/bin/env python3
"""
Lightweight episode runner using OpenAI API directly + local mock-tools server.

No OpenClaw gateway needed. Runs the agent loop in-process:
  1. Starts mock-tools server in background
  2. Sends scenario prompt + AGENTS.md as system message to OpenAI
  3. Handles tool calls by proxying to mock-tools server
  4. Scores the final response with the scoring engine

Usage:
    OPENAI_API_KEY=sk-... python3 scripts/run_episode_openai.py --scenario inbox_triage --variant miner
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

# Add project root to path
SANDBOX_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SANDBOX_DIR))

from trajectory_sandbox.scoring import score_episode, format_score_summary

SCENARIOS_DIR = SANDBOX_DIR / "scenarios"
FIXTURES_DIR = SANDBOX_DIR / "fixtures"
MOCK_TOOLS_URL = "http://localhost:3001"


# ---------------------------------------------------------------------------
# OpenAI tool schemas matching the real OpenClaw tools
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": (
                "Execute a shell command. Supports: "
                "himalaya (email: envelope list, message read <id>, template write, message send), "
                "curl to Notion API (task queries, page get/create), "
                "curl to Google Calendar API or gcalcli (calendar events), "
                "gh (GitHub CLI)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "slack",
            "description": (
                "Interact with Slack. Use 'action' to specify the operation: "
                "readMessages (channelId, limit), sendMessage (to, content), "
                "react (channelId, messageId, emoji), memberInfo (userId), listPins, emojiList."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action: readMessages, sendMessage, react, memberInfo, listPins, emojiList"},
                    "channelId": {"type": "string", "description": "Channel ID (e.g. C_ENG, C_INCIDENTS, C_GENERAL)"},
                    "to": {"type": "string", "description": "Channel or user to send to"},
                    "content": {"type": "string", "description": "Message content"},
                    "limit": {"type": "integer", "description": "Max messages to return"},
                    "userId": {"type": "string", "description": "User ID for memberInfo"},
                    "messageId": {"type": "string", "description": "Message ID"},
                    "emoji": {"type": "string", "description": "Emoji name"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search memory files by query. Returns matching snippets from stored notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "maxResults": {"type": "integer", "description": "Max results to return"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get",
            "description": "Read a specific file from memory storage by path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read (e.g. goals.md, sprint_state.json)"},
                    "from": {"type": "integer", "description": "Start line number"},
                    "lines": {"type": "integer", "description": "Number of lines to read"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns a list of results with title, URL, and snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "count": {"type": "integer", "description": "Number of results"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a file from the workspace (e.g. USER.md, AGENTS.md).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                },
                "required": ["path"],
            },
        },
    },
]


def load_scenario(name: str) -> dict:
    path = SCENARIOS_DIR / f"{name}.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_agents_md(scenario: str, variant: str, agents_md_path: str = None) -> str:
    if agents_md_path:
        p = Path(agents_md_path)
        if not p.is_absolute():
            p = SANDBOX_DIR / p
        return p.read_text()
    config = load_scenario(scenario)
    agents_file = config["variants"][variant]
    path = FIXTURES_DIR / scenario / agents_file
    return path.read_text()


def load_user_md(scenario: str) -> str:
    path = FIXTURES_DIR / scenario / "USER.md"
    if path.exists():
        return path.read_text()
    return ""


def start_mock_server(scenario: str) -> subprocess.Popen:
    """Start mock-tools server in background."""
    env = os.environ.copy()
    env["FIXTURES_PATH"] = str(FIXTURES_DIR)
    env["SCENARIO"] = scenario
    env["LOG_PATH"] = str(SANDBOX_DIR / "logs")

    proc = subprocess.Popen(
        [sys.executable, "-m", "trajectory_sandbox.mock_tools.server"],
        env=env,
        cwd=str(SANDBOX_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


def wait_for_mock_server(timeout: int = 15) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(f"{MOCK_TOOLS_URL}/health", timeout=2)
            if r.status_code == 200:
                return True
        except httpx.RequestError:
            time.sleep(0.5)
    return False


def reset_scenario(scenario: str):
    try:
        httpx.post(f"{MOCK_TOOLS_URL}/set_scenario/{scenario}", timeout=5)
    except httpx.RequestError:
        pass


def call_mock_tool(tool_name: str, args: dict) -> str:
    """Call a tool on the mock server and return the result as string."""
    try:
        r = httpx.post(
            f"{MOCK_TOOLS_URL}/tools/{tool_name}",
            json=args,
            timeout=10,
        )
        return r.text
    except httpx.RequestError as e:
        return json.dumps({"error": str(e)})


def get_tool_calls_log() -> list:
    try:
        r = httpx.get(f"{MOCK_TOOLS_URL}/tool_calls", timeout=5)
        if r.status_code == 200:
            return r.json().get("calls", [])
    except httpx.RequestError:
        pass
    return []


def run_agent_loop(scenario: str, variant: str, model: str = "gpt-4o",
                    agents_md_path: str = None) -> dict:
    """Run the full agent loop: system prompt → tool calls → final response."""
    from openai import OpenAI

    client = OpenAI()
    config = load_scenario(scenario)

    agents_md = load_agents_md(scenario, variant, agents_md_path=agents_md_path)
    user_md = load_user_md(scenario)
    prompt = config["prompt"].strip()

    system_msg = f"""You are a helpful assistant with access to tools.

# AGENTS.md
{agents_md}

# USER.md
{user_md}
"""

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": prompt},
    ]

    tool_call_count = 0
    max_iterations = 20  # safety limit

    print(f"\n{'='*60}")
    print(f"Running: {scenario} / {variant} (model: {model})")
    print(f"{'='*60}")
    print(f"Prompt: {prompt[:100]}...")

    for iteration in range(max_iterations):
        print(f"\n--- Iteration {iteration + 1} ---")

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.2,
        )

        choice = response.choices[0]
        msg = choice.message

        # If no tool calls, we're done
        if not msg.tool_calls:
            print(f"Final response ({len(msg.content or '')} chars)")
            break

        # Process tool calls
        messages.append(msg.model_dump())

        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            tool_call_count += 1
            print(f"  Tool #{tool_call_count}: {tool_name}({json.dumps(args)[:80]})")

            result = call_mock_tool(tool_name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        if choice.finish_reason == "stop":
            break
    else:
        print("WARNING: Hit max iterations")

    # Extract final response
    final_response = msg.content or ""

    # Get tool calls from mock server log
    logged_calls = get_tool_calls_log()

    print(f"\nTotal tool calls: {tool_call_count}")
    print(f"Response length: {len(final_response)} chars")

    return {
        "scenario": scenario,
        "variant": variant,
        "response": final_response,
        "tool_calls_total": tool_call_count,
        "tool_calls_raw": logged_calls,
        "tool_calls_by_type": _count_by_type(logged_calls),
    }


def _count_by_type(calls: list) -> dict:
    counts = {}
    for c in calls:
        t = c.get("tool", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description="Run episode with OpenAI + mock tools")
    parser.add_argument("--scenario", "-s", default="inbox_triage")
    parser.add_argument("--variant", "-v", default="miner")
    parser.add_argument("--model", "-m", default="gpt-4o")
    parser.add_argument("--output", "-o", type=str, help="Save results JSON")
    parser.add_argument("--agents-md", type=str,
                        help="Override: use this AGENTS.md file instead of variant lookup")
    args = parser.parse_args()

    # Check API key
    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    # Start mock server
    print("Starting mock-tools server...")
    mock_proc = start_mock_server(args.scenario)

    try:
        if not wait_for_mock_server():
            print("ERROR: Mock server failed to start")
            mock_proc.kill()
            sys.exit(1)

        print(f"Mock server ready (scenario: {args.scenario})")
        reset_scenario(args.scenario)

        # Run agent loop
        result = run_agent_loop(args.scenario, args.variant, args.model,
                                agents_md_path=args.agents_md)

        # Score
        config = load_scenario(args.scenario)
        score = score_episode(result, config["scoring"])

        print(f"\n{'='*60}")
        print("SCORING RESULTS")
        print(f"{'='*60}")
        print(format_score_summary(score))

        # Print response preview
        print(f"\n{'='*60}")
        print("RESPONSE PREVIEW")
        print(f"{'='*60}")
        print(result["response"][:2000])
        if len(result["response"]) > 2000:
            print("... (truncated)")

        # Save if requested
        if args.output:
            output = {**result, "score": score}
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, "w") as f:
                json.dump(output, f, indent=2)
            print(f"\nResults saved to: {args.output}")

    finally:
        mock_proc.kill()
        mock_proc.wait()
        print("\nMock server stopped.")


if __name__ == "__main__":
    main()
