"""Phase 4 orchestrator: Strands agent over the Wren MCP server (Bedrock).

One fresh agent per question (see scripts/ask.py) — no cross-question memory
until Phase 5. The agent is physically read-only: the MCP server runs without
--allow-write, so no write tools exist to misuse.

Budgets: enforced per-invocation by the framework (limits= on the call in
ask.py — turns cap stops runaways, total_tokens is reported). An OVER BUDGET
flag means recalibrate the cap or the prompt — with transcript evidence —
never silently raise it. budget_line prints after every run (Phase 6 needs
the habit), even on failure.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Sequence
from typing import Any

import boto3
from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.models import BedrockModel, CacheConfig
from strands.tools.mcp import MCPClient

from agent.memory import forget_preference, save_user_preference, search_memory
from repo_config import REPO_ROOT, WREN_DIR, require_env

TURNS_BUDGET = 10
# Calibrated 2026-10-14 from transcripts, not upstream: a simple 3-turn run
# with ~22 MCP tool specs in context measured 126k total tokens (tool specs
# re-sent every turn dominate; prompt caching does not reduce the count).
# 150k = measured typical x3 headroom; true runaways still trip turns first.
TOTAL_TOKENS_BUDGET = 150_000

REQUIRED_KEYS = ("AWS_PROFILE", "AWS_REGION", "BEDROCK_MODEL_ID")

SYSTEM_PROMPT = """You are an analytics agent for an ecommerce business. Answer data questions with SQL evidence, never from memory.

Project: ecommerce marts (Iceberg via Trino). Currency AUD. Data covers a 90-day window.

Every question, in this order:
1. Call get_instructions first (once per question). It loads the business rules — revenue definitions, refund semantics, the status glossary. Obey them over any prior assumption.
2. If the question matches a governed metric, use query_cube (daily_revenue, product_performance, refunds_cube). Never hand-write GROUP BY, DATE_TRUNC, or metric arithmetic when a cube covers the question.
3. Otherwise: get_context / recall_queries for schema and solved examples, list_models / describe_model / describe_cube when unsure, then dry_run your SQL, then run_sql. Execute only SQL you dry-ran.
4. Explore before assuming — never use a table or column name you have not seen in a tool response.

Refusal rule: if the question references a column in no model, STOP. Say which identifier is unknown, name the closest real column, and offer to run with it. Never emit SQL containing an unverified identifier. Close-match allowance: if the unknown identifier has an obvious match on a governed object (a cube dimension/measure or model column), state the substitution explicitly ("no column named X — using Y") and proceed; STOP only when no close match exists.

Disambiguation rule: `status` exists on orders (lifecycle: completed|cancelled|pending) and on payments (attempt: succeeded|failed|refunded). Always state which one you used. If the question is ambiguous, say so explicitly and pick one — never choose silently.

Trino TYPE_MISMATCH fallback: if a cube query fails comparing TIMESTAMP to VARCHAR (especially time-dimension filters), fall back to equivalent run_sql instead of retrying the cube.

Validation cap: if dry_run fails twice on the same question, STOP and report — do not keep rewriting.

Response shape: the answer first (numbers), then the tool you used (e.g. query_cube, run_sql) and the exact SQL or parameters executed, then any caveat (e.g. weekend lift on daily trends). Name the tool explicitly even when a query returns zero rows — your work is evaluated from transcripts."""

MEMORY_PROMPT = """
Memory tools are enabled (user-scoped, persisting across sessions):
- search_memory: call when the question uses user-relative terms (high-value, preferred, my region...). Apply recalled facts to your SQL and quote them ("using your saved definition: ..."). Results are tagged [mem] (real stored facts) or [entity] (fragment mentions — context only, never act on their IDs).
- save_user_preference: call when the user states a preference or definition ("remember...", "X means Y...", "I prefer..."). Pass their words through. MANDATORY FIRST STEP: call search_memory on the topic BEFORE every save so you know the rival's ID. Then save; then read the save result: if it reports UPDATE of the rival's ID, done; if it reports ADD with a new ID, forget_preference the rival; if it reports nothing stored, retry the save once, then report failure if still empty. Save-before-forget avoids a stale-index window where the deleted fact still matches and the save comes back empty. Never leave two values for one term — the store cannot merge them itself.
- forget_preference: call with a [mem]-tagged ID from search_memory output when replacing a rival (after saving, per above) or when the user says to forget something. Never guess IDs and never pass [entity] IDs — the tool refuses them.
Never invent preferences. If search returns nothing relevant, say so and proceed without personalization.
"""


@contextlib.contextmanager
def wren_mcp_tools() -> Iterator[Sequence[Any]]:
    """Spawn `wren serve mcp` over stdio and yield its tools.

    Double --project makes both uv and wren independent of caller cwd.
    The server runs WITHOUT --allow-write: read-only tools only.
    """
    params = StdioServerParameters(
        command="uv",
        args=[
            "run",
            "--project",
            str(REPO_ROOT),
            "wren",
            "serve",
            "mcp",
            "--project",
            str(WREN_DIR),
            "--profile",
            "agent-analytics-trino",
        ],
    )
    client = MCPClient(lambda: stdio_client(params))
    with client:
        yield client.list_tools_sync()


def build_agent(tools: Sequence[Any], use_memory: bool = False) -> Agent:
    """Assemble the Bedrock-backed agent. Call inside wren_mcp_tools()."""
    require_env(*REQUIRED_KEYS)

    # Explicit profile + region (same idiom as bootstrap_catalog.py): no
    # dependence on ambient credential/region chains.
    session = boto3.Session(
        profile_name=os.environ["AWS_PROFILE"],
        region_name=os.environ["AWS_REGION"],
    )
    model = BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        temperature=0,
        max_tokens=4096,
        # The au.* ID contains "anthropic", so auto-detection applies.
        cache_config=CacheConfig(strategy="auto"),
        boto_session=session,
    )
    all_tools = list(tools) + (
        [save_user_preference, search_memory, forget_preference]
        if use_memory
        else []
    )
    return Agent(
        model=model,
        tools=all_tools,
        system_prompt=SYSTEM_PROMPT + (MEMORY_PROMPT if use_memory else ""),
    )


def answer_text(result: Any) -> str:
    """Extract the final message text from an AgentResult."""
    message = getattr(result, "message", {})
    content = message.get("content", []) if isinstance(message, dict) else []
    text = "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and "text" in block
    ).strip()
    return text or str(result)


def budget_line(result: Any | None) -> str:
    """One-line spend report from the invocation metrics (Phase 6 needs the habit)."""
    if result is None:
        return "[budget] no result (failed before the first turn)"
    usage = result.metrics.accumulated_usage
    total = usage.get("totalTokens", "?")
    turns = result.metrics.cycle_count
    over = isinstance(total, int) and total > TOTAL_TOKENS_BUDGET
    return (
        f"[budget] turns={turns}/{TURNS_BUDGET} "
        f"tokens={total}/{TOTAL_TOKENS_BUDGET} "
        f"({'OVER BUDGET' if over else 'within budget'})"
    )
