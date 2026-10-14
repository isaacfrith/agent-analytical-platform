"""Phase 2 pipeline: landing Parquet → Iceberg tables via PyIceberg REST.

Reads the landing files written by generate_data.py and creates + fills the
six ecommerce tables through the Lakekeeper REST catalog — the same catalog
Trino uses, so both engines immediately see the same tables.

Credential model: NO S3 keys configured here. Table data flows through the
STS-vended credentials Lakekeeper mints for the catalog session (via the
vending role from terraform/iam.tf). If this script works with zero key
configuration, Phase 1's trust design is proven for a second engine.

Type contract with the generator (Arrow → Iceberg): int64→long, int32→int,
string→string, timestamp[us]→timestamp, decimal128(10,2)→decimal(10,2),
decimal128(12,2)→decimal(12,2). append() requires matching field NAMES;
types just need to be promotable.

Partitioning: orders is partitioned by day(created_at) — the one deliberate
physical decision this phase. Partition field IDs start at 1000 by spec
convention. Everything else is unpartitioned.

Reruns resume: on TableAlreadyExistsError the script loads the table and skips
it when it already holds rows. Sound because append() commits atomically
(one snapshot) — a non-empty table is a completed one, an empty table means
a previous run died mid-load and gets appended to.

Run from the repo root (after `make data`):
    uv run python scripts/run_pipeline.py
"""

from __future__ import annotations

import os
import sys

import pyarrow.fs as pa_fs
import pyarrow.parquet as pq
from dotenv import load_dotenv
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import (
    NamespaceAlreadyExistsError,
    TableAlreadyExistsError,
)
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import DayTransform
from pyiceberg.types import (
    DecimalType,
    IcebergType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestampType,
)


def F(field_id: int, name: str, field_type: IcebergType, required: bool = False) -> NestedField:
    return NestedField(field_id, name, field_type, required=required)


SCHEMAS: dict[str, Schema] = {
    "customers": Schema(
        F(1, "customer_id", LongType(), required=True),
        F(2, "first_name", StringType()),
        F(3, "last_name", StringType()),
        F(4, "email", StringType()),
        F(5, "country", StringType()),
        F(6, "city", StringType()),
        F(7, "signup_at", TimestampType()),
        identifier_field_ids=[1],
    ),
    "products": Schema(
        F(1, "product_id", LongType(), required=True),
        F(2, "name", StringType()),
        F(3, "category", StringType()),
        F(4, "unit_price", DecimalType(10, 2)),
        identifier_field_ids=[1],
    ),
    "orders": Schema(
        F(1, "order_id", LongType(), required=True),
        F(2, "customer_id", LongType()),
        F(3, "created_at", TimestampType()),
        F(4, "status", StringType()),
        F(5, "total_amount", DecimalType(12, 2)),
        identifier_field_ids=[1],
    ),
    "order_items": Schema(
        F(1, "order_item_id", LongType(), required=True),
        F(2, "order_id", LongType()),
        F(3, "product_id", LongType()),
        F(4, "quantity", IntegerType()),
        F(5, "unit_price", DecimalType(10, 2)),
        F(6, "line_total", DecimalType(12, 2)),
        identifier_field_ids=[1],
    ),
    "payments": Schema(
        F(1, "payment_id", LongType(), required=True),
        F(2, "order_id", LongType()),
        F(3, "amount", DecimalType(12, 2)),
        F(4, "status", StringType()),
        F(5, "paid_at", TimestampType()),
        identifier_field_ids=[1],
    ),
    "returned_orders": Schema(
        F(1, "return_id", LongType(), required=True),
        F(2, "order_id", LongType()),
        F(3, "reason", StringType()),
        F(4, "returned_at", TimestampType()),
        F(5, "refund_amount", DecimalType(12, 2)),
        identifier_field_ids=[1],
    ),
}

# Only orders is partitioned. source_id 3 = created_at in the orders schema.
PARTITION_SPECS: dict[str, PartitionSpec | None] = {
    "orders": PartitionSpec(
        PartitionField(
            source_id=3, field_id=1000, transform=DayTransform(), name="created_at_day"
        ),
        spec_id=0,
    ),
}

TABLES = (
    "customers",
    "products",
    "orders",
    "order_items",
    "payments",
    "returned_orders",
)


def pass_line(message: str) -> None:
    print(f"[PASS] {message}")


def fail_line(message: str, hint: str | None = None) -> None:
    print(f"[FAIL] {message}")
    if hint:
        print(f"       hint: {hint}")


def main() -> int:
    load_dotenv()

    missing = [
        key
        for key in (
            "AWS_PROFILE",
            "AWS_REGION",
            "AWS_S3_BUCKET",
            "LAKEKEEPER_URI",
            "ICEBERG_WAREHOUSE",
        )
        if not os.environ.get(key)
    ]
    if missing:
        fail_line(f"config: missing {', '.join(missing)} in .env")
        return 1
    bucket = os.environ["AWS_S3_BUCKET"]

    # The warehouse key selects dev_lakehouse on Lakekeeper's side; it becomes
    # the REST prefix (same role iceberg.rest-catalog.warehouse plays for Trino).
    catalog = load_catalog(
        "dev",
        type="rest", uri=f"{os.environ['LAKEKEEPER_URI'].rstrip('/')}/catalog", warehouse=os.environ["ICEBERG_WAREHOUSE"],
    )

    try:
        catalog.create_namespace("ecommerce")
        pass_line("namespace: ecommerce created")
    except NamespaceAlreadyExistsError:
        # Expected: the Phase 1 smoke test created it.
        pass_line("namespace: ecommerce already exists (skipping)")

    s3 = pa_fs.S3FileSystem(region=os.environ["AWS_REGION"])
    for name in TABLES:
        arrow_table = pq.read_table(
            f"{bucket}/landing/{name}/part-0.parquet", filesystem=s3
        )
        try:
            # partition_spec=None crashes PyIceberg's REST client (it
            # dereferences the spec to assign fresh IDs), so unpartitioned
            # tables omit the kwarg entirely instead of passing None.
            if (spec := PARTITION_SPECS.get(name)) is not None:
                table = catalog.create_table(
                    f"ecommerce.{name}", schema=SCHEMAS[name], partition_spec=spec
                )
            else:
                table = catalog.create_table(
                    f"ecommerce.{name}", schema=SCHEMAS[name]
                )
        except TableAlreadyExistsError:
            # Resume, don't fail: append() commits atomically (one snapshot),
            # so a non-empty table is a completed table. An empty one means a
            # previous run died mid-load — fall through and append.
            table = catalog.load_table(f"ecommerce.{name}")
            existing = table.scan().to_arrow().num_rows
            if existing > 0:
                pass_line(
                    f"table: ecommerce.{name} already holds {existing} rows "
                    "(skipping)"
                )
                continue
        table.append(arrow_table)
        # Read-back through the catalog proves the write committed and the
        # vended-credential read path works for this engine too.
        stored = table.scan().to_arrow().num_rows
        status = "PASS" if stored == arrow_table.num_rows else "FAIL"
        print(
            f"[{status}] table: ecommerce.{name} — "
            f"appended {arrow_table.num_rows}, table holds {stored}"
        )
        if stored != arrow_table.num_rows:
            return 1

    print("\nAll tables loaded. Verify in Trino (see the Phase 2 brief).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
