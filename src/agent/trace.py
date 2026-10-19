"""Agent trace for the demo UI: tool steps, captured rows, charts.

TraceRecorder is a Strands HookProvider that turns one agent run into a
flat stream of JSON-safe events, pushed to a sink as they happen (live
SSE) and kept in order (replay files). Rows from run_sql / query_cube
results are captured verbatim, so a chart can only point at numbers the
semantic layer returned — never at numbers in LLM text.

ChartTools exposes show_chart: the agent names columns of a captured step,
the recorder attaches that step's rows. A bad spec raises, so it comes back
as a tool error listing the real columns and the agent can correct itself.

Event types (every event also carries "t", seconds since the recorder started):
  step_start  {step, tool, input}
  step_end    {step, tool, status, path, duration_ms,
               row_count?, columns?, truncated?, sql?, error?}
  reasoning   {text}                      model text between tool calls
  usage       {turns, tokens}             after every model call
  chart       {step, spec, columns, rows}
answer_question adds: question {question, domains}, answer {...}, error {message}.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from strands import tool
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

EventSink = Callable[[dict], None]
# (tool name, tool input) -> target-dialect SQL, or None when unavailable
SqlCompiler = Callable[[str, dict], "str | None"]

DATA_TOOLS = ("query_cube", "run_sql")
MAX_CAPTURED_ROWS = 5000
CHART_KINDS = ("line", "bar", "stacked_bar", "table")
MAP_KEYS = ("facility_code", "network_region")

_STEP_PATHS = {
    "query_cube": "cube",
    "run_sql": "sql",
    "dry_run": "sql",
    "show_chart": "chart",
}


def step_path(tool_name: str) -> str:
    """Badge for one step: cube | sql | chart | context."""
    return _STEP_PATHS.get(tool_name, "context")


def parse_rows(result: Any) -> dict | None:
    """Pull {columns, rows, truncated} out of a Wren data-tool result."""
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            rows = payload["rows"][:MAX_CAPTURED_ROWS]
            columns = payload.get("columns") or (list(rows[0]) if rows else [])
            return {
                "columns": columns,
                "rows": rows,
                "truncated": bool(payload.get("truncated"))
                or len(payload["rows"]) > MAX_CAPTURED_ROWS,
            }
    return None


def _result_error(result: Any) -> str | None:
    if isinstance(result, Exception):
        return str(result)
    if isinstance(result, dict) and result.get("status") == "error":
        texts = [b.get("text", "") for b in result.get("content") or [] if isinstance(b, dict)]
        return " ".join(t for t in texts if t)[:2000] or "tool error"
    return None


class TraceRecorder(HookProvider):
    """Records one agent run as events. Fresh instance per question."""

    def __init__(self, sink: EventSink | None = None, compile_sql: SqlCompiler | None = None):
        self._sink = sink
        self._compile_sql = compile_sql
        self._t0 = time.monotonic()
        self._steps: dict[str, int] = {}  # toolUseId -> step number
        self.events: list[dict] = []
        self.datasets: dict[int, dict] = {}  # step -> {columns, rows, truncated}

    def emit(self, type_: str, **fields: Any) -> dict:
        event = {"type": type_, "t": round(time.monotonic() - self._t0, 3), **fields}
        self.events.append(event)
        if self._sink is not None:
            self._sink(event)
        return event

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)
        registry.add_callback(MessageAddedEvent, self._message_added)
        registry.add_callback(AfterModelCallEvent, self._after_model)

    def path(self) -> str:
        """Overall route: the charted step's tool if any; else cube if a cube
        returned rows, else sql, else refused."""
        ends = [
            e
            for e in self.events
            if e["type"] == "step_end" and e["status"] == "success" and "row_count" in e
        ]
        charts = [e for e in self.events if e["type"] == "chart"]
        if charts:
            charted = next((e for e in ends if e["step"] == charts[-1]["step"]), None)
            if charted is not None:
                return step_path(charted["tool"])
        tools = {e["tool"] for e in ends}
        if "query_cube" in tools:
            return "cube"
        if "run_sql" in tools:
            return "sql"
        return "refused"

    def latest_dataset_step(self) -> int | None:
        return max(self.datasets) if self.datasets else None

    # -- hook callbacks ---------------------------------------------------

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        use = event.tool_use
        step = len(self._steps) + 1
        self._steps[use["toolUseId"]] = step
        self.emit("step_start", step=step, tool=use["name"], input=use.get("input") or {})

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        use = event.tool_use
        name = use["name"]
        step = self._steps.get(use["toolUseId"], len(self._steps))
        error = _result_error(event.result)
        fields: dict[str, Any] = {
            "step": step,
            "tool": name,
            "status": "error" if error else "success",
            "path": step_path(name),
            "duration_ms": round((event.duration or 0) * 1000),
        }
        if error:
            fields["error"] = error
        elif name in DATA_TOOLS:
            data = parse_rows(event.result)
            if data is not None:
                self.datasets[step] = data
                fields.update(
                    row_count=len(data["rows"]),
                    columns=data["columns"],
                    truncated=data["truncated"],
                )
        if name in ("query_cube", "run_sql", "dry_run") and self._compile_sql is not None:
            try:
                sql = self._compile_sql(name, use.get("input") or {})
            except Exception:  # noqa: BLE001 — compiled SQL is decoration; never fail the run
                sql = None
            if sql:
                fields["sql"] = sql
        self.emit("step_end", **fields)

    def _message_added(self, event: MessageAddedEvent) -> None:
        message = event.message
        if message.get("role") != "assistant":
            return
        content = message.get("content") or []
        # Only intermediate turns: the final answer arrives as the answer event.
        if not any("toolUse" in block for block in content):
            return
        text = "\n".join(b["text"] for b in content if b.get("text")).strip()
        if text:
            self.emit("reasoning", text=text)

    def _after_model(self, event: AfterModelCallEvent) -> None:
        metrics = event.agent.event_loop_metrics
        self.emit(
            "usage",
            turns=metrics.cycle_count,
            tokens=metrics.accumulated_usage.get("totalTokens", 0),
        )


class ChartTools:
    """The show_chart tool, bound to one run's TraceRecorder."""

    def __init__(self, recorder: TraceRecorder):
        self.recorder = recorder

    def as_list(self) -> list:
        return [self.show_chart]

    @tool
    def show_chart(
        self,
        kind: str,
        title: str,
        x: str,
        y: list[str],
        series: str | None = None,
        map_key: str | None = None,
        map_metric: str | None = None,
        step: int | None = None,
    ) -> str:
        """Show the user a chart of rows a data tool (query_cube / run_sql) already returned.

        Call once, after the data step that answers the question. You name
        columns; the rows come from that step's result — never type numbers.

        Args:
            kind: line (over time) | bar (compare categories) | stacked_bar
                (parts of a whole per category; needs series) | table.
            title: Short chart title with units, e.g. "Avg spot price by region (AUD/MWh)".
            x: Column for the x axis (day, network_region, fueltech, ...).
            y: Measure column(s) to plot.
            series: Optional column that splits y into coloured series.
            map_key: Optional column holding NEM regions (network_region) or
                facility_code — the map recolours by it.
            map_metric: Measure column the map colours by (defaults to y[0]).
            step: Step number of the data tool to chart (defaults to the latest).
        """
        rec = self.recorder
        target = step if step is not None else rec.latest_dataset_step()
        if target is None or target not in rec.datasets:
            raise ValueError("No captured rows to chart yet: run query_cube or run_sql first.")
        data = rec.datasets[target]
        columns = data["columns"]
        if kind not in CHART_KINDS:
            raise ValueError(f"Unknown kind {kind!r}. Use one of: {', '.join(CHART_KINDS)}.")
        refs = [x, *y, series, map_key, map_metric]
        missing = [c for c in refs if c is not None and c not in columns]
        if missing:
            raise ValueError(
                f"Unknown column(s) {missing} for step {target}. Available: {columns}."
            )
        if kind == "stacked_bar" and series is None:
            raise ValueError("stacked_bar needs a series column.")
        spec = {"kind": kind, "title": title, "x": x, "y": list(y), "series": series}
        if map_key is None:
            # The model often forgets; map-able rows are recognisable by column name.
            map_key = next((c for c in MAP_KEYS if c in columns), None)
        if map_key is not None:
            spec["map"] = {"key": map_key, "metric": map_metric or y[0]}
        rec.emit("chart", step=target, spec=spec, columns=columns, rows=data["rows"])
        return f"Chart shown: {title} ({len(data['rows'])} rows from step {target})."
