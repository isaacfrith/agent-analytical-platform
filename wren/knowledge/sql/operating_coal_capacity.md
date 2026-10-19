---
nl: operating coal capacity by state
sql: |
  SELECT f.network, f.network_region, SUM(f.capacity_registered_mw) AS coal_mw, COUNT(DISTINCT f.facility_code) AS sites
  FROM facilities AS f
  JOIN fueltech_groups AS g ON f.fueltech = g.fueltech
  WHERE f.status = 'operating' AND g.fueltech_group = 'coal'
  GROUP BY 1, 2
  ORDER BY 3 DESC
source: user
datasource: agent-analytics-trino
---

Seed example: capacity SUMs registered MW; site counts use DISTINCT facility_code (unit grain). "Coal" is the governed fueltech_group (black + brown), never a hand-picked fueltech list.
