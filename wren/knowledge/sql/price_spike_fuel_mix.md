---
nl: fuel mix on the highest-price day in the last 30 days
sql: |
  WITH spike AS (
    SELECT day, network_region, price_aud_mwh
    FROM market_daily
    WHERE day >= CURRENT_DATE - INTERVAL '30' DAY
    ORDER BY price_aud_mwh DESC
    LIMIT 1
  )
  SELECT g.fueltech, SUM(g.energy_mwh) AS energy_mwh
  FROM generation_daily AS g
  JOIN spike AS s
    ON g.network_region = s.network_region AND g.day = s.day
  GROUP BY 1
  ORDER BY 2 DESC
source: user
datasource: agent-analytics-trino
---

Seed example: two-part price-then-mix questions go straight to one run_sql
with a CTE — do not chain multiple query_cube calls (cube time filters hit
the known Trino TYPE_MISMATCH and filter syntax wastes turns).
