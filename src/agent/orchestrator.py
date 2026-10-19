"""The analytics agent: a Strands agent over the Wren MCP server (Bedrock).

Entry point: answer_question(question, user_id, ...) -> AnswerResult.
One fresh agent per question: no cross-question memory unless use_memory=True.
The agent is physically read-only: the MCP server runs without --allow-write.

Passing on_event (the demo UI) streams trace events and adds the show_chart
tool; without it the agent is exactly the CLI/eval agent.

Budgets: enforced per invocation (the turns cap stops runaways; total tokens
are reported). An OVER BUDGET flag means recalibrate the cap or the prompt,
with transcript evidence; never silently raise it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import boto3
from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.models import BedrockModel, CacheConfig
from strands.tools.mcp import MCPClient

from agent.cube_query import CubeQueryTool
from agent.memory import MemoryTools
from agent.trace import ChartTools, EventSink, TraceRecorder
from config import AgentConfig, Config
from memory import build_memory_store
from repo_config import REPO_ROOT, WREN_DIR

DOMAINS = ("ecommerce", "energy")
_PROMPTS_DIR = REPO_ROOT / "prompts"

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
- show_chart is REQUIRED whenever a data step returned the rows that answer the question: call it exactly once, before your final answer, naming columns of that step's rows (x, y, optional series). Rows come from the tool result — never type numbers into it. If the answer needs arithmetic over rows (a share, a total, a ranking), do it in the query and chart that step; never compute figures yourself from returned rows. The only times to skip it: you refused, or no query returned rows.
- Kinds: line for days, bar to compare regions or fuels, stacked_bar for fuel mix, table otherwise. Set map_key to the network_region or facility_code column whenever rows are per region or per facility, so the map recolours; to chart facilities on the map, include facility_code in the SELECT.
"""


@dataclass
class AnswerResult:
    text: str
    turns: int
    total_tokens: int | str
    over_budget: bool
    metrics_line: str


def load_prompt(domains: Sequence[str] = DOMAINS) -> str:
    """Assemble base + domain blocks. Unknown domains fail loud."""
    parts = [(_PROMPTS_DIR / "base.md").read_text().strip()]
    for domain in domains:
        path = _PROMPTS_DIR / f"{domain}.md"
        if not path.is_file():
            raise ValueError(f"unknown prompt domain: {domain!r}")
        parts.append(path.read_text().strip())
    return "\n\n".join(parts)


def prompt_version(domains: Sequence[str] = DOMAINS) -> str:
    """Short hash of the assembled prompt, recorded in eval reports."""
    return hashlib.sha256(load_prompt(domains).encode()).hexdigest()[:12]


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


def _sql_compiler(tools: Sequence[Any]):
    """Compiled target-dialect SQL for a data step, via the read-only MCP tools."""
    by_name = {t.tool_name: t for t in tools}
    cubes = CubeQueryTool(tools)

    def compile_sql(name: str, tool_input: dict) -> str | None:
        if name == "query_cube":
            return cubes.sql(**tool_input)
        if "sql" not in tool_input or "dry_plan" not in by_name:
            return None
        result = by_name["dry_plan"].mcp_client.call_tool_sync(
            tool_use_id="trace-compile", name="dry_plan", arguments={"sql": tool_input["sql"]}
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


def _build_agent(
    tools: Sequence[Any],
    config: Config,
    use_memory: bool,
    domains: Sequence[str],
    recorder: TraceRecorder | None,
) -> Agent:
    """Assemble the Bedrock-backed agent. Call inside wren_mcp_tools()."""
    session = boto3.Session(profile_name=config.aws.profile, region_name=config.aws.region)
    model = BedrockModel(
        model_id=config.aws.bedrock_model_id,
        temperature=0,
        max_tokens=4096,
        cache_config=CacheConfig(strategy="auto"),
        boto_session=session,
    )
    # Wren's query_cube emits untyped time literals Trino rejects; ours types them.
    all_tools = [t for t in tools if t.tool_name != "query_cube"]
    all_tools += CubeQueryTool(tools).as_list()
    prompt = load_prompt(domains)
    if use_memory:
        all_tools += MemoryTools(build_memory_store(config)).as_list()
        prompt += _MEMORY_PROMPT
    if recorder is not None:
        all_tools += ChartTools(recorder).as_list()
        prompt += _CHART_PROMPT
    return Agent(
        model=model,
        tools=all_tools,
        system_prompt=prompt,
        hooks=[recorder] if recorder is not None else None,
    )


def _answer_text(result: Any) -> str:
    """The final message text from an AgentResult."""
    message = getattr(result, "message", {})
    content = message.get("content", []) if isinstance(message, dict) else []
    text = "\n".join(
        block.get("text", "") for block in content if isinstance(block, dict) and "text" in block
    ).strip()
    return text or str(result)


def _metrics(result: Any | None, budgets: AgentConfig) -> tuple[int, int | str, bool, str]:
    """(turns, total tokens, over budget, the `[budget] ...` line)."""
    if result is None:
        return 0, "?", False, "[budget] no result (failed before the first turn)"
    total = result.metrics.accumulated_usage.get("totalTokens", "?")
    turns = result.metrics.cycle_count
    over = isinstance(total, int) and total > budgets.tokens_budget
    line = (
        f"[budget] turns={turns}/{budgets.turns_budget} "
        f"tokens={total}/{budgets.tokens_budget} "
        f"({'OVER BUDGET' if over else 'within budget'})"
    )
    return turns, total, over, line


def answer_question(
    question: str,
    user_id: str,
    config: Config | None = None,
    use_memory: bool = True,
    domains: Sequence[str] = DOMAINS,
    on_event: EventSink | None = None,
) -> AnswerResult:
    """Answer one question with a fresh agent.

    Args:
        question: The user's question.
        user_id: User identifier (scopes memory).
        config: Configuration (default: loaded from the environment).
        use_memory: Enable user-scoped memory tools.
        domains: Prompt domains to include (default: ecommerce and energy).
        on_event: Optional sink for trace events (see agent.trace); enables show_chart.

    The `[budget]` line is printed to stderr once per run, even when the run fails.
    Exceptions from Bedrock, Wren or missing config propagate.
    """
    if config is None:
        config = Config.from_env()

    result = None
    recorder = None
    try:
        with wren_mcp_tools() as tools:
            if on_event is not None:
                recorder = TraceRecorder(on_event, _sql_compiler(tools))
                recorder.emit("question", question=question, domains=list(domains))
            agent = _build_agent(list(tools), config, use_memory, domains, recorder)
            result = agent(
                question,
                limits={
                    "turns": config.agent.turns_budget,
                    "total_tokens": config.agent.tokens_budget,
                },
            )
        text = _answer_text(result)
        turns, tokens, over, line = _metrics(result, config.agent)
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
            text=text, turns=turns, total_tokens=tokens, over_budget=over, metrics_line=line
        )
    except Exception as e:
        if recorder is not None:
            recorder.emit("error", message=str(e))
        raise
    finally:
        print(_metrics(result, config.agent)[3], file=sys.stderr)
