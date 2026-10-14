---
nl: total refunded amount by return reason
sql: |
  SELECT reason, SUM(refund_amount) AS refund_total, COUNT(*) AS return_count
  FROM returned_orders
  GROUP BY 1
  ORDER BY 2 DESC
source: user
datasource: agent-analytics-trino
---

Seed example: processed refunds only (rows in returned_orders).
