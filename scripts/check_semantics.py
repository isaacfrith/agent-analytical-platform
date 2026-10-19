"""MDL smoke test: validate the semantic layer without the agent.

Deterministic and LLM-free — structure validation, MDL compile, then
bounded execution of every knowledge/sql example, one join probe per
relationship in relationships.yml, and one measure-only probe per cube.

Cube probes deliberately avoid time windows: Wren's own `cube query` CLI
still emits untyped time literals that Trino rejects (TIMESTAMP vs VARCHAR).
The agent's query_cube (src/agent/cube_query.py) types them; the
energy_cube_time_window eval covers that path. The view order_item_details gets its own probe
since product_performance is built on it.

Usage: uv run python scripts/check_semantics.py  (or `make mdl-test`)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repo_config import WREN_DIR, run_wren

SQL_DIR = WREN_DIR / "knowledge" / "sql"

# Mirrors relationships.yml — update together. COUNT(*) probes validate that
# each governed join path compiles through the MDL and executes on Trino.
JOIN_PROBES: list[tuple[str, str]] = [
    (
        "order_items_orders",
        "SELECT COUNT(*) AS n FROM order_items JOIN orders ON order_items.order_id = orders.order_id",
    ),
    (
        "order_items_products",
        "SELECT COUNT(*) AS n FROM order_items JOIN products ON order_items.product_id = products.product_id",
    ),
    (
        "orders_customers",
        "SELECT COUNT(*) AS n FROM orders JOIN customers ON orders.customer_id = customers.customer_id",
    ),
    (
        "payments_orders",
        "SELECT COUNT(*) AS n FROM payments JOIN orders ON payments.order_id = orders.order_id",
    ),
    (
        "returned_orders_orders",
        "SELECT COUNT(*) AS n FROM returned_orders JOIN orders ON returned_orders.order_id = orders.order_id",
    ),
    ("view_order_item_details", "SELECT COUNT(*) AS n FROM order_item_details"),
    (
        "generation_fueltech_groups",
        "SELECT COUNT(*) AS n FROM generation_daily JOIN fueltech_groups ON generation_daily.fueltech = fueltech_groups.fueltech",
    ),
    (
        "facilities_fueltech_groups",
        "SELECT COUNT(*) AS n FROM facilities JOIN fueltech_groups ON facilities.fueltech = fueltech_groups.fueltech",
    ),
    (
        "market_generation",
        "SELECT COUNT(*) AS n FROM market_daily JOIN generation_daily ON market_daily.network_region = generation_daily.network_region AND market_daily.day = generation_daily.day",
    ),
    (
        "view_generation_with_group",
        "SELECT COUNT(*) AS n FROM generation_with_group",
    ),
]

# Measure-only probes (no time dimensions — see module docstring).
CUBE_PROBES: list[tuple[str, list[str]]] = [
    (
        "daily_revenue",
        [
            "cube",
            "query",
            "--cube",
            "daily_revenue",
            "--measures",
            "gross_revenue,order_count",
            "-l",
            "5",
        ],
    ),
    (
        "refunds_cube",
        [
            "cube",
            "query",
            "--cube",
            "refunds_cube",
            "--measures",
            "refund_total,return_count",
            "--dimensions",
            "reason",
            "-l",
            "5",
        ],
    ),
    (
        "product_performance",
        [
            "cube",
            "query",
            "--cube",
            "product_performance",
            "--measures",
            "units_sold,revenue",
            "--dimensions",
            "category",
            "-l",
            "5",
        ],
    ),
    (
        "daily_generation",
        [
            "cube",
            "query",
            "--cube",
            "daily_generation",
            "--measures",
            "energy_total,emissions_total,market_value_total",
            "-l",
            "5",
        ],
    ),
    (
        "daily_price",
        [
            "cube",
            "query",
            "--cube",
            "daily_price",
            "--measures",
            "avg_price,demand_energy_total",
            "-l",
            "5",
        ],
    ),
    (
        "renewable_share",
        [
            "cube",
            "query",
            "--cube",
            "renewable_share",
            "--measures",
            "renewable_energy,total_energy,renewable_share_pct",
            "-l",
            "5",
        ],
    ),
]


def parse_seed(path: Path) -> tuple[str, str] | None:
    """Extract (nl, sql) from a knowledge/sql/*.md frontmatter block."""
    text = path.read_text()
    parts = text.split("---")
    if len(parts) < 3:
        return None
    front, nl, sql, block = parts[1], "", "", False
    for line in front.splitlines():
        if block:
            if line.startswith((" ", "\t")):
                sql += line.strip() + "\n"
                continue
            block = False
        if line.startswith("nl:"):
            nl = line.split(":", 1)[1].strip()
        elif line.startswith("sql:"):
            rest = line.split(":", 1)[1].strip()
            if rest == "|":
                block, sql = True, ""
            else:
                sql = rest
    sql = sql.strip()
    return (nl or path.stem, sql) if sql else None


def main() -> int:
    checks = 0
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print(f"  [{'ok' if ok else 'FAIL'}] {label}", flush=True)
        if not ok:
            failures.append(label)
            if detail:
                print(f"       {detail[:800]}", flush=True)

    print("== validate + build ==", flush=True)
    ok, detail = run_wren(["context", "validate"])
    check("wren context validate", ok, detail)
    if not ok:
        print(f"{checks - len(failures)}/{checks} checks passed.")
        return 1
    ok, detail = run_wren(["context", "build"])
    check("wren context build", ok, detail)
    if not ok:
        print(f"{checks - len(failures)}/{checks} checks passed.")
        return 1

    print("== seed queries (knowledge/sql) ==", flush=True)
    seeds = sorted(SQL_DIR.glob("*.md"))
    if not seeds:
        check("seed files present", False, f"no *.md in {SQL_DIR}")
    for path in seeds:
        parsed = parse_seed(path)
        if parsed is None:
            check(f"seed {path.name}", False, "no sql in frontmatter")
            continue
        nl, sql = parsed
        ok, detail = run_wren(["--limit", "50", "-q", "-o", "csv"], sql=sql)
        check(f"seed {path.name} ({nl[:50]})", ok, detail)

    print("== join probes ==", flush=True)
    for name, sql in JOIN_PROBES:
        ok, detail = run_wren(["-q", "-o", "csv"], sql=sql)
        check(f"join {name}", ok, detail)

    print("== cube probes ==", flush=True)
    for name, args in CUBE_PROBES:
        ok, detail = run_wren([*args, "-o", "csv"])
        check(f"cube {name}", ok, detail)

    passed = checks - len(failures)
    print(f"{passed}/{checks} checks passed.")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
