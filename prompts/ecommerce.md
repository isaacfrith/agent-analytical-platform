# Domain: ecommerce

Ecommerce marts (Iceberg `ecommerce` namespace via Trino). Currency AUD. Data covers a 90-day window.

Governed cubes: daily_revenue, product_performance, refunds_cube.

Known collisions: `status` exists on orders (lifecycle: completed|cancelled|pending) and on payments (attempt: succeeded|failed|refunded).

Caveats to surface: orders.created_at carries a ~1.5x weekend lift from the generator — do not present raw daily trends without that caveat.
