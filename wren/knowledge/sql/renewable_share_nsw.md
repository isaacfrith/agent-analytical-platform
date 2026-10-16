---
nl: renewable share of NSW generation over the last 30 days
sql: |
  SELECT day, SUM(CASE WHEN is_renewable THEN energy_mwh ELSE 0 END) / SUM(energy_mwh) * 100 AS renewable_pct
  FROM generation_with_group
  WHERE network_region = 'NSW1' AND day >= CURRENT_DATE - INTERVAL '30' DAY
  GROUP BY 1
  ORDER BY 1
source: user
datasource: agent-analytics-trino
---

Seed example: share via the governed is_renewable flag on the pre-joined view.
