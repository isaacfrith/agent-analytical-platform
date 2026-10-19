"""The trace recorder turns hook events into UI events; show_chart only charts captured rows."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.trace import ChartTools, TraceRecorder, parse_rows

ROWS = [{"network_region": "NSW1", "avg_price": 87.5}, {"network_region": "VIC1", "avg_price": 49.0}]


def wren_result(rows=ROWS, truncated=False) -> dict:
    payload = {"columns": list(rows[0]), "rows": rows, "row_count": len(rows), "truncated": truncated}
    return {"status": "success", "toolUseId": "x", "content": [{"text": json.dumps(payload)}]}


def run_tool(rec: TraceRecorder, name: str, tool_input: dict, result, use_id: str = "u1", duration=0.25):
    use = {"toolUseId": use_id, "name": name, "input": tool_input}
    rec._before_tool(SimpleNamespace(tool_use=use))
    rec._after_tool(SimpleNamespace(tool_use=use, result=result, duration=duration))


@pytest.fixture
def rec() -> TraceRecorder:
    return TraceRecorder()


def test_parse_rows_reads_wren_payload():
    assert parse_rows(wren_result()) == {"columns": ["network_region", "avg_price"], "rows": ROWS, "truncated": False}


def test_parse_rows_ignores_errors_and_non_json():
    assert parse_rows({"status": "error", "content": [{"text": "boom"}]}) is None
    assert parse_rows({"status": "success", "content": [{"text": "plain words"}]}) is None


def test_data_step_captures_rows_and_badges_cube(rec: TraceRecorder):
    run_tool(rec, "query_cube", {"cube": "daily_price"}, wren_result())

    start, end = rec.events
    assert start["type"] == "step_start" and start["step"] == 1 and start["tool"] == "query_cube"
    assert end["status"] == "success" and end["path"] == "cube" and end["row_count"] == 2
    assert end["duration_ms"] == 250
    assert rec.datasets[1]["rows"] == ROWS
    assert rec.path() == "cube"


def test_sql_fallback_path_and_compiled_sql():
    rec = TraceRecorder(compile_sql=lambda name, inp: f"COMPILED({inp['sql']})")
    run_tool(rec, "run_sql", {"sql": "SELECT 1"}, wren_result())

    assert rec.events[-1]["sql"] == "COMPILED(SELECT 1)"
    assert rec.path() == "sql"


def test_compiler_failure_never_breaks_the_step():
    def boom(name, inp):
        raise RuntimeError("mcp gone")

    rec = TraceRecorder(compile_sql=boom)
    run_tool(rec, "run_sql", {"sql": "SELECT 1"}, wren_result())

    assert rec.events[-1]["status"] == "success" and "sql" not in rec.events[-1]


def test_no_data_means_refused(rec: TraceRecorder):
    run_tool(rec, "get_context", {"question": "efficiency"}, {"status": "success", "content": [{"text": "{}"}]})
    run_tool(rec, "run_sql", {"sql": "SELECT efficiency"}, Exception("unknown column"), use_id="u2")

    assert rec.events[-1]["status"] == "error" and "unknown column" in rec.events[-1]["error"]
    assert rec.path() == "refused"


def test_sink_receives_events_live():
    seen = []
    rec = TraceRecorder(sink=seen.append)
    rec.emit("question", question="hi")
    assert seen == rec.events and seen[0]["question"] == "hi"


def test_reasoning_only_for_intermediate_assistant_turns(rec: TraceRecorder):
    rec._message_added(SimpleNamespace(message={"role": "assistant", "content": [{"text": "Checking cubes"}, {"toolUse": {}}]}))
    rec._message_added(SimpleNamespace(message={"role": "assistant", "content": [{"text": "Final answer"}]}))
    rec._message_added(SimpleNamespace(message={"role": "user", "content": [{"text": "q"}]}))

    assert [e["text"] for e in rec.events if e["type"] == "reasoning"] == ["Checking cubes"]


def test_usage_reads_agent_metrics(rec: TraceRecorder):
    metrics = SimpleNamespace(cycle_count=2, accumulated_usage={"totalTokens": 1234})
    rec._after_model(SimpleNamespace(agent=SimpleNamespace(event_loop_metrics=metrics)))
    assert rec.events[-1] | {"t": 0} == {"type": "usage", "t": 0, "turns": 2, "tokens": 1234}


def test_show_chart_attaches_captured_rows(rec: TraceRecorder):
    run_tool(rec, "query_cube", {}, wren_result())
    out = ChartTools(rec).show_chart(kind="bar", title="Price", x="network_region", y=["avg_price"], map_key="network_region")

    chart = rec.events[-1]
    assert out.startswith("Chart shown")
    assert chart["type"] == "chart" and chart["rows"] == ROWS and chart["step"] == 1
    assert chart["spec"]["map"] == {"key": "network_region", "metric": "avg_price"}


def test_show_chart_rejects_unknown_columns_with_the_real_ones(rec: TraceRecorder):
    run_tool(rec, "query_cube", {}, wren_result())
    with pytest.raises(ValueError, match=r"\['region'\].*network_region"):
        ChartTools(rec).show_chart(kind="bar", title="P", x="region", y=["avg_price"])

    assert not any(e["type"] == "chart" for e in rec.events)


def test_show_chart_needs_data_first(rec: TraceRecorder):
    with pytest.raises(ValueError, match="run query_cube or run_sql first"):
        ChartTools(rec).show_chart(kind="bar", title="P", x="a", y=["b"])


def test_stacked_bar_needs_series(rec: TraceRecorder):
    run_tool(rec, "query_cube", {}, wren_result())
    with pytest.raises(ValueError, match="needs a series"):
        ChartTools(rec).show_chart(kind="stacked_bar", title="P", x="network_region", y=["avg_price"])


def test_map_key_defaults_to_a_region_column(rec: TraceRecorder):
    run_tool(rec, "run_sql", {"sql": "..."}, wren_result())
    ChartTools(rec).show_chart(kind="bar", title="P", x="network_region", y=["avg_price"])
    assert rec.events[-1]["spec"]["map"] == {"key": "network_region", "metric": "avg_price"}


def test_no_map_without_a_map_column(rec: TraceRecorder):
    run_tool(rec, "run_sql", {"sql": "..."}, wren_result([{"day": "2026-10-01", "mwh": 5.0}]))
    ChartTools(rec).show_chart(kind="line", title="P", x="day", y=["mwh"])
    assert "map" not in rec.events[-1]["spec"]


def test_path_follows_the_charted_step(rec: TraceRecorder):
    run_tool(rec, "query_cube", {}, wren_result(), use_id="u1")
    run_tool(rec, "run_sql", {"sql": "..."}, wren_result(), use_id="u2")
    ChartTools(rec).show_chart(kind="bar", title="P", x="network_region", y=["avg_price"])
    assert rec.path() == "sql"
