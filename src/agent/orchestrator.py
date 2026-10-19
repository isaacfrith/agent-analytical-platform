"""Phase 4 orchestrator: Strands agent over the Wren MCP server (Bedrock).

Entry point: answer_question(question, user_id, config=None, use_memory=True, domains=DOMAINS, on_event=None)
Returns AnswerResult with text, metrics, and pre-formatted budget line.
Passing on_event (the demo UI) streams trace events and adds the show_chart
tool; without it the agent is exactly the CLI/eval agent.

One fresh agent per question — no cross-question memory unless use_memory=True.
The agent is physically read-only: the MCP server runs without --allow-write.

Budgets: enforced per-invocation (turns cap stops runaways, total_tokens
reported). An OVER BUDGET flag means recalibrate the cap or the prompt —
with transcript evidence — never silently raise it.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import boto3
from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.models import BedrockModel, CacheConfig
from strands.tools.mcp import MCPClient

from agent.memory import MemoryTools
from agent.trace import ChartTools, EventSink, TraceRecorder
from config import Config
from memory import build_memory_store
from repo_config import REPO_ROOT, WREN_DIR

_PROMPTS_DIR = REPO_ROOT / "prompts"
_DOMAINS = ("ecommerce", "energy")

# Public for ask.py's CLI parsing (can be removed once ask.py is refactored)
DOMAINS = _DOMAINS
TURNS_BUDGET = 10
TOTAL_TOKENS_BUDGET = 150_000


@dataclass
class AnswerResult:
    text: str
    turns: int
    total_tokens: int | str
    over_budget: bool
    metrics_line: str


def _load_prompt(domains: Sequence[str] = _DOMAINS) -> str:
    """Assemble base + domain blocks. Unknown domains fail loud."""
    parts = [(_PROMPTS_DIR / "base.md").read_text().strip()]
    for domain in domains:
        path = _PROMPTS_DIR / f"{domain}.md"
        if not path.is_file():
            raise ValueError(f"unknown prompt domain: {domain!r}")
        parts.append(path.read_text().strip())
    return "\n\n".join(parts)


def _prompt_version(domains: Sequence[str] = _DOMAINS) -> str:
    """Short hash of the assembled prompt — recorded in eval reports."""
    import hashlib

    return hashlib.sha256(_load_prompt(domains).encode()).hexdigest()[:12]


_SYSTEM_PROMPT = _load_prompt()

_MEMORY_PROMPT = """
Memory tools are enabled (user-scoped, persisting across sessions):
- search_memory: call when the question uses user-relative terms (high-value, preferred, my region...). Apply recalled facts to your SQL and quote them ("using your saved definition: ..."). Results are tagged [mem] (real stored facts) or [entity] (fragment mentions — context only, never act on their IDs).
- save_user_preference: call when the user states a preference or definition ("remember...", "X means Y...", "I prefer..."). Pass their words through. MANDATORY FIRST STEP: call search_memory on the topic BEFORE every save so you know the rival's ID. Then save; then read the save result: if it reports UPDATE of the rival's ID, done; if it reports ADD with a new ID, forget_preference the rival; if it reports nothing stored, the extractor refused (usually a contradiction with the rival) — forget_preference the rival's [mem] ID FIRST, then save again (with the rival gone the save lands as novel); if still empty after that, retry the save once more, then report failure if still empty. Save-before-forget avoids a stale-index window where the deleted fact still matches and the save comes back empty — the only exception is the refused-save path, where forget-before-save is the documented recovery. Never leave two values for one term — the store cannot merge them itself.
- forget_preference: call with a [mem]-tagged ID from search_memory output when replacing a rival (after saving, per above) or when the user says to forget something. Never guess IDs and never pass [entity] IDs — the tool refuses them.
Never invent preferences. If search returns nothing relevant, say so and proceed without personalization.
"""


_CHART_PROMPT = """
Chart tool is enabled (a visual demo UI shows your work):
- Charting never changes WHAT you answer or how many queries you run. If the data cannot answer the question, refuse exactly as you otherwise would and do not fetch rows just to have something to chart.
- show_chart: when a data step returned the rows that answer the question, call show_chart ONCE before your final answer, naming columns of that step's rows (x, y, optional series). Rows come from the tool result — never type numbers into it. If the answer needs arithmetic over rows (a share, a total, a ranking), do it in SQL and chart that step; never compute figures yourself from returned rows.
- Your final answer always comes first: skip show_chart rather than run out of turns. line for days, bar to compare regions or fuels, stacked_bar for fuel mix, table otherwise. Set map_key to the network_region or facility_code column whenever rows are per region or per facility, so the map recolours. To chart facilities on the map, include facility_code in the SELECT. Skip it when you refused or returned no rows.
"""


def _sql_compiler(tools: Sequence[Any]):
    """Compiled target-dialect SQL for a data step, via the read-only MCP tools."""
    by_name = {t.tool_name: t for t in tools}

    def compile_sql(name: str, tool_input: dict) -> str | None:
        if name == "query_cube":
            call, args = "query_cube", {**tool_input, "sql_only": True}
        elif "sql" in tool_input:
            call, args = "dry_plan", {"sql": tool_input["sql"]}
        else:
            return None
        if call not in by_name:
            return None
        result = by_name[call].mcp_client.call_tool_sync(
            tool_use_id="trace-compile", name=call, arguments=args
        )
        if result.get("status") != "success":
            return None
        text = "\n".join(b.get("text", "") for b in result.get("content") or []).strip()
        try:
            payload = json.loads(text)
        except ValueError:
            return text or None
        if isinstance(payload, dict):
            return payload.get("sql") or payload.get("dialect_sql") or text
        return text

    return compile_sql


@contextlib.contextmanager
def _wren_mcp_tools() -> Iterator[Sequence[Any]]:
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


def _build_agent(
    tools: Sequence[Any],
    config: Config,
    use_memory: bool = False,
    domains: Sequence[str] = _DOMAINS,
    recorder: TraceRecorder | None = None,
) -> Agent:
    """Assemble the Bedrock-backed agent. Call inside _wren_mcp_tools()."""
    session = boto3.Session(
        profile_name=config.aws.profile,
        region_name=config.aws.region,
    )
    model = BedrockModel(
        model_id=config.aws.bedrock_model_id,
        temperature=0,
        max_tokens=4096,
        cache_config=CacheConfig(strategy="auto"),
        boto_session=session,
    )
    all_tools = list(tools)
    if use_memory:
        all_tools += MemoryTools(build_memory_store(config)).as_list()
    prompt = _load_prompt(domains) + (_MEMORY_PROMPT if use_memory else "")
    if recorder is not None:
        all_tools += ChartTools(recorder).as_list()
        prompt += _CHART_PROMPT
    return Agent(
        model=model,
        tools=all_tools,
        system_prompt=prompt,
        hooks=[recorder] if recorder is not None else None,
    )


def _extract_answer_text(result: Any) -> str:
    """Extract the final message text from an AgentResult."""
    message = getattr(result, "message", {})
    content = message.get("content", []) if isinstance(message, dict) else []
    text = "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and "text" in block
    ).strip()
    return text or str(result)


def _format_metrics(
    result: Any | None, tokens_budget: int = 150_000
) -> tuple[int, int | str, bool, str]:
    """Extract metrics from result and return (turns, tokens, over_budget, formatted_line)."""
    if result is None:
        return 0, "?", False, "[budget] no result (failed before the first turn)"
    usage = result.metrics.accumulated_usage
    total = usage.get("totalTokens", "?")
    turns = result.metrics.cycle_count
    over = isinstance(total, int) and total > tokens_budget
    line = (
        f"[budget] turns={turns}/{TURNS_BUDGET} "
        f"tokens={total}/{tokens_budget} "
        f"({'OVER BUDGET' if over else 'within budget'})"
    )
    return turns, total, over, line


def answer_question(
    question: str,
    user_id: str,
    config: Config | None = None,
    use_memory: bool = True,
    domains: Sequence[str] = _DOMAINS,
    on_event: EventSink | None = None,
) -> AnswerResult:
    """Answer a user's question using the agent.

    Args:
        question: The user's question.
        user_id: User identifier (used for memory scoping).
        config: Configuration object (default: loaded from environment).
        use_memory: Enable user-scoped memory persistence (default: True).
        domains: Semantic domains to include (default: both ecommerce and energy).
        on_event: Optional sink for trace events (see agent.trace); enables show_chart.

    Returns:
        AnswerResult with text, metrics, and pre-formatted budget line.
        Raises: Any exception from Bedrock, Wren, or missing config is propagated.
    """
    if config is None:
        config = Config.from_env()

    result = None
    recorder = None
    try:
        with _wren_mcp_tools() as tools:
            if on_event is not None:
                recorder = TraceRecorder(on_event, _sql_compiler(tools))
                recorder.emit("question", question=question, domains=list(domains))
            agent = _build_agent(
                list(tools),
                config=config,
                use_memory=use_memory,
                domains=domains,
                recorder=recorder,
            )
            result = agent(
                question,
                limits={
                    "turns": config.agent.turns_budget,
                    "total_tokens": config.agent.tokens_budget,
                },
            )
        text = _extract_answer_text(result)
        turns, tokens, over, metrics = _format_metrics(
            result, config.agent.tokens_budget
        )
        if recorder is not None:
            recorder.emit(
                "answer",
                text=text,
                turns=turns,
                tokens=tokens,
                over_budget=over,
                path=recorder.path(),
            )
        return AnswerResult(
            text=text,
            turns=turns,
            total_tokens=tokens,
            over_budget=over,
            metrics_line=metrics,
        )
    except Exception as e:
        if recorder is not None:
            recorder.emit("error", message=str(e))
        raise
    finally:
        # Ensure metrics print even on exception
        if result is not None:
            _, _, _, metrics = _format_metrics(result, config.agent.tokens_budget)
            import sys
            print(metrics, file=sys.stderr)


# Public for backward compatibility (ask.py, evaluations)
def wren_mcp_tools():
    return _wren_mcp_tools()


def build_agent(tools, use_memory=False, domains=_DOMAINS, config: Config | None = None, recorder=None):
    if config is None:
        config = Config.from_env()
    return _build_agent(tools, config, use_memory, domains, recorder)


def answer_text(result):
    return _extract_answer_text(result)


def budget_line(result, tokens_budget: int = 150_000):
    _, _, _, line = _format_metrics(result, tokens_budget)
    return line


def prompt_version(domains=_DOMAINS):
    return _prompt_version(domains)
