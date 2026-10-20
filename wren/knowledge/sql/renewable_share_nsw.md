---
nl: renewable share of NSW generation over the last 30 days
sql: |
  SELECT 100.0 * SUM(CASE WHEN is_renewable THEN energy_mwh ELSE 0 END) / NULLIF(SUM(energy_mwh), 0) AS renewable_pct
  FROM generation_with_group
  WHERE network_region = 'NSW1' AND day >= CURRENT_DATE - INTERVAL '30' DAY
source: user
datasource: agent-analytics-trino
---

Seed example: one share for the whole window (a ratio of sums, not an average of daily shares),
via the governed is_renewable flag on the pre-joined view. The renewable_share cube does the same.
