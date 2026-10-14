# Business definitions (ecommerce-analytics)

## Revenue
- Gross revenue = SUM(orders.total_amount) WHERE status = 'completed'.
- Net revenue = gross revenue − SUM(returned_orders.refund_amount). A return always refunds the full order total.
- Never count total_amount on cancelled/pending orders as revenue.

## Refunds
- A "processed refund" = a row in returned_orders. Its payment flips to status = 'refunded'.
- A failed payment (payments.status = 'failed') is NOT a refund — no money moved.

## Statuses (ambiguous by design — always qualify the table)
- orders.status: completed | cancelled | pending (order lifecycle).
- payments.status: succeeded | failed | refunded (attempt outcome).

## Units and window
- Currency is AUD; amounts are DECIMAL(10,2) / DECIMAL(12,2).
- Data covers a 90-day window ending at generation time (~2026-07-16 to 2026-10-13 for the current load).
- orders.created_at carries a ~1.5x weekend lift from the generator — do not present raw daily trends without that caveat.

## Canonical joins
- orders → customers via customer_id; order_items → orders / products; payments → orders 1:1 on order_id; returned_orders → orders.
- order_items.unit_price is the order-time snapshot; products.unit_price is the current list price. Use the snapshot for historical revenue.
