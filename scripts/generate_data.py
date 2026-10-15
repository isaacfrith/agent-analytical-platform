"""Phase 2 data generator: synthetic ecommerce dataset → landing Parquet on S3.

Deterministic (seeded) Faker data mirroring the upstream repo's six-table
shape: customers, products, orders, order_items, payments, returned_orders.

Credential model: landing/ lives OUTSIDE the catalog, so this script talks to
S3 directly. require_env() first — AWS_PROFILE in the environment is then
picked up by pyarrow's S3 client (AWS SDK default chain). Contrast with
run_pipeline.py, which configures no S3 keys at all (vended credentials).

Output: s3://<AWS_S3_BUCKET>/landing/<table>/part-0.parquet — one file per
table is plenty at this scale. Row counts print at the end; reconcile them
against Trino after the pipeline runs.

Run from the repo root:
    uv run python scripts/generate_data.py
"""

from __future__ import annotations

import os
import random
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pyarrow as pa
import pyarrow.fs as pa_fs
import pyarrow.parquet as pq
from faker import Faker

from repo_config import require_env

fake = Faker("en_AU")
Faker.seed(42)
random.seed(42)

N_CUSTOMERS = 5_000
N_PRODUCTS = 150
N_ORDERS = 30_000
WINDOW_DAYS = 90
RETURN_RATE = 0.05

CATEGORIES = [
    "Electronics",
    "Clothing",
    "Home & Kitchen",
    "Books",
    "Sports",
    "Beauty",
    "Toys",
    "Grocery",
]
# Weighted draw: mostly-AU customer base with a long tail.
COUNTRIES = (
    ["Australia"] * 60
    + ["United States"] * 15
    + ["United Kingdom"] * 10
    + ["New Zealand"] * 5
    + ["Canada"] * 5
    + ["Singapore"] * 5
)
RETURN_REASONS = [
    "Changed mind",
    "Defective item",
    "Wrong size",
    "Late delivery",
    "Damaged in transit",
]
PRODUCT_SUFFIXES = ["Pro", "Max", "Lite", "Ultra", "Plus", "Prime"]

SCHEMAS: dict[str, pa.Schema] = {
    "customers": pa.schema(
        [
            ("customer_id", pa.int64()),
            ("first_name", pa.string()),
            ("last_name", pa.string()),
            ("email", pa.string()),
            ("country", pa.string()),
            ("city", pa.string()),
            ("signup_at", pa.timestamp("us")),
        ]
    ),
    "products": pa.schema(
        [
            ("product_id", pa.int64()),
            ("name", pa.string()),
            ("category", pa.string()),
            ("unit_price", pa.decimal128(10, 2)),
        ]
    ),
    "orders": pa.schema(
        [
            ("order_id", pa.int64()),
            ("customer_id", pa.int64()),
            ("created_at", pa.timestamp("us")),
            ("status", pa.string()),
            ("total_amount", pa.decimal128(12, 2)),
        ]
    ),
    "order_items": pa.schema(
        [
            ("order_item_id", pa.int64()),
            ("order_id", pa.int64()),
            ("product_id", pa.int64()),
            ("quantity", pa.int32()),
            ("unit_price", pa.decimal128(10, 2)),
            ("line_total", pa.decimal128(12, 2)),
        ]
    ),
    "payments": pa.schema(
        [
            ("payment_id", pa.int64()),
            ("order_id", pa.int64()),
            ("amount", pa.decimal128(12, 2)),
            ("status", pa.string()),
            ("paid_at", pa.timestamp("us")),
        ]
    ),
    "returned_orders": pa.schema(
        [
            ("return_id", pa.int64()),
            ("order_id", pa.int64()),
            ("reason", pa.string()),
            ("returned_at", pa.timestamp("us")),
            ("refund_amount", pa.decimal128(12, 2)),
        ]
    ),
}

# The generator never emits nulls, so say so: PyIceberg's append() rejects
# nullable Arrow fields for required Iceberg columns (every PK). Landing
# Parquet then carries truthful nullability metadata for all future readers.
STRICT_SCHEMAS: dict[str, pa.Schema] = {
    name: pa.schema([pa.field(f.name, f.type, nullable=False) for f in schema])
    for name, schema in SCHEMAS.items()
}


def slug(text: str) -> str:
    """Lowercase alphanumeric only — safe inside synthetic emails."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def money(low: float, high: float) -> Decimal:
    """AUD amount as Decimal — never a float, so Arrow's decimal cast is exact."""
    return Decimal(str(round(random.uniform(low, high), 2)))


END = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
START = END - timedelta(days=WINDOW_DAYS)


def random_created_at() -> datetime:
    """Random naive timestamp in the 90-day window, with ~1.5x weekend lift."""
    while True:
        day = START + timedelta(days=random.randint(0, WINDOW_DAYS - 1))
        weight = 1.5 if day.weekday() >= 5 else 1.0
        if random.random() < weight / 1.5:
            return day + timedelta(seconds=random.randint(0, 86_399))


def make_customers() -> list[dict]:
    rows = []
    for cid in range(1, N_CUSTOMERS + 1):
        first, last = fake.first_name(), fake.last_name()
        signup = START - timedelta(days=random.randint(0, 365)) + timedelta(
            seconds=random.randint(0, 86_399)
        )
        rows.append(
            {
                "customer_id": cid,
                "first_name": first,
                "last_name": last,
                "email": f"{slug(first)}.{slug(last)}.{cid}@example.com",
                "country": random.choice(COUNTRIES),
                "city": fake.city(),
                "signup_at": signup,
            }
        )
    return rows


def make_products() -> list[dict]:
    rows = []
    for pid in range(1, N_PRODUCTS + 1):
        rows.append(
            {
                "product_id": pid,
                "name": (
                    f"{fake.color_name().title()} {fake.word().title()} "
                    f"{random.choice(PRODUCT_SUFFIXES)}"
                ),
                "category": CATEGORIES[(pid - 1) % len(CATEGORIES)],
                "unit_price": money(5, 500),
            }
        )
    return rows


def make_orders(
    products: list[dict],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Orders + items + payments + returns, with totals reconciled by construction.

    Every order gets exactly one payment (payment_id == order_id), so the
    "one payment per order" invariant holds structurally; Trino re-verifies it.
    """
    prices = {p["product_id"]: p["unit_price"] for p in products}
    orders: list[dict] = []
    items: list[dict] = []
    payments: list[dict] = []
    returns: list[dict] = []
    item_id = 0

    for oid in range(1, N_ORDERS + 1):
        created = random_created_at()
        roll = random.random()
        if roll < 0.97:
            order_status, pay_status = "completed", "succeeded"
        elif roll < 0.99:
            order_status, pay_status = "cancelled", "failed"
        else:
            order_status, pay_status = "pending", "failed"

        total = Decimal("0.00")
        for _ in range(random.randint(1, 4)):
            item_id += 1
            pid = random.randint(1, N_PRODUCTS)
            qty = random.randint(1, 3)
            price = prices[pid]
            line = Decimal(qty) * price
            total += line
            items.append(
                {
                    "order_item_id": item_id,
                    "order_id": oid,
                    "product_id": pid,
                    "quantity": qty,
                    "unit_price": price,
                    "line_total": line,
                }
            )

        orders.append(
            {
                "order_id": oid,
                "customer_id": random.randint(1, N_CUSTOMERS),
                "created_at": created,
                "status": order_status,
                "total_amount": total,
            }
        )
        payments.append(
            {
                "payment_id": oid,
                "order_id": oid,
                "amount": total,
                "status": pay_status,
                "paid_at": created + timedelta(minutes=random.randint(5, 180)),
            }
        )

    completed = [o["order_id"] for o in orders if o["status"] == "completed"]
    payment_by_order = {p["order_id"]: p for p in payments}
    order_by_id = {o["order_id"]: o for o in orders}
    for rid, oid in enumerate(
        random.sample(completed, k=int(len(completed) * RETURN_RATE)), start=1
    ):
        order = order_by_id[oid]
        payment_by_order[oid]["status"] = "refunded"
        returns.append(
            {
                "return_id": rid,
                "order_id": oid,
                "reason": random.choice(RETURN_REASONS),
                "returned_at": order["created_at"]
                + timedelta(days=random.randint(2, 20)),
                "refund_amount": order["total_amount"],
            }
        )
    return orders, items, payments, returns


def main() -> int:
    require_env("AWS_PROFILE", "AWS_REGION", "AWS_S3_BUCKET")
    bucket = os.environ["AWS_S3_BUCKET"]
    # No explicit keys: the AWS SDK default chain picks up AWS_PROFILE from the
    # environment that require_env() just populated.
    s3 = pa_fs.S3FileSystem(region=os.environ["AWS_REGION"])

    customers = make_customers()
    products = make_products()
    orders, items, payments, returns = make_orders(products)

    datasets = {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": items,
        "payments": payments,
        "returned_orders": returns,
    }
    for name, rows in datasets.items():
        table = pa.Table.from_pylist(rows, schema=STRICT_SCHEMAS[name])
        key = f"{bucket}/landing/{name}/part-0.parquet"
        pq.write_table(table, key, filesystem=s3)
        print(f"[PASS] {name}: {table.num_rows} rows → s3://{key}")

    print("\nAll tables written. Next: make pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
