"""The data catalog: energy-only subset, derived lineage, and curated files that can't drift."""

from __future__ import annotations

import pytest

from demo.catalog import (
    build_catalog,
    energy_subset,
    expr_columns,
    low_cardinality_columns,
    parse_stats,
    stats_sql,
    topic_path,
    validate_questions,
    validate_topics,
)


def col(name, typ="VARCHAR", **extra):
    return {"name": name, "type": typ, "properties": {"description": f"{name} desc"}, **extra}


MDL = {
    "models": [
        {
            "name": "gen",
            "tableReference": {"schema": "energy"},
            "columns": [
                col("day", "TIMESTAMP"),
                col("region"),
                col("fuel"),
                col("mwh", "DOUBLE"),
                col("fuel_group", "groups", relationship="gen_groups"),
            ],
        },
        {
            "name": "groups",
            "tableReference": {"schema": "energy"},
            "columns": [col("fuel"), col("is_renewable", "BOOLEAN")],
        },
        {"name": "orders", "tableReference": {"schema": "ecommerce"}, "columns": [col("id")]},
    ],
    "views": [
        {
            "name": "gen_with_group",
            "statement": "SELECT g.day, g.region, m.is_renewable, g.mwh FROM gen AS g JOIN groups AS m ON g.fuel = m.fuel",
        },
        {"name": "order_view", "statement": "SELECT o.id FROM orders AS o"},
    ],
    "cubes": [
        {
            "name": "share",
            "baseObject": "gen_with_group",
            "measures": [
                {
                    "name": "ren",
                    "expression": "SUM(CASE WHEN is_renewable THEN mwh ELSE 0 END)",
                    "type": "DOUBLE",
                },
                {"name": "total", "expression": "SUM(mwh)", "type": "DOUBLE"},
            ],
            "dimensions": [{"name": "region", "expression": "region", "type": "VARCHAR"}],
            "timeDimensions": [{"name": "day", "expression": "day", "type": "TIMESTAMP"}],
        },
        {
            "name": "output",
            "baseObject": "gen",
            "measures": [{"name": "total", "expression": "SUM(mwh)", "type": "DOUBLE"}],
            "dimensions": [{"name": "fuel", "expression": "fuel", "type": "VARCHAR"}],
        },
        {
            "name": "revenue",
            "baseObject": "orders",
            "measures": [{"name": "n", "expression": "COUNT(id)", "type": "BIGINT"}],
        },
    ],
    "relationships": [
        {
            "name": "gen_groups",
            "models": ["gen", "groups"],
            "joinType": "MANY_TO_ONE",
            "condition": "gen.fuel = groups.fuel",
        },
        {"name": "x", "models": ["orders", "gen"], "condition": "orders.id = gen.day"},
    ],
}


def test_expr_columns():
    assert expr_columns("SUM(CASE WHEN is_renewable THEN mwh ELSE 0 END)") == {
        "is_renewable",
        "mwh",
    }


def test_energy_subset_drops_other_domains():
    s = energy_subset(MDL)
    assert [m["name"] for m in s["models"]] == ["gen", "groups"]
    assert [v["name"] for v in s["views"]] == ["gen_with_group"]
    assert [c["name"] for c in s["cubes"]] == ["share", "output"]
    assert [r["name"] for r in s["relationships"]] == ["gen_groups"]


def test_cube_on_a_view_traces_to_model_columns_and_join_keys():
    path = topic_path({"id": "ren", "uses": [{"cube": "share"}]}, energy_subset(MDL))
    assert path["nodes"] == [
        "cube:share",
        "model:gen",
        "model:groups",
        "topic:ren",
        "view:gen_with_group",
    ]
    assert path["columns"]["model:gen"] == ["day", "fuel", "mwh", "region"]
    assert path["columns"]["model:groups"] == ["fuel", "is_renewable"]
    assert ["view:gen_with_group", "model:groups"] in path["edges"]


def test_measure_narrowing_and_direct_model_use():
    s = energy_subset(MDL)
    path = topic_path(
        {
            "id": "t",
            "uses": [
                {"cube": "share", "measures": ["total"]},
                {"model": "groups", "columns": ["is_renewable"]},
            ],
        },
        s,
    )
    assert path["columns"]["cube:share"] == ["day", "region", "total"]
    assert path["columns"]["view:gen_with_group"] == ["day", "mwh", "region"]
    assert ["topic:t", "model:groups"] in path["edges"]


def test_validate_topics_lists_every_unknown_name():
    topics = {
        "topics": [
            {
                "id": "bad",
                "uses": [
                    {"cube": "nope"},
                    {"cube": "share", "measures": ["ghost"]},
                    {"model": "gen", "columns": ["fuel_group", "nah"]},
                ],
            }
        ]
    }
    with pytest.raises(ValueError) as err:
        validate_topics(topics, energy_subset(MDL), seeds={})
    msg = str(err.value)
    for name in ("'nope'", "'ghost'", "'nah'", "'fuel_group'"):
        assert name in msg  # fuel_group is a join handle, not a column


def test_validate_questions_lists_every_problem():
    questions = [
        {"id": "a", "topic": "ren", "question": "Q?"},
        {"id": "a", "topic": "ren", "question": "dup?"},
        {"id": "b", "topic": "nope", "question": "Q?"},
        {"id": "c", "topic": "limits", "question": "Q?", "expect": "maybe"},
        {"id": "d", "topic": "ren", "question": "Q?", "seed": "missing"},
        {"id": "e", "topic": "ren"},
    ]
    with pytest.raises(ValueError) as err:
        validate_questions(questions, {"ren"}, seeds={})
    msg = str(err.value)
    for problem in ("duplicate id", "'nope'", "expect must be", "'missing'", "'e': needs"):
        assert problem in msg


def test_refusal_questions_need_no_topic_of_their_own():
    validate_questions(
        [{"id": "r", "topic": "limits", "question": "Q?", "expect": "refused"}], set(), seeds={}
    )


def test_build_catalog_links_replays_and_seed_sql():
    topics = {
        "limits": ["daily only"],
        "topics": [
            {
                "id": "ren",
                "name": "Renewables",
                "summary": "s",
                "uses": [{"cube": "share"}],
            }
        ],
    }
    questions = [
        {"id": "q1", "topic": "ren", "question": "Q1", "seed": "s1"},
        {"id": "r1", "topic": "limits", "question": "R1", "expect": "refused"},
        {"id": "q2", "topic": "ren", "question": "Q2"},
    ]
    stats = {"gen": {"rows": 10, "columns": {"region": {"distinct": 2, "values": ["A", "B"]}}}}
    out = build_catalog(
        MDL,
        topics,
        questions,
        stats,
        "# rules",
        {"s1": {"sql": "SELECT 1\n"}},
        "2026-10-19T00:00:00Z",
    )
    ex = out["topics"][0]["examples"]
    assert ex == [
        {"question": "Q1", "replay": "q1", "sql": "SELECT 1"},
        {"question": "Q2", "replay": "q2"},
    ]
    gen = next(m for m in out["models"] if m["name"] == "gen")
    assert gen["rows"] == 10 and [c["name"] for c in gen["columns"]] == [
        "day",
        "region",
        "fuel",
        "mwh",
    ]
    assert next(c for c in gen["columns"] if c["name"] == "region")["values"] == ["A", "B"]
    view = out["views"][0]
    assert {"name": "is_renewable", "source": "groups.is_renewable"}.items() <= next(
        c for c in view["columns"] if c["name"] == "is_renewable"
    ).items()
    assert (
        out["cubes"][0]["base"] == "view:gen_with_group" and out["cubes"][1]["base"] == "model:gen"
    )
    assert out["joins"][0]["keys"] == [
        {"from": "model:gen", "from_col": "fuel", "to": "model:groups", "to_col": "fuel"}
    ]


def test_stats_sql_and_parse():
    gen = MDL["models"][0]
    sql = stats_sql(gen)
    assert "MIN(mwh) AS mwh__min" in sql and "MIN(region)" not in sql and "fuel_group" not in sql
    parsed = parse_stats(
        gen,
        {
            "__rows": 5,
            "day__distinct": 3,
            "day__min": "2026-07-21 00:00:00.000000",
            "day__max": "2026-10-18 00:00:00.000000",
            "region__distinct": 2,
            "fuel__distinct": 40,
            "mwh__distinct": 5,
            "mwh__min": -1.5,
            "mwh__max": 9.0,
        },
    )
    assert parsed["rows"] == 5
    assert parsed["columns"]["day"] == {"distinct": 3, "min": "2026-07-21", "max": "2026-10-18"}
    assert low_cardinality_columns(gen, parsed) == ["region"]
