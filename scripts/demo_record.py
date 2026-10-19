"""Record the energy demo UI's replays and map overview (Phase 3).

Runs each suggestion chip through the real agent with trace events on,
and writes one replay file per chip plus an index. Also snapshots, with
plain SQL through the semantic layer and no LLM: the map overview (facility
dots, 30-day renewable share by region) and the data catalog behind the
"What's in the data" tab (compiled MDL + demos/energy-topics.yml + stats). Everything lands in ui/data/, which is
committed, so GitHub Pages serves it and re-recordings show up as diffs.

Costs Bedrock tokens: about 7 questions at 25-50k tokens each (~$1).

Usage: uv run python scripts/demo_record.py [--only chip_id,...] [--overview-only | --catalog-only]
       (or `make demo-record`)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml

from agent.orchestrator import _wren_mcp_tools, answer_question
from agent.trace import parse_rows
from demo.catalog import (
    build_catalog,
    energy_subset,
    low_cardinality_columns,
    parse_stats,
    stats_sql,
)
from repo_config import REPO_ROOT, WREN_DIR, load_env

DATA_DIR = REPO_ROOT / "ui" / "data"
REPLAY_DIR = DATA_DIR / "replays"

# (id, chip label, question). Order is the order the chips appear in the UI.
CHIPS: list[tuple[str, str, str]] = [
    ("renewable_share_nsw", "Renewable share, NSW", "What share of NSW generation was renewable over the last 30 days?"),
    ("price_by_region", "Spot price by region", "What was the average spot price by region over the last 30 days?"),
    ("price_spike_fuel_mix", "Fuel mix on the price spike", "On the highest-price day in the last 30 days, which fuel types generated the most?"),
    ("coal_capacity", "Coal capacity by state", "Show me operating coal capacity by state."),
    ("wa_vs_nem", "WA vs the east coast", "How does WA's renewable share compare with each NEM region over the last 30 days?"),
    ("largest_wind_solar", "Largest wind & solar farms", "Which are the 15 largest operating wind and solar facilities by registered capacity?"),
    ("refuse_efficiency", "Generation by efficiency", "Show me generation by unit efficiency."),
]

OVERVIEW_SQL = {
    # One row per site x fuel group x status: a site can mix techs (solar + battery).
    "facilities": """
        SELECT f.facility_code, f.facility_name, f.network_region, g.fueltech_group,
               f.status, SUM(f.capacity_registered_mw) AS capacity_mw,
               MAX(f.latitude) AS lat, MAX(f.longitude) AS lng
        FROM facilities AS f
        JOIN fueltech_groups AS g ON f.fueltech = g.fueltech
        WHERE f.latitude IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
    """,
    "regions": """
        SELECT network_region,
               SUM(CASE WHEN is_renewable THEN energy_mwh ELSE 0 END) / SUM(energy_mwh) * 100 AS renewable_pct,
               SUM(energy_mwh) AS energy_mwh,
               SUM(emissions_tco2e) AS emissions_tco2e
        FROM generation_with_group
        WHERE day >= CURRENT_DATE - INTERVAL '30' DAY
        GROUP BY 1
    """,
    "window": "SELECT CAST(MIN(day) AS VARCHAR) AS first_day, CAST(MAX(day) AS VARCHAR) AS last_day FROM market_daily",
}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, default=str) + "\n")


def record_overview(recorded_at: str) -> dict:
    out: dict = {"recorded_at": recorded_at}
    with _wren_mcp_tools() as tools:
        run_sql = next(t for t in tools if t.tool_name == "run_sql")
        for key, sql in OVERVIEW_SQL.items():
            result = run_sql.mcp_client.call_tool_sync(
                tool_use_id=f"overview-{key}", name="run_sql", arguments={"sql": sql, "limit": 10000}
            )
            data = parse_rows(result)
            if data is None:
                raise RuntimeError(f"overview query {key!r} failed: {str(result)[:500]}")
            out[key] = data["rows"]
    out["window"] = out["window"][0]
    write_json(DATA_DIR / "overview.json", out)
    print(f"[PASS] overview: {len(out['facilities'])} facility rows, {len(out['regions'])} regions, window {out['window']}")
    return out


TOPICS_FILE = REPO_ROOT / "demos" / "energy-topics.yml"


def load_seeds() -> dict[str, dict]:
    """wren/knowledge/sql/*.md front matter ({nl, sql, ...}) by file stem."""
    seeds = {}
    for path in sorted((WREN_DIR / "knowledge" / "sql").glob("*.md")):
        parts = path.read_text().split("---")
        if len(parts) >= 3:
            seeds[path.stem] = yaml.safe_load(parts[1]) or {}
    return seeds


def record_catalog(recorded_at: str) -> dict:
    """ui/data/model.json: the governed model, its stats, and the curated topics."""
    with _wren_mcp_tools() as tools:
        by_name = {t.tool_name: t for t in tools}
        client = by_name["run_sql"].mcp_client

        def call(name: str, arguments: dict) -> str:
            result = client.call_tool_sync(tool_use_id=f"catalog-{name}", name=name, arguments=arguments)
            if result.get("status") != "success":
                raise RuntimeError(f"{name} failed: {str(result)[:500]}")
            return "\n".join(b.get("text", "") for b in result.get("content") or [])

        mdl = json.loads(call("get_mdl", {}))
        stats: dict[str, dict] = {}
        for model in energy_subset(mdl)["models"]:
            rows = json.loads(call("run_sql", {"sql": stats_sql(model)}))["rows"]
            stats[model["name"]] = parse_stats(model, rows[0])
            for col in low_cardinality_columns(model, stats[model["name"]]):
                values = json.loads(call("run_sql", {"sql": f"SELECT DISTINCT {col} AS v FROM {model['name']}", "limit": 100}))["rows"]
                stats[model["name"]]["columns"][col]["values"] = sorted(str(r["v"]) for r in values if r["v"] is not None)
    catalog = build_catalog(
        mdl,
        yaml.safe_load(TOPICS_FILE.read_text()),
        stats,
        (WREN_DIR / "knowledge" / "rules" / "energy.md").read_text(),
        load_seeds(),
        {question: chip_id for chip_id, _, question in CHIPS},
        recorded_at,
    )
    write_json(DATA_DIR / "model.json", catalog)
    columns = sum(len(m["columns"]) for m in catalog["models"])
    print(f"[PASS] catalog: {len(catalog['topics'])} topics, {len(catalog['cubes'])} cubes, {len(catalog['models'])} models, {columns} columns")
    return catalog


def record_chip(chip_id: str, label: str, question: str, recorded_at: str, window: dict | None) -> dict:
    events: list[dict] = []
    status = "ok"
    try:
        answer_question(question, user_id="demo-record", use_memory=False, domains=("energy",), on_event=events.append)
    except Exception as e:  # noqa: BLE001 — the error event is already in the stream; keep the file for inspection
        status = f"error: {e}"
    replay = {
        "id": chip_id,
        "label": label,
        "question": question,
        "recorded_at": recorded_at,
        "data_window": window,
        "events": events,
    }
    write_json(REPLAY_DIR / f"{chip_id}.json", replay)
    answer = next((e for e in events if e["type"] == "answer"), {})
    charts = sum(e["type"] == "chart" for e in events)
    print(
        f"[{'PASS' if status == 'ok' else 'FAIL'}] {chip_id}: path={answer.get('path', '?')} "
        f"turns={answer.get('turns', '?')} tokens={answer.get('tokens', '?')} charts={charts}"
        + ("" if status == "ok" else f" ({status})")
    )
    return {"id": chip_id, "label": label, "question": question, "recorded_at": recorded_at,
            "path": answer.get("path"), "ok": status == "ok"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="comma-separated chip ids to re-record")
    parser.add_argument("--overview-only", action="store_true")
    parser.add_argument("--catalog-only", action="store_true", help="only the data catalog (no LLM)")
    args = parser.parse_args()
    load_env()

    recorded_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_catalog(recorded_at)
    if args.catalog_only:
        return 0
    overview = record_overview(recorded_at)
    if args.overview_only:
        return 0

    only = {c for c in args.only.split(",") if c}
    unknown = only - {c[0] for c in CHIPS}
    if unknown:
        print(f"unknown chip id(s): {sorted(unknown)}")
        return 2

    index_path = REPLAY_DIR / "index.json"
    previous = {c["id"]: c for c in json.loads(index_path.read_text())["chips"]} if index_path.exists() else {}
    results = []
    for chip_id, label, question in CHIPS:
        if only and chip_id not in only:
            if chip_id in previous:
                results.append(previous[chip_id])
            continue
        results.append(record_chip(chip_id, label, question, recorded_at, overview["window"]))
    write_json(index_path, {"chips": results})
    failed = [r["id"] for r in results if not r.get("ok")]
    print(f"{len(results) - len(failed)}/{len(results)} replays ok → {REPLAY_DIR.relative_to(REPO_ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
