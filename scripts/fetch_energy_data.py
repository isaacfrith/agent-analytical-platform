"""Phase 1 energy pipeline: Open Electricity API → landing Parquet → Iceberg.

Fetches 90 days of daily data (live, ending yesterday) plus the full
facility registry for both networks Open Electricity covers — the NEM (east
coast, five regions) and the WEM (south-west WA, one region) — and loads
four tables into the `energy` namespace: facilities (unit grain),
generation_daily (region × fueltech × day), market_daily (region × day), and
the static fueltech_groups mapping. Every fact row carries `network`; the two
markets are not interconnected. Days are each network's local calendar day
(NEM +10:00, WEM +08:00), as the API reports them.

Single script instead of the generate/load split: the source is one API,
not a generator, so fetch and load share the window and schemas. Split it
if either half outgrows the file.

Usage: uv run python scripts/fetch_energy_data.py  (or `make energy-data`)
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pyarrow as pa
import pyarrow.fs as pa_fs
import pyarrow.parquet as pq
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    DoubleType,
    NestedField,
    StringType,
    TimestampType,
)

from repo_config import pass_line, require_env

WINDOW_DAYS = 90
NETWORKS = ("NEM", "WEM")

# Governed grouping, owned here and mirrored into energy.md in Phase 2.
# Mirrors Open Electricity's fueltech groups; is_renewable is OUR definition
# (wind/solar/hydro/bioenergy generate; batteries and pumps store).
FUELTECH_GROUPS: dict[str, tuple[str, bool]] = {
    "coal_black": ("coal", False),
    "coal_brown": ("coal", False),
    "gas_ccgt": ("gas", False),
    "gas_ocgt": ("gas", False),
    "gas_recip": ("gas", False),
    "gas_steam": ("gas", False),
    "gas_wcmg": ("gas", False),
    "distillate": ("fossil", False),
    "hydro": ("hydro", True),
    "hydro_and_storage": ("hydro", True),
    "wind": ("wind", True),
    "wind_offshore": ("wind", True),
    "solar": ("solar", True),
    "solar_utility": ("solar", True),
    "solar_rooftop": ("solar", True),
    "solar_thermal": ("solar", True),
    "bioenergy_biogas": ("renewable", True),
    "bioenergy_biomass": ("renewable", True),
    "battery": ("battery", False),
    "battery_charging": ("battery", False),
    "battery_discharging": ("battery", False),
    "pumps": ("battery", False),
    "nuclear": ("other", False),
    "aggregator_dr": ("other", False),
    "aggregator_vpp": ("other", False),
    "exports": ("other", False),
    "imports": ("other", False),
    "interconnector": ("other", False),
    "other": ("other", False),
}


def F(field_id: int, name: str, field_type, required: bool = False) -> NestedField:
    return NestedField(field_id, name, field_type, required=required)


SCHEMAS: dict[str, Schema] = {
    "facilities": Schema(
        F(1, "facility_code", StringType(), required=True),
        F(2, "facility_name", StringType()),
        F(3, "network_region", StringType()),
        F(4, "unit_code", StringType(), required=True),
        F(5, "fueltech", StringType()),
        F(6, "status", StringType()),
        F(7, "capacity_registered_mw", DoubleType()),
        F(8, "capacity_maximum_mw", DoubleType()),
        F(9, "capacity_storage_mwh", DoubleType()),
        F(10, "emissions_factor_tco2e_mwh", DoubleType()),
        F(11, "latitude", DoubleType()),
        F(12, "longitude", DoubleType()),
        F(13, "network", StringType(), required=True),
    ),
    "generation_daily": Schema(
        F(1, "day", TimestampType(), required=True),
        F(2, "network_region", StringType(), required=True),
        F(3, "fueltech", StringType(), required=True),
        F(4, "energy_mwh", DoubleType()),
        F(5, "emissions_tco2e", DoubleType()),
        F(6, "market_value_aud", DoubleType()),
        F(7, "network", StringType(), required=True),
    ),
    "market_daily": Schema(
        F(1, "day", TimestampType(), required=True),
        F(2, "network_region", StringType(), required=True),
        F(3, "price_aud_mwh", DoubleType()),
        F(4, "demand_mw", DoubleType()),
        F(5, "demand_energy_mwh", DoubleType()),
        F(6, "network", StringType(), required=True),
    ),
    "fueltech_groups": Schema(
        F(1, "fueltech", StringType(), required=True),
        F(2, "fueltech_group", StringType()),
        F(3, "is_renewable", BooleanType()),
    ),
}

# landing name -> Iceberg table name
TABLES = {
    "energy_facilities": "facilities",
    "energy_generation_daily": "generation_daily",
    "energy_market_daily": "market_daily",
    "energy_fueltech_groups": "fueltech_groups",
}


def naive_utc(dt: datetime) -> datetime:
    """Iceberg TimestampType is tz-naive; days are NEM-local calendar days."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def day_key(dt: datetime) -> datetime:
    d = dt.date()
    return datetime(d.year, d.month, d.day)


def fetch_all() -> dict[str, tuple]:
    """Per network, three API calls: registry, generation facts, market facts.

    Returns {network: (facilities, gen, market)}.

    Facilities go over raw HTTP: the 0.12.0 client rejects the
    'commissioning' status the server actually sends (stale enum), so
    typed parsing fails the whole response. Time-series endpoints parse
    fine through the client.
    """
    import logging

    logging.disable(logging.CRITICAL)
    from openelectricity import OEClient

    end = datetime.now(UTC).date() - timedelta(days=1)
    start = end - timedelta(days=WINDOW_DAYS - 1)
    start_dt = datetime(start.year, start.month, start.day)
    end_dt = datetime(end.year, end.month, end.day) + timedelta(days=1)
    print(f"window: {start} .. {end} ({', '.join(NETWORKS)}, daily)")

    with OEClient() as client:
        return {network: fetch_network(client, network, start_dt, end_dt) for network in NETWORKS}


def fetch_network(client, network: str, start_dt: datetime, end_dt: datetime) -> tuple:
    import requests
    from openelectricity import DataMetric, MarketMetric

    resp = requests.get(
        "https://api.openelectricity.org.au/v4/facilities/",
        params={"network_id": network},
        headers={"Authorization": f"Bearer {os.environ['OPENELECTRICITY_API_KEY']}"},
        timeout=120,
    )
    resp.raise_for_status()
    facilities = resp.json()["data"]
    gen = client.get_network_data(
        network_code=network,
        metrics=[DataMetric.ENERGY, DataMetric.EMISSIONS, DataMetric.MARKET_VALUE],
        interval="1d",
        date_start=start_dt,
        date_end=end_dt,
        primary_grouping="network_region",
        secondary_grouping="fueltech",
    )
    market = client.get_market(
        network_code=network,
        metrics=[
            MarketMetric.PRICE,
            MarketMetric.DEMAND,
            MarketMetric.DEMAND_ENERGY,
        ],
        interval="1d",
        date_start=start_dt,
        date_end=end_dt,
        primary_grouping="network_region",
    )
    return facilities, gen, market


def metric_name(m) -> str:
    """DataMetric/MarketMetric may be str-enums; normalize to plain str."""
    return getattr(m, "value", m)


def flatten_facilities(facilities) -> list[dict]:
    rows = []
    for f in facilities:
        # Site-level point; a handful of sites have none (left null).
        loc = f.get("location") or {}
        for u in f.get("units") or []:
            rows.append(
                {
                    "facility_code": f.get("code"),
                    "facility_name": f.get("name"),
                    "network_region": f.get("network_region"),
                    "unit_code": u.get("code"),
                    "fueltech": u.get("fueltech_id"),
                    "status": u.get("status_id"),
                    "capacity_registered_mw": u.get("capacity_registered"),
                    "capacity_maximum_mw": u.get("capacity_maximum"),
                    "capacity_storage_mwh": u.get("capacity_storage"),
                    "emissions_factor_tco2e_mwh": u.get("emissions_factor_co2"),
                    "latitude": loc.get("lat"),
                    "longitude": loc.get("lng"),
                }
            )
    return rows


def flatten_generation(gen) -> list[dict]:
    cells: dict[tuple, dict] = {}
    for series in gen.data:
        for res in series.results:
            region = res.columns.region
            fueltech = res.columns.fueltech
            for p in res.data:
                point_dt, value = p.root
                key = (day_key(point_dt), region, fueltech)
                cells.setdefault(
                    key,
                    {"day": key[0], "network_region": region, "fueltech": fueltech},
                )
                cells[key][metric_name(series.metric)] = value
    return [
        {
            "day": v["day"],
            "network_region": v["network_region"],
            "fueltech": v["fueltech"],
            "energy_mwh": v.get("energy"),
            "emissions_tco2e": v.get("emissions"),
            "market_value_aud": v.get("market_value"),
        }
        for v in cells.values()
    ]


def flatten_market(market) -> list[dict]:
    cells: dict[tuple, dict] = {}
    for series in market.data:
        for res in series.results:
            region = res.columns.region
            for p in res.data:
                point_dt, value = p.root
                key = (day_key(point_dt), region)
                cells.setdefault(key, {"day": key[0], "network_region": region})
                cells[key][metric_name(series.metric)] = value
    return [
        {
            "day": v["day"],
            "network_region": v["network_region"],
            "price_aud_mwh": v.get("price"),
            "demand_mw": v.get("demand"),
            "demand_energy_mwh": v.get("demand_energy"),
        }
        for v in cells.values()
    ]


def to_arrow(rows: list[dict], schema: Schema) -> pa.Table:
    """Build via pylist with naive-UTC datetimes, then cast to the schema."""
    for row in rows:
        for k, v in row.items():
            if isinstance(v, datetime):
                row[k] = naive_utc(v)
    table = pa.Table.from_pylist(rows)
    return table.cast(pa.schema(schema.as_arrow()))


def main() -> int:
    require_env(
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_S3_BUCKET",
        "LAKEKEEPER_URI",
        "ICEBERG_WAREHOUSE",
        "OPENELECTRICITY_API_KEY",
    )
    bucket = os.environ["AWS_S3_BUCKET"]
    s3 = pa_fs.S3FileSystem(region=os.environ["AWS_REGION"])

    fetched = fetch_all()

    def tagged(flatten, index: int) -> list[dict]:
        return [
            {**row, "network": network}
            for network, parts in fetched.items()
            for row in flatten(parts[index])
        ]

    tables = {
        "energy_facilities": to_arrow(tagged(flatten_facilities, 0), SCHEMAS["facilities"]),
        "energy_generation_daily": to_arrow(
            tagged(flatten_generation, 1), SCHEMAS["generation_daily"]
        ),
        "energy_market_daily": to_arrow(tagged(flatten_market, 2), SCHEMAS["market_daily"]),
        "energy_fueltech_groups": to_arrow(
            [
                {
                    "fueltech": ft,
                    "fueltech_group": grp,
                    "is_renewable": ren,
                }
                for ft, (grp, ren) in FUELTECH_GROUPS.items()
            ],
            SCHEMAS["fueltech_groups"],
        ),
    }
    for name, table in tables.items():
        key = f"{bucket}/landing/{name}/part-0.parquet"
        pq.write_table(table, key, filesystem=s3)
        print(f"[PASS] {name}: {table.num_rows} rows → s3://{key}")

    catalog = load_catalog(
        "dev",
        type="rest",
        uri=f"{os.environ['LAKEKEEPER_URI'].rstrip('/')}/catalog",
        warehouse=os.environ["ICEBERG_WAREHOUSE"],
    )
    try:
        catalog.create_namespace("energy")
        pass_line("namespace: energy created")
    except NamespaceAlreadyExistsError:
        pass_line("namespace: energy already exists (skipping)")

    # Full reload every run (agreed): purge + recreate, never append.
    for landing, table_name in TABLES.items():
        arrow_table = pq.read_table(f"{bucket}/landing/{landing}/part-0.parquet", filesystem=s3)
        try:
            catalog.purge_table(f"energy.{table_name}")
        except NoSuchTableError:
            pass
        table = catalog.create_table(f"energy.{table_name}", schema=SCHEMAS[table_name])
        table.append(arrow_table)
        stored = table.scan().to_arrow().num_rows
        status = "PASS" if stored == arrow_table.num_rows else "FAIL"
        print(
            f"[{status}] table: energy.{table_name} — "
            f"loaded {arrow_table.num_rows}, table holds {stored}"
        )
        if stored != arrow_table.num_rows:
            return 1

    print("\nAll energy tables loaded. Verify in Trino.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
