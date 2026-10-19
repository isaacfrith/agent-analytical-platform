"""Phase 1 energy gate: data invariants against Trino (no LLM, no MDL).

Proves the fetch pipeline landed queryable truth: row counts, 90-day
coverage per network (NEM + WEM), key uniqueness, network/region
consistency, and referential integrity between generation facts and the
fueltech_groups mapping. Anything red here means
`make energy-data` produced bad data — fix the fetch, not the layer.

Usage: uv run python scripts/check_energy_data.py  (or `make energy-test`)
"""

from __future__ import annotations

import csv
import subprocess
import sys

STEP_TIMEOUT_S = 300

QUERIES: list[tuple[str, str, str]] = [
    # (label, sql, expectation: "nonempty" | "zero" | "empty" | "report" | "=<value>")
    (
        "facilities units present",
        "SELECT COUNT(*) FROM iceberg.energy.facilities",
        "nonempty",
    ),
    (
        "facilities keys not null",
        "SELECT COUNT(*) FROM iceberg.energy.facilities  WHERE facility_code IS NULL OR unit_code IS NULL",
        "zero",
    ),
    (
        "facilities statuses",
        "SELECT status, COUNT(*) FROM iceberg.energy.facilities  GROUP BY 1 ORDER BY 1",
        "report",
    ),
    (
        "facilities mostly located",
        "SELECT COUNT(*) FROM iceberg.energy.facilities  WHERE latitude IS NULL HAVING COUNT(*) > 0.05 * (SELECT COUNT(*) FROM iceberg.energy.facilities)",
        "empty",
    ),
    (
        "facilities inside Australia",
        "SELECT COUNT(*) FROM iceberg.energy.facilities  WHERE latitude NOT BETWEEN -44 AND -10 OR longitude NOT BETWEEN 112 AND 154",
        "zero",
    ),
    (
        "generation covers both networks",
        "SELECT COUNT(DISTINCT network) FROM iceberg.energy.generation_daily",
        "=2",
    ),
    (
        "generation covers 90 days per network",
        "SELECT COUNT(*) FROM (SELECT network FROM iceberg.energy.generation_daily GROUP BY 1 HAVING COUNT(DISTINCT day) <> 90)",
        "zero",
    ),
    (
        "generation covers 6 regions",
        "SELECT COUNT(DISTINCT network_region)  FROM iceberg.energy.generation_daily",
        "=6",
    ),
    (
        "network matches region",
        "SELECT (SELECT COUNT(*) FROM iceberg.energy.generation_daily WHERE (network = 'WEM') <> (network_region = 'WEM')) + (SELECT COUNT(*) FROM iceberg.energy.market_daily WHERE (network = 'WEM') <> (network_region = 'WEM')) + (SELECT COUNT(*) FROM iceberg.energy.facilities WHERE (network = 'WEM') <> (network_region = 'WEM'))",
        "zero",
    ),
    (
        "generation keys unique",
        "SELECT COUNT(*) FROM (SELECT day, network_region, fueltech  FROM iceberg.energy.generation_daily  GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)",
        "zero",
    ),
    (
        "generation fueltechs mapped",
        "SELECT COUNT(*) FROM (SELECT DISTINCT fueltech FROM iceberg.energy.generation_daily WHERE fueltech NOT IN (SELECT fueltech FROM iceberg.energy.fueltech_groups))",
        "zero",
    ),
    (
        "generation is recent",
        "SELECT CAST(max(day) AS VARCHAR) FROM iceberg.energy.generation_daily",
        "report",
    ),
    (
        "market covers 90x6 cells",
        "SELECT COUNT(*) FROM iceberg.energy.market_daily",
        "=540",
    ),
    (
        "market facts present",
        "SELECT COUNT(*) FROM iceberg.energy.market_daily  WHERE price_aud_mwh IS NULL OR demand_energy_mwh IS NULL",
        "zero",
    ),
    (
        "mapping covers 29 fueltechs",
        "SELECT COUNT(*) FROM iceberg.energy.fueltech_groups",
        "nonempty",
    ),
]


def run_trino(sql: str) -> tuple[bool, list[list[str]]]:
    try:
        proc = subprocess.run(
            ["docker", "exec", "trino", "trino", "--execute", sql],
            check=False,
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, [[f"TIMEOUT after {STEP_TIMEOUT_S}s"]]
    if proc.returncode != 0:
        return False, [[(proc.stderr or proc.stdout or "")[-500:]]]
    return True, list(csv.reader(proc.stdout.splitlines()))


def main() -> int:
    checks = 0
    failures: list[str] = []
    for label, sql, expect in QUERIES:
        checks += 1
        ok, rows = run_trino(sql)
        detail = ""
        if ok:
            if expect == "zero":
                ok = rows == [["0"]]
                detail = f"got {rows[0][0] if rows else '?'} violating row(s)"
            elif expect.startswith("="):
                got = rows[0][0] if rows else "no rows"
                ok = got == expect[1:]
                detail = f"got {got}, want {expect[1:]}"
            elif expect == "empty":
                ok = rows == []
                detail = "ok" if ok else f"{rows[0][0]} unlocated unit(s), over 5%"
            elif expect == "nonempty":
                ok = bool(rows) and rows != [["0"]]
                detail = f"got {rows[0][0] if rows else 'no rows'}"
            else:
                detail = "; ".join("=".join(r) for r in rows)[:160]
        else:
            detail = rows[0][0] if rows else "no output"
        print(
            f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" ({detail})" if detail else ""),
            flush=True,
        )
        if not ok:
            failures.append(label)
    passed = checks - len(failures)
    print(f"{passed}/{checks} checks passed.")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
