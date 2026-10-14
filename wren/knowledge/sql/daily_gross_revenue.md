---
nl: gross daily revenue for the last 30 days
sql: |
  SELECT DATE_TRUNC('day', created_at) AS day, SUM(total_amount) AS gross_revenue
  FROM orders
  WHERE status = 'completed' AND created_at >= CURRENT_DATE - INTERVAL '30' DAY
  GROUP BY 1
  ORDER BY 1
source: user
datasource: agent-analytics-trino
---

Seed example: completed orders only; amounts reconcile with order_items.line_total.
