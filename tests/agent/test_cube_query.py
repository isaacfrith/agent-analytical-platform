"""query_cube on Trino: relative dates resolve; date literals against time columns get typed."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from agent.cube_query import (
    CubeQueryTool,
    resolve_date,
    resolve_filter,
    resolve_time_dimension,
    type_time_literals,
)

TODAY = date(2026, 10, 19)


def test_resolve_date_tokens():
    assert resolve_date("today", TODAY) == "2026-10-19"
    assert resolve_date("-30d", TODAY) == "2026-09-19"
    assert resolve_date("2026-01-01", TODAY) == "2026-01-01"


def test_resolve_time_dimension_range_only():
    assert resolve_time_dimension("day:day:-30d,today", TODAY) == "day:day:2026-09-19,2026-10-19"
    assert resolve_time_dimension("day:day", TODAY) == "day:day"
    assert resolve_time_dimension(None, TODAY) is None


def test_resolve_filter_only_touches_time_dimensions():
    assert resolve_filter("day:gte:-7d", {"day"}, TODAY) == "day:gte:2026-10-12"
    assert resolve_filter("network_region:eq:-7d", {"day"}, TODAY) == "network_region:eq:-7d"


def test_type_time_literals_casts_comparisons_on_time_columns():
    sql = "SELECT a FROM t WHERE day >= '2026-09-19' AND day < '2026-10-19' AND network_region = 'NSW1'"
    out = type_time_literals(sql, {"day"})
    assert "day >= CAST('2026-09-19' AS TIMESTAMP)" in out
    assert "day < CAST('2026-10-19' AS TIMESTAMP)" in out
    assert "network_region = 'NSW1'" in out


def test_type_time_literals_handles_reversed_in_and_between():
    out = type_time_literals(
        "SELECT a FROM t WHERE '2026-09-19' <= day AND day IN ('2026-10-01') AND day BETWEEN '2026-10-01' AND '2026-10-02'",
        {"day"},
    )
    assert "CAST('2026-09-19' AS TIMESTAMP) <= day" in out
    assert "IN (CAST('2026-10-01' AS TIMESTAMP))" in out
    assert "BETWEEN CAST('2026-10-01' AS TIMESTAMP) AND CAST('2026-10-02' AS TIMESTAMP)" in out


def test_type_time_literals_leaves_non_dates_and_other_columns():
    sql = "SELECT a FROM t WHERE day >= CURRENT_DATE - INTERVAL '30' DAY AND label = '2026-09-19'"
    out = type_time_literals(sql, {"day"})
    assert "CAST(" not in out


class FakeClient:
    def __init__(self, cube_sql: str):
        self.cube_sql = cube_sql
        self.calls: list[tuple[str, dict]] = []

    def call_tool_sync(self, tool_use_id, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_mdl":
            mdl = {
                "cubes": [
                    {
                        "name": "renewable_share",
                        "timeDimensions": [
                            {"name": "day", "expression": "day", "type": "TIMESTAMP"}
                        ],
                    }
                ]
            }
            return {"status": "success", "content": [{"text": json.dumps(mdl)}]}
        if name == "query_cube":
            return {"status": "success", "content": [{"text": json.dumps({"sql": self.cube_sql})}]}
        if name == "run_sql":
            return {
                "status": "success",
                "content": [
                    {
                        "text": '{"columns": ["n"], "rows": [{"n": 1}], "row_count": 1, "truncated": false}'
                    }
                ],
            }
        raise AssertionError(name)


def make_tool(client: FakeClient) -> CubeQueryTool:
    tools = [
        SimpleNamespace(tool_name=n, mcp_client=client)
        for n in ("query_cube", "run_sql", "get_mdl")
    ]
    return CubeQueryTool(tools, today=lambda: TODAY)


def test_tool_compiles_with_wren_types_literals_and_runs_through_run_sql():
    client = FakeClient(
        "SELECT SUM(x) AS v FROM generation_with_group WHERE day >= '2026-09-19' AND day < '2026-10-19'"
    )
    out = make_tool(client).query_cube(
        cube="renewable_share",
        measures=["v"],
        time_dimension="day:day:-30d,today",
        filters=["network_region:eq:NSW1"],
        limit=50,
    )

    _, (_, cube_args), (_, sql_args) = client.calls
    assert cube_args["time_dimension"] == "day:day:2026-09-19,2026-10-19"
    assert cube_args["sql_only"] is True
    assert "CAST('2026-09-19' AS TIMESTAMP)" in sql_args["sql"]
    assert sql_args["limit"] == 50
    assert json.loads(out)["rows"] == [{"n": 1}]


def test_tool_surfaces_wren_errors():
    client = FakeClient("SELECT 1")
    tool = make_tool(client)
    client.call_tool_sync = lambda tool_use_id, name, arguments: {
        "status": "error",
        "content": [{"text": "unknown cube"}],
    }
    with pytest.raises(ValueError, match="unknown cube"):
        tool.query_cube(cube="nope", measures=["v"])


def test_mdl_failure_is_a_tool_error_not_a_build_crash():
    client = FakeClient("SELECT 1")
    client.call_tool_sync = lambda tool_use_id, name, arguments: {
        "status": "error",
        "content": [{"text": "mcp hiccup"}],
    }
    tool = make_tool(client)  # building never touches the MCP server
    with pytest.raises(ValueError, match="mcp hiccup"):
        tool.query_cube(cube="renewable_share", measures=["v"])
