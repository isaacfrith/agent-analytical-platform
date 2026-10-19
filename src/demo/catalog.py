"""The demo UI's "What's in the data" catalog, built from the compiled MDL.

Pure functions, no I/O: the recorder (scripts/demo_record.py) loads the
compiled MDL, the curated topics file, the rules and seed queries, runs the
stats SQL, and passes everything here. The result is ui/data/model.json.

Lineage is derived, not curated: a cube's measures and dimensions are parsed
for the columns they read; when the cube sits on a view, the view's SELECT
maps those back to model columns (plus its join keys). The topics file only
says which cubes/models a topic uses, and validate_topics fails on any name
the model doesn't have, so the tab can't drift from the semantic layer.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

SCHEMA = "energy"
LOW_CARDINALITY = 30  # categorical columns with at most this many values list them


def node_id(kind: str, name: str) -> str:
    return f"{kind}:{name}"


def expr_columns(expression: str) -> set[str]:
    """Bare column names an MDL expression reads (e.g. SUM(CASE WHEN a THEN b ...))."""
    return {c.name for c in sqlglot.parse_one(expression).find_all(exp.Column)}


def energy_subset(mdl: dict) -> dict:
    """Models in the energy schema, plus the views, cubes and joins over them only."""
    models = [
        m for m in mdl.get("models") or [] if m.get("tableReference", {}).get("schema") == SCHEMA
    ]
    names = {m["name"] for m in models}
    views = [v for v in mdl.get("views") or [] if _view_sources(v) and _view_sources(v) <= names]
    objects = names | {v["name"] for v in views}
    cubes = [c for c in mdl.get("cubes") or [] if c.get("baseObject") in objects]
    relationships = [r for r in mdl.get("relationships") or [] if set(r["models"]) <= names]
    return {"models": models, "views": views, "cubes": cubes, "relationships": relationships}


def _view_sources(view: dict) -> set[str]:
    return {t.name for t in sqlglot.parse_one(view["statement"]).find_all(exp.Table)}


def view_lineage(view: dict) -> tuple[dict[str, tuple[str, str]], set[tuple[str, str]]]:
    """({output column: (model, column)}, {join-key (model, column)}) for a view."""
    tree = sqlglot.parse_one(view["statement"])
    alias_to_table = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    outputs: dict[str, tuple[str, str]] = {}
    for projection in tree.expressions:
        cols = list(projection.find_all(exp.Column))
        if len(cols) == 1 and cols[0].table in alias_to_table:
            outputs[projection.alias_or_name] = (alias_to_table[cols[0].table], cols[0].name)
    keys: set[tuple[str, str]] = set()
    for join in tree.find_all(exp.Join):
        for col in join.find_all(exp.Column):
            if col.table in alias_to_table:
                keys.add((alias_to_table[col.table], col.name))
    return outputs, keys


def relationship_keys(rel: dict) -> list[tuple[str, str, str, str]]:
    """[(left model, left column, right model, right column)] from a join condition."""
    pairs = []
    for eq in sqlglot.parse_one(rel["condition"]).find_all(exp.EQ):
        left, right = eq.this, eq.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            pairs.append((left.table, left.name, right.table, right.name))
    return pairs


def _model_columns(model: dict) -> list[dict]:
    """Physical columns only (relationship handles are joins, drawn as edges)."""
    return [c for c in model.get("columns") or [] if not c.get("relationship")]


def validate_topics(topics: dict, subset: dict, seeds: dict[str, dict]) -> None:
    """Raise ValueError listing every name the topics file uses that the model lacks."""
    cubes = {c["name"]: c for c in subset["cubes"]}
    models = {m["name"]: {c["name"] for c in _model_columns(m)} for m in subset["models"]}
    problems = []
    for topic in topics.get("topics") or []:
        where = f"topic {topic.get('id')!r}"
        for use in topic.get("uses") or []:
            if "cube" in use:
                cube = cubes.get(use["cube"])
                if cube is None:
                    problems.append(f"{where}: no energy cube {use['cube']!r}")
                    continue
                known = {m["name"] for m in cube["measures"]}
                problems += [
                    f"{where}: cube {use['cube']} has no measure {m!r}"
                    for m in use.get("measures") or []
                    if m not in known
                ]
            elif "model" in use:
                cols = models.get(use["model"])
                if cols is None:
                    problems.append(f"{where}: no energy model {use['model']!r}")
                    continue
                problems += [
                    f"{where}: model {use['model']} has no column {c!r}"
                    for c in use.get("columns") or []
                    if c not in cols
                ]
            else:
                problems.append(f"{where}: a use needs 'cube' or 'model'")
    if problems:
        raise ValueError(
            "Topics file doesn't match the semantic model:\n  " + "\n  ".join(problems)
        )


EXPECTATIONS = ("answer", "refused")
LIMITS_TOPIC = "limits"  # questions the data can't answer; not a topic in the diagram


def validate_questions(questions: list[dict], topic_ids: set[str], seeds: dict[str, dict]) -> None:
    """Raise ValueError listing every problem in the suggested-questions file."""
    problems = []
    seen: set[str] = set()
    for q in questions:
        qid = q.get("id")
        where = f"question {qid!r}"
        if not qid or not q.get("question"):
            problems.append(f"{where}: needs an id and a question")
        if qid in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(qid)
        if q.get("topic") not in topic_ids | {LIMITS_TOPIC}:
            problems.append(f"{where}: unknown topic {q.get('topic')!r}")
        if q.get("expect", "answer") not in EXPECTATIONS:
            problems.append(f"{where}: expect must be one of {EXPECTATIONS}")
        if q.get("seed") and q["seed"] not in seeds:
            problems.append(f"{where}: no seed query {q['seed']!r} in wren/knowledge/sql/")
    if problems:
        raise ValueError("Questions file has problems:\n  " + "\n  ".join(problems))


def topic_path(topic: dict, subset: dict) -> dict:
    """Nodes, edges and columns ({node id: [column]}) a topic touches."""
    cubes = {c["name"]: c for c in subset["cubes"]}
    views = {v["name"]: v for v in subset["views"]}
    tid = node_id("topic", topic["id"])
    nodes, edges = {tid}, set()
    columns: dict[str, set[str]] = {}

    def add_cols(nid: str, cols: set[str]) -> None:
        columns.setdefault(nid, set()).update(cols)

    for use in topic.get("uses") or []:
        if "model" in use:
            mid = node_id("model", use["model"])
            nodes.add(mid)
            edges.add((tid, mid))
            add_cols(mid, set(use.get("columns") or []))
            continue
        cube = cubes[use["cube"]]
        cid = node_id("cube", cube["name"])
        nodes.add(cid)
        edges.add((tid, cid))
        wanted = set(use.get("measures") or [m["name"] for m in cube["measures"]])
        members = [m for m in cube["measures"] if m["name"] in wanted]
        members += list(cube.get("dimensions") or []) + list(cube.get("timeDimensions") or [])
        add_cols(cid, {m["name"] for m in members})
        read = set().union(*(expr_columns(m["expression"]) for m in members))
        base = cube["baseObject"]
        if base in views:
            vid = node_id("view", base)
            nodes.add(vid)
            edges.add((cid, vid))
            add_cols(vid, read)
            outputs, keys = view_lineage(views[base])
            for col in read:
                if col in outputs:
                    model, mcol = outputs[col]
                    mid = node_id("model", model)
                    nodes.add(mid)
                    edges.add((vid, mid))
                    add_cols(mid, {mcol})
            for model, mcol in keys:
                mid = node_id("model", model)
                nodes.add(mid)
                edges.add((vid, mid))
                add_cols(mid, {mcol})
        else:
            mid = node_id("model", base)
            nodes.add(mid)
            edges.add((cid, mid))
            add_cols(mid, read)
    return {
        "nodes": sorted(nodes),
        "edges": sorted([a, b] for a, b in edges),
        "columns": {k: sorted(v) for k, v in sorted(columns.items())},
    }


def build_catalog(
    mdl: dict,
    topics: dict,
    questions: list[dict],
    stats: dict[str, dict],
    rules_md: str,
    seeds: dict[str, dict],
    recorded_at: str,
) -> dict:
    """The JSON the "What's in the data" tab renders.

    Each topic's example questions are the suggested questions filed under it;
    each names its recording (`replay`), which the UI plays if it recorded cleanly.
    """
    subset = energy_subset(mdl)
    validate_topics(topics, subset, seeds)
    validate_questions(questions, {t["id"] for t in topics.get("topics") or []}, seeds)
    model_index = {m["name"]: m for m in subset["models"]}

    def column_entry(model: str, col: dict) -> dict:
        entry = {
            "name": col["name"],
            "type": col["type"],
            "description": (col.get("properties") or {}).get("description", ""),
        }
        entry.update(stats.get(model, {}).get("columns", {}).get(col["name"], {}))
        return entry

    models = [
        {
            "id": node_id("model", m["name"]),
            "name": m["name"],
            "description": (m.get("properties") or {}).get("description", ""),
            "primary_key": m.get("primaryKey"),
            "rows": stats.get(m["name"], {}).get("rows"),
            "columns": [column_entry(m["name"], c) for c in _model_columns(m)],
        }
        for m in subset["models"]
    ]
    views = []
    for v in subset["views"]:
        outputs, _ = view_lineage(v)
        cols = []
        for out, (model, mcol) in outputs.items():
            source = next(
                (c for c in _model_columns(model_index[model]) if c["name"] == mcol),
                {"name": mcol, "type": "?"},
            )
            cols.append({**column_entry(model, source), "name": out, "source": f"{model}.{mcol}"})
        views.append(
            {
                "id": node_id("view", v["name"]),
                "name": v["name"],
                "description": (v.get("properties") or {}).get("description", ""),
                "sources": sorted(_view_sources(v)),
                "columns": cols,
            }
        )
    cubes = [
        {
            "id": node_id("cube", c["name"]),
            "name": c["name"],
            "base": node_id(
                "view" if c["baseObject"] in {v["name"] for v in subset["views"]} else "model",
                c["baseObject"],
            ),
            "description": (c.get("properties") or {}).get("description", ""),
            "measures": [
                {
                    "name": m["name"],
                    "kind": "measure",
                    "type": m["type"],
                    "expression": m["expression"],
                }
                for m in c["measures"]
            ],
            "dimensions": [
                {"name": d["name"], "kind": kind, "type": d["type"], "expression": d["expression"]}
                for kind, group in (
                    ("dimension", c.get("dimensions") or []),
                    ("time", c.get("timeDimensions") or []),
                )
                for d in group
            ],
        }
        for c in subset["cubes"]
    ]
    joins = [
        {
            "name": r["name"],
            "type": r.get("joinType"),
            "condition": r["condition"],
            "keys": [
                {
                    "from": node_id("model", a),
                    "from_col": ac,
                    "to": node_id("model", b),
                    "to_col": bc,
                }
                for a, ac, b, bc in relationship_keys(r)
            ],
        }
        for r in subset["relationships"]
    ]
    out_topics = []
    for t in topics["topics"]:
        examples = []
        for q in questions:
            if q["topic"] != t["id"]:
                continue
            item = {"question": q["question"], "replay": q["id"]}
            if q.get("seed"):
                item["sql"] = seeds[q["seed"]]["sql"].strip()
            examples.append(item)
        out_topics.append(
            {
                "id": node_id("topic", t["id"]),
                "name": t["name"],
                "summary": t["summary"],
                "limits": t.get("limits") or [],
                "examples": examples,
                "path": topic_path(t, subset),
            }
        )
    return {
        "recorded_at": recorded_at,
        "limits": topics.get("limits") or [],
        "rules_md": rules_md,
        "topics": out_topics,
        "cubes": cubes,
        "views": views,
        "models": models,
        "joins": joins,
    }


def stats_sql(model: dict) -> str:
    """One query per model: row count, distinct counts, and min/max for numbers and dates."""
    parts = ["COUNT(*) AS __rows"]
    for c in _model_columns(model):
        name, typ = c["name"], c["type"].upper()
        parts.append(f"COUNT(DISTINCT {name}) AS {name}__distinct")
        if typ in ("DOUBLE", "INTEGER", "BIGINT", "DECIMAL", "REAL", "TIMESTAMP", "DATE"):
            parts += [f"MIN({name}) AS {name}__min", f"MAX({name}) AS {name}__max"]
    return f"SELECT {', '.join(parts)} FROM {model['name']}"


def parse_stats(model: dict, row: dict) -> dict:
    """{rows, columns: {col: {distinct, min?, max?}}} from a stats_sql result row."""
    columns: dict[str, dict[str, Any]] = {}
    for c in _model_columns(model):
        entry: dict[str, Any] = {"distinct": row.get(f"{c['name']}__distinct")}
        for bound in ("min", "max"):
            key = f"{c['name']}__{bound}"
            if row.get(key) is not None:
                value = row[key]
                entry[bound] = (
                    value[:10]
                    if isinstance(value, str) and c["type"].upper() in ("TIMESTAMP", "DATE")
                    else value
                )
        columns[c["name"]] = entry
    return {"rows": row.get("__rows"), "columns": columns}


def low_cardinality_columns(model: dict, stats: dict) -> list[str]:
    """Text/boolean columns worth listing every value of."""
    return [
        c["name"]
        for c in _model_columns(model)
        if c["type"].upper() in ("VARCHAR", "BOOLEAN")
        and (stats["columns"].get(c["name"], {}).get("distinct") or 0) <= LOW_CARDINALITY
    ]
