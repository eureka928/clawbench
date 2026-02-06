"""
Mock Tool Server — Generic tool server for trajectory sandbox evaluation.

Serves deterministic responses from fixture files. All common productivity
tool categories are supported: email, calendar, messaging (Slack), tasks,
documents, contacts, memory, and web search.

Architecture:
  - TOOL_CATALOG defines every known tool and its behavior
  - A single generic endpoint `/tools/{tool_name}` dispatches all calls
  - Behaviors: fixture_list, fixture_lookup, write_action, custom
  - Adding a new tool = one dict entry + optional fixture file
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mock-tools")

# ---------------------------------------------------------------------------
# App & Config
# ---------------------------------------------------------------------------
app = FastAPI(title="Trajectory Sandbox — Mock Tools Server")

FIXTURES_PATH = Path(os.getenv("FIXTURES_PATH", "./fixtures"))
LOG_PATH = Path(os.getenv("LOG_PATH", "./logs"))
CURRENT_SCENARIO = os.getenv("SCENARIO", "inbox_triage")

LOG_PATH.mkdir(parents=True, exist_ok=True)

# In-memory logs (reset per scenario)
tool_calls: list[dict] = []
all_requests: list[dict] = []


# ============================================================================
# Tool Catalog — every known mock tool
# ============================================================================
#
# behavior types:
#   fixture_list   — load fixture JSON (array), return as {response_key: [...]}
#   fixture_lookup — find one item in fixture array by ID field
#   write_action   — log the call, return a success response
#   custom         — per-tool handler function
#
TOOL_CATALOG: dict[str, dict[str, Any]] = {
    # -- Email & Inbox -------------------------------------------------------
    "inbox.list": {
        "behavior": "fixture_list",
        "fixture": "inbox.json",
        "response_key": "messages",
        "transform": "inbox_summary",
    },
    "email.read": {
        "behavior": "fixture_lookup",
        "fixture": "inbox.json",
        "lookup_field": "id",
        "param_field": "message_id",
        "response_key": "email",
    },
    "email.draft": {
        "behavior": "custom",
        "handler": "handle_email_draft",
    },
    "email.send": {
        "behavior": "write_action",
        "default_response": {"status": "sent"},
        "echo_fields": ["draft_id"],
        "irreversible": True,
    },
    "email.archive": {
        "behavior": "write_action",
        "default_response": {"status": "archived"},
        "echo_fields": ["message_id"],
    },

    # -- Calendar ------------------------------------------------------------
    "calendar.read": {
        "behavior": "fixture_list",
        "fixture": "calendar.json",
        "response_key": "events",
    },
    "calendar.create": {
        "behavior": "write_action",
        "default_response": {"status": "created", "event_id": "evt_{ts}"},
        "echo_fields": ["title", "start", "end"],
        "irreversible": True,
    },
    "calendar.update": {
        "behavior": "write_action",
        "default_response": {"status": "updated"},
        "echo_fields": ["event_id", "title", "start", "end"],
    },
    "calendar.delete": {
        "behavior": "write_action",
        "default_response": {"status": "deleted"},
        "echo_fields": ["event_id"],
        "irreversible": True,
    },

    # -- Messaging (Slack-like) ----------------------------------------------
    "slack.list_channels": {
        "behavior": "fixture_list",
        "fixture": "slack_channels.json",
        "response_key": "channels",
    },
    "slack.read_messages": {
        "behavior": "custom",
        "handler": "handle_slack_read_messages",
    },
    "slack.post_message": {
        "behavior": "write_action",
        "default_response": {"status": "posted", "message_id": "slack_msg_{ts}"},
        "echo_fields": ["channel", "text"],
        "irreversible": True,
    },
    "slack.send_dm": {
        "behavior": "write_action",
        "default_response": {"status": "sent", "message_id": "dm_{ts}"},
        "echo_fields": ["user", "text"],
        "irreversible": True,
    },

    # -- Tasks (Jira / Linear-like) ------------------------------------------
    "task.list": {
        "behavior": "fixture_list",
        "fixture": "tasks.json",
        "response_key": "tasks",
    },
    "task.get": {
        "behavior": "fixture_lookup",
        "fixture": "tasks.json",
        "lookup_field": "id",
        "param_field": "task_id",
        "response_key": "task",
    },
    "task.create": {
        "behavior": "write_action",
        "default_response": {"status": "created", "task_id": "task_{ts}"},
        "echo_fields": ["title", "description", "assignee", "priority"],
    },
    "task.update": {
        "behavior": "write_action",
        "default_response": {"status": "updated"},
        "echo_fields": ["task_id", "status", "priority"],
    },

    # -- Documents (Drive / Notion-like) -------------------------------------
    "doc.list": {
        "behavior": "fixture_list",
        "fixture": "documents.json",
        "response_key": "documents",
    },
    "doc.read": {
        "behavior": "fixture_lookup",
        "fixture": "documents.json",
        "lookup_field": "id",
        "param_field": "document_id",
        "response_key": "document",
    },
    "doc.create": {
        "behavior": "write_action",
        "default_response": {"status": "created", "document_id": "doc_{ts}"},
        "echo_fields": ["title", "content"],
    },

    # -- Contacts ------------------------------------------------------------
    "contacts.list": {
        "behavior": "fixture_list",
        "fixture": "contacts.json",
        "response_key": "contacts",
    },
    "contacts.get": {
        "behavior": "fixture_lookup",
        "fixture": "contacts.json",
        "lookup_field": "id",
        "param_field": "contact_id",
        "response_key": "contact",
    },

    # -- Memory / Notes ------------------------------------------------------
    "memory.read": {
        "behavior": "custom",
        "handler": "handle_memory_read",
    },
    "memory.write": {
        "behavior": "write_action",
        "default_response": {"success": True},
        "echo_fields": ["path"],
    },

    # -- Web Search (mock) ---------------------------------------------------
    "search.web": {
        "behavior": "custom",
        "handler": "handle_search_web",
    },
}


# ============================================================================
# Helpers
# ============================================================================

def load_fixture(scenario: str, filename: str) -> Any | None:
    """Load a fixture file, returning None if it doesn't exist."""
    path = FIXTURES_PATH / scenario / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def log_tool_call(tool: str, args: dict, result: Any):
    """Log a successful tool call for later analysis."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": args,
        "result_summary": str(result)[:200],
    }
    tool_calls.append(entry)

    log_file = LOG_PATH / f"{CURRENT_SCENARIO}_calls.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _flexget(data: dict, *keys: str, default: Any = None) -> Any:
    """Get the first non-None value from data for any of the given keys."""
    for k in keys:
        val = data.get(k)
        if val is not None:
            return val
    return default


# ============================================================================
# Custom Handlers
# ============================================================================

def handle_email_draft(data: dict) -> dict:
    message_id = _flexget(data, "message_id", "messageId", "email_id", "emailId", "id", default="unknown")
    instructions = _flexget(data, "instructions", "body", "content", "text", "reply", "message", default="No instructions provided")
    draft_id = f"draft_{message_id}"
    preview = f"[Draft reply to {message_id}]: {str(instructions)[:100]}..."
    return {"draft_id": draft_id, "preview": preview}


def handle_memory_read(data: dict) -> dict:
    req_path = _flexget(data, "path", "key", default="")
    try:
        path = FIXTURES_PATH / CURRENT_SCENARIO / "memory" / req_path
        if path.exists():
            return {"content": path.read_text(), "exists": True}
    except Exception:
        pass
    return {"content": None, "exists": False}


def handle_slack_read_messages(data: dict) -> dict:
    """Read Slack messages, optionally filtered by channel."""
    channel = _flexget(data, "channel", "channel_id", default=None)
    messages = load_fixture(CURRENT_SCENARIO, "slack_messages.json") or []
    if channel:
        # Normalize: match with or without '#' prefix
        ch = channel.lstrip("#")
        messages = [
            m for m in messages
            if m.get("channel", "").lstrip("#") == ch
        ]
    return {"messages": messages}


def handle_search_web(data: dict) -> dict:
    """Mock web search — returns fixture results or a generic placeholder."""
    query = _flexget(data, "query", "q", default="")
    results = load_fixture(CURRENT_SCENARIO, "web_search_results.json")
    if results:
        if isinstance(results, dict) and query in results:
            return {"results": results[query]}
        if isinstance(results, list):
            return {"results": results}
    return {
        "results": [
            {
                "title": f"Search result for: {query}",
                "url": f"https://example.com/search?q={query}",
                "snippet": f"Mock search result for '{query}'",
            }
        ]
    }


CUSTOM_HANDLERS: dict[str, Any] = {
    "handle_email_draft": handle_email_draft,
    "handle_memory_read": handle_memory_read,
    "handle_slack_read_messages": handle_slack_read_messages,
    "handle_search_web": handle_search_web,
}


# ============================================================================
# Middleware — log every POST /tools/* request
# ============================================================================

@app.middleware("http")
async def log_all_requests_middleware(request: Request, call_next):
    body_json = None

    if request.method == "POST" and request.url.path.startswith("/tools/"):
        body_bytes = await request.body()
        try:
            body_json = json.loads(body_bytes) if body_bytes else None
        except (json.JSONDecodeError, ValueError):
            body_json = {"_raw": body_bytes.decode("utf-8", errors="replace")}

        logger.debug("REQUEST  %s %s  body=%s", request.method, request.url.path, json.dumps(body_json, default=str)[:500])

    response = await call_next(request)

    if request.method == "POST" and request.url.path.startswith("/tools/"):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": request.url.path.replace("/tools/", ""),
            "request_body": body_json,
            "status_code": response.status_code,
            "success": 200 <= response.status_code < 300,
        }
        all_requests.append(entry)

        debug_log = LOG_PATH / f"{CURRENT_SCENARIO}_all_requests.jsonl"
        with open(debug_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

        if response.status_code >= 400:
            logger.warning("FAILED   %s  status=%d", request.url.path, response.status_code)

    return response


# ============================================================================
# Generic Tool Dispatch
# ============================================================================

@app.post("/tools/{tool_name:path}")
async def handle_tool(tool_name: str, request: Request):
    """Generic handler — dispatches any tool call via the catalog."""
    global CURRENT_SCENARIO

    tool_def = TOOL_CATALOG.get(tool_name)
    if not tool_def:
        raise HTTPException(404, f"Unknown tool: {tool_name}. Known tools: {sorted(TOOL_CATALOG.keys())}")

    # Parse body
    body = await request.body()
    try:
        data = json.loads(body) if body else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    logger.info("TOOL %-25s  body=%s", tool_name, json.dumps(data, default=str)[:500])

    behavior = tool_def["behavior"]
    result: dict

    # --- fixture_list: load fixture array, return keyed -----------------------
    if behavior == "fixture_list":
        fixture_data = load_fixture(CURRENT_SCENARIO, tool_def["fixture"])
        if fixture_data is None:
            fixture_data = []
        # Apply inbox-style summary transform if specified
        if tool_def.get("transform") == "inbox_summary":
            fixture_data = [
                {
                    "id": msg.get("id"),
                    "sender": msg.get("sender"),
                    "subject": msg.get("subject"),
                    "snippet": str(msg.get("body", ""))[:100],
                    "received_ts": msg.get("received_ts", ""),
                    "labels": msg.get("labels", []),
                    "is_urgent": msg.get("is_urgent", False),
                }
                for msg in fixture_data
            ]
        result = {tool_def["response_key"]: fixture_data}

    # --- fixture_lookup: find one item by ID ----------------------------------
    elif behavior == "fixture_lookup":
        fixture_data = load_fixture(CURRENT_SCENARIO, tool_def["fixture"])
        if fixture_data is None:
            result = {tool_def["response_key"]: None, "found": False}
        else:
            lookup_val = _flexget(data, tool_def["param_field"], "id", default="")
            item = next(
                (x for x in fixture_data if str(x.get(tool_def["lookup_field"])) == str(lookup_val)),
                None,
            )
            result = {tool_def["response_key"]: item, "found": item is not None}

    # --- write_action: log and return success ---------------------------------
    elif behavior == "write_action":
        result = dict(tool_def.get("default_response", {"success": True}))
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        # Template {field} placeholders in response values
        for key, val in list(result.items()):
            if isinstance(val, str) and "{" in val:
                try:
                    result[key] = val.format(ts=ts, **data)
                except (KeyError, IndexError):
                    result[key] = val.replace("{ts}", ts)
        # Echo requested fields from input
        for field in tool_def.get("echo_fields", []):
            if field in data:
                result[field] = data[field]

    # --- custom: delegate to a handler function -------------------------------
    elif behavior == "custom":
        handler = CUSTOM_HANDLERS.get(tool_def["handler"])
        if not handler:
            raise HTTPException(500, f"Missing handler: {tool_def['handler']}")
        result = handler(data)

    else:
        raise HTTPException(500, f"Unknown behavior: {behavior}")

    log_tool_call(tool_name, data, result)
    return JSONResponse(content=result)


# ============================================================================
# Control Endpoints
# ============================================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "scenario": CURRENT_SCENARIO,
        "tools_available": len(TOOL_CATALOG),
    }


@app.post("/set_scenario/{scenario}")
async def set_scenario(scenario: str):
    """Set the current scenario (switches fixture directory)."""
    global CURRENT_SCENARIO
    CURRENT_SCENARIO = scenario
    tool_calls.clear()
    all_requests.clear()
    logger.info("Scenario reset to: %s", scenario)
    return {"scenario": CURRENT_SCENARIO}


@app.get("/tool_calls")
async def get_tool_calls():
    """Successful tool calls in this session."""
    return {"calls": tool_calls}


@app.get("/all_requests")
async def get_all_requests():
    """ALL requests including failures — for debugging."""
    return {
        "requests": all_requests,
        "summary": {
            "total": len(all_requests),
            "success": sum(1 for r in all_requests if r["success"]),
            "failed": sum(1 for r in all_requests if not r["success"]),
        },
    }


@app.get("/tools")
async def list_tools():
    """List all known tools and their behavior type."""
    tools = []
    for name, defn in TOOL_CATALOG.items():
        tools.append({
            "name": name,
            "behavior": defn["behavior"],
            "irreversible": defn.get("irreversible", False),
            "fixture": defn.get("fixture"),
        })
    return {"tools": tools, "count": len(tools)}


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)
