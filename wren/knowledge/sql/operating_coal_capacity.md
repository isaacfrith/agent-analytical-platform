---
nl: operating coal capacity by state
sql: |
  SELECT network_region, SUM(capacity_registered_mw) AS coal_mw, COUNT(DISTINCT facility_code) AS sites
  FROM facilities
  WHERE status = 'operating' AND fueltech IN ('coal_black', 'coal_brown')
  GROUP BY 1
  ORDER BY 2 DESC
source: user
datasource: agent-analytics-trino
---

Seed example: capacity SUMs registered MW; site counts use DISTINCT facility_code (unit grain).
