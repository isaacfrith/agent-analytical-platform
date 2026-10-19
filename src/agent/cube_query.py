"""query_cube that works on Trino: Wren's cube SQL with typed time literals.

Wren (0.15/0.16) compiles cube time filters to untyped string literals —
`WHERE day >= '2026-09-19'` — and passes them through to Trino, which
refuses to compare a timestamp with a varchar (TYPE_MISMATCH). No MDL
setting avoids it: the declared type is ignored, a DATE expression still
mismatches, and a VARCHAR expression breaks DATE_TRUNC.

So this tool replaces Wren's MCP query_cube with the same interface: Wren
still compiles the cube (sql_only), this module casts each date literal
compared against a cube time column to TIMESTAMP, and the SQL runs through
Wren's run_sql, so the semantic layer still expands it. Typed literals plan
cleanly to CAST(... AS TIMESTAMP(0)) for Trino.

It also resolves `today` and `-Nd` (N days before today) in date positions,
because the model does not know the date and otherwise invents syntax.
Drop this module once Wren types the literals itself.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

import sqlglot
from sqlglot import exp
from strands import tool

_DATE_LITERAL = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_RELATIVE = re.compile(r"^-(\d+)d$")
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def resolve_date(token: str, today: date) -> str:
    """'today' and '-Nd' become ISO dates; anything else passes through."""
    token = token.strip()
    if token.lower() == "today":
        return today.isoformat()
    if m := _RELATIVE.match(token):
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return token


def resolve_time_dimension(spec: str | None, today: date) -> str | None:
    """name:granularity[:start,end] with relative dates resolved."""
    if not spec:
        return spec
    parts = spec.split(":", 2)
    if len(parts) == 3:
        parts[2] = ",".join(resolve_date(d, today) for d in parts[2].split(","))
    return ":".join(parts)


def resolve_filter(spec: str, time_dims: set[str], today: date) -> str:
    """dim:op:value — relative dates resolved only for time dimensions."""
    parts = spec.split(":", 2)
    if len(parts) == 3 and parts[0] in time_dims:
        parts[2] = ",".join(resolve_date(v, today) for v in parts[2].split(","))
    return ":".join(parts)


def _typed(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Literal) and node.is_string and _DATE_LITERAL.match(node.this):
        return exp.Cast(this=node.copy(), to=exp.DataType.build("TIMESTAMP"))
    return node


def _is_time_column(node: exp.Expression, time_columns: set[str]) -> bool:
    return isinstance(node, exp.Column) and not node.table and node.name in time_columns


def type_time_literals(sql: str, time_columns: set[str]) -> str:
    """Cast string date literals compared against a time column to TIMESTAMP."""
    tree = sqlglot.parse_one(sql)
    for cmp in tree.find_all(*_COMPARISONS):
        left, right = cmp.this, cmp.expression
        if _is_time_column(left, time_columns):
            cmp.set("expression", _typed(right))
        elif _is_time_column(right, time_columns):
            cmp.set("this", _typed(left))
    for node in tree.find_all(exp.In, exp.Between):
        if not _is_time_column(node.this, time_columns):
            continue
        if isinstance(node, exp.In):
            node.set("expressions", [_typed(e) for e in node.expressions])
        else:
            node.set("low", _typed(node.args["low"]))
            node.set("high", _typed(node.args["high"]))
    return tree.sql()


def _result_text(result: dict) -> str:
    return "\n".join(b.get("text", "") for b in result.get("content") or []).strip()


class CubeQueryTool:
    """The agent's query_cube, over the Wren MCP tools of one session."""

    def __init__(self, mcp_tools: Sequence[Any], today: Callable[[], date] = date.today):
        by_name = {t.tool_name: t for t in mcp_tools}
        self._client = by_name["query_cube"].mcp_client
        self._today = today
        self._time: tuple[set[str], set[str]] | None = None

    def _time_names(self) -> tuple[set[str], set[str]]:
        """(time dimension names, time column expressions) across all cubes.

        Loaded on first use, not at agent build: a failure here is then one
        failed tool call the agent can recover from, not a crashed question.
        """
        if self._time is None:
            mdl = json.loads(_result_text(self._call("get_mdl", {})))
            dims: set[str] = set()
            columns: set[str] = set()
            for cube in mdl.get("cubes") or []:
                for td in cube.get("timeDimensions") or []:
                    dims.add(td["name"])
                    expression = td.get("expression") or td["name"]
                    if re.fullmatch(r"\w+", expression):
                        columns.add(expression)
            self._time = (dims, columns)
        return self._time

    def _call(self, name: str, arguments: dict) -> dict:
        result = self._client.call_tool_sync(tool_use_id=f"cube-{name}", name=name, arguments=arguments)
        if result.get("status") != "success":
            raise ValueError(_result_text(result) or f"{name} failed")
        return result

    def sql(
        self,
        cube: str,
        measures: list[str],
        dimensions: list[str] | None = None,
        time_dimension: str | None = None,
        filters: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> str:
        """Wren's cube SQL, with relative dates resolved and time literals typed."""
        today = self._today()
        time_dims, time_columns = self._time_names()
        args = {
            "cube": cube,
            "measures": measures,
            "dimensions": dimensions,
            "time_dimension": resolve_time_dimension(time_dimension, today),
            "filters": [resolve_filter(f, time_dims, today) for f in filters or []],
            "order_by": order_by,
            "limit": limit,
            "offset": offset,
            "sql_only": True,
        }
        payload = json.loads(_result_text(self._call("query_cube", {k: v for k, v in args.items() if v is not None})))
        return type_time_literals(payload["sql"], time_columns)

    def as_list(self) -> list:
        return [self.query_cube]

    @tool
    def query_cube(
        self,
        cube: str,
        measures: list[str],
        dimensions: list[str] | None = None,
        time_dimension: str | None = None,
        filters: list[str] | None = None,
        order_by: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> str:
        """Run a structured cube (metric) query and return aggregated rows.

        Args:
            cube: Cube name (list_cubes / describe_cube show what exists).
            measures: Measure names to compute.
            dimensions: Dimension names to group by.
            time_dimension: "name:granularity" or "name:granularity:start,end",
                e.g. "day:day:-30d,today". Granularity: year|quarter|month|week|day.
                Dates are YYYY-MM-DD, "today", or "-Nd" (N days before today).
                The range END IS EXCLUSIVE: "-30d,today" is the last 30 complete days.
            filters: "dimension:operator:value" — operators eq|neq|in|not_in|gt|gte|lt|lte|
                contains|starts_with|is_null|is_not_null; comma-separate values for in/not_in.
                Examples: "network_region:eq:NSW1", "network:eq:NEM", "day:gte:-7d".
            order_by: "member:asc|desc"; the member must be selected. Pair with limit for top-N.
            limit: Maximum rows to return.
            offset: Rows to skip.
        """
        sql = self.sql(cube, measures, dimensions, time_dimension, filters, order_by, limit, offset)
        args: dict[str, Any] = {"sql": sql}
        if limit is not None:
            args["limit"] = limit
        return _result_text(self._call("run_sql", args))
